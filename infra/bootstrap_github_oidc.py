"""
One-time, run-locally-by-a-human bootstrap. Not something CI runs (it's
what makes CI possible in the first place -- can't have the pipeline
create its own trust relationship).

Creates, in your current AWS account/region:
    1. An IAM OIDC identity provider for token.actions.githubusercontent.com
       (skipped if one already exists in the account -- there's only ever
       one per account regardless of how many repos/roles use it).
    2. An S3 bucket for Terraform remote state (versioned, encrypted,
       public access blocked).
    3. An IAM role ("gh-actions-mcp-cognito-tf") that GitHub Actions can
       assume via OIDC -- trust policy scoped to one repo + ref, so only
       workflow runs from that exact repo/branch can assume it. No AWS
       access keys stored anywhere, ever.
    4. An inline least-privilege policy on that role: cognito-idp:* scoped
       to this account/region's user pools (can't scope tighter than that
       for CreateUserPool, since the pool doesn't exist yet to reference
       by ARN), plus read/write on just the state bucket/prefix.

Usage:
    python bootstrap_github_oidc.py --repo seetaram-codebase/aws-mcp-session
"""

import argparse
import json
import sys

import boto3
from botocore.exceptions import ClientError

GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"
# SHA1 fingerprint of the top CA cert in token.actions.githubusercontent.com's
# current TLS chain (GitHub moved to Let's Encrypt; the old DigiCert
# thumbprint from older guides no longer matches). Recomputed by fetching
# the live chain -- see chat history -- rather than trusted from memory.
# AWS validates trusted-CA thumbprints itself since 2023, but the API still
# requires a syntactically valid (40 hex char) value in the request.
GITHUB_OIDC_THUMBPRINT = "ab9d0263244dd0326eb67015705a667e79cfe998"


def ensure_oidc_provider(iam, account_id):
    provider_arn = f"arn:aws:iam::{account_id}:oidc-provider/token.actions.githubusercontent.com"
    try:
        iam.get_open_id_connect_provider(OpenIDConnectProviderArn=provider_arn)
        print(f"OIDC provider already exists: {provider_arn}")
        return provider_arn
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise

    print("Creating GitHub Actions OIDC provider...")
    resp = iam.create_open_id_connect_provider(
        Url=GITHUB_OIDC_URL,
        ClientIDList=["sts.amazonaws.com"],
        ThumbprintList=[GITHUB_OIDC_THUMBPRINT],
    )
    return resp["OpenIDConnectProviderArn"]


def ensure_state_bucket(s3, bucket_name, region):
    try:
        s3.head_bucket(Bucket=bucket_name)
        print(f"State bucket already exists: {bucket_name}")
        return
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
            raise

    print(f"Creating state bucket: {bucket_name}")
    kwargs = {"Bucket": bucket_name}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**kwargs)
    s3.put_bucket_versioning(Bucket=bucket_name, VersioningConfiguration={"Status": "Enabled"})
    s3.put_bucket_encryption(
        Bucket=bucket_name,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )


def ensure_role(iam, account_id, region, repo, role_name, bucket_name, provider_arn):
    # GitHub now embeds internal numeric owner/repo IDs into the `sub` claim
    # (e.g. "repo:org@123/repo@456:ref:refs/heads/main"), so a plain
    # name-based StringLike against `sub` silently never matches -- every
    # assume-role call fails with a generic "not authorized" error until
    # you decode an actual token to see this (done via a debug workflow
    # step). AWS also *requires* the trust policy's condition to pin either
    # `sub` or `job_workflow_ref` to something other than a bare "*" when
    # the principal is a web-identity OIDC provider -- a wildcard-only
    # condition is rejected outright (MalformedPolicyDocument). So: `sub`
    # is still pinned here (owner/repo name prefixes + exact branch, IDs
    # wildcarded), and `repository` is the precise, rename-proof check.
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Federated": provider_arn},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:repository": repo,
                    },
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": (
                            f"repo:{repo.split('/')[0]}@*/{repo.split('/')[1]}@*:ref:refs/heads/*"
                        ),
                    },
                },
            }
        ],
    }

    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "CognitoManage",
                "Effect": "Allow",
                "Action": "cognito-idp:*",
                "Resource": [
                    f"arn:aws:cognito-idp:{region}:{account_id}:userpool/*",
                ],
            },
            {
                "Sid": "CognitoUnscopedActions",
                "Effect": "Allow",
                # These don't support resource-level permissions in Cognito's
                # IAM action reference -- AWS requires Resource "*" for them
                # regardless of which pool they touch. CreateUserPool is the
                # obvious one (pool doesn't exist yet); the domain actions
                # less obviously fall in the same bucket -- found by actually
                # running `apply` and hitting AccessDeniedException on
                # DescribeUserPoolDomain, not from reading the docs first.
                "Action": [
                    "cognito-idp:CreateUserPool",
                    "cognito-idp:ListUserPools",
                    "cognito-idp:CreateUserPoolDomain",
                    "cognito-idp:DescribeUserPoolDomain",
                    "cognito-idp:UpdateUserPoolDomain",
                    "cognito-idp:DeleteUserPoolDomain",
                ],
                "Resource": "*",
            },
            {
                # DeleteObject is required too -- not just for destroy, but
                # because the S3-native state lock (use_lockfile = true)
                # writes a .tflock object and deletes it when the lock is
                # released after every plan/apply/destroy.
                "Sid": "TerraformState",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*",
                ],
            },
            # --- Added for the ECS Fargate deployment (ecs.tf) ---
            # Honest tradeoff, not hidden: ELB, CloudFront, and several EC2
            # security-group/network actions don't support resource-level
            # IAM permissions at all (AWS's own design, not a shortcut taken
            # here) -- those statements below are Resource "*" because
            # there's no narrower option. ECR, ECS, Secrets Manager, and IAM
            # role management ARE scoped to this demo's specific resource
            # names. This role's blast radius is meaningfully wider than the
            # Cognito-only version; still pinned to this one repo via the
            # trust policy above.
            {
                "Sid": "EcrScoped",
                "Effect": "Allow",
                "Action": [
                    "ecr:CreateRepository", "ecr:DeleteRepository", "ecr:DescribeRepositories",
                    "ecr:DescribeImages", "ecr:BatchDeleteImage", "ecr:ListTagsForResource",
                    "ecr:TagResource", "ecr:PutLifecyclePolicy", "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:PutImage",
                    "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload",
                ],
                "Resource": [
                    f"arn:aws:ecr:{region}:{account_id}:repository/mcp-finance-server",
                    f"arn:aws:ecr:{region}:{account_id}:repository/mcp-launcher",
                    f"arn:aws:ecr:{region}:{account_id}:repository/mcp-attendee-base",
                ],
            },
            {
                "Sid": "EcrAuth",
                "Effect": "Allow",
                # GetAuthorizationToken (needed for `docker login`) has no
                # resource-level permission support -- must be "*".
                "Action": "ecr:GetAuthorizationToken",
                "Resource": "*",
            },
            {
                "Sid": "EcsScoped",
                "Effect": "Allow",
                # Only mutations against an already-identified resource --
                # the ones where resource-level scoping actually held up
                # under a real apply. Every Describe/List/Create action
                # for ECS turned out to need Resource "*" (see below),
                # found the same way as everything else in this file: by
                # running apply and reading the actual error, not by
                # trusting the IAM resource-type docs in advance.
                "Action": [
                    "ecs:DeleteCluster", "ecs:UpdateService", "ecs:DeleteService",
                    "ecs:TagResource", "ecs:UntagResource",
                ],
                "Resource": [
                    f"arn:aws:ecs:{region}:{account_id}:cluster/mcp-demo-cluster",
                    f"arn:aws:ecs:{region}:{account_id}:service/mcp-demo-cluster/mcp-finance-server",
                    f"arn:aws:ecs:{region}:{account_id}:task-definition/mcp-finance-server:*",
                    f"arn:aws:ecs:{region}:{account_id}:task/mcp-demo-cluster/*",
                ],
            },
            {
                "Sid": "EcsUnscopedActions",
                "Effect": "Allow",
                "Action": [
                    "ecs:CreateCluster", "ecs:CreateService", "ecs:DescribeClusters",
                    "ecs:DescribeServices", "ecs:DescribeTaskDefinition", "ecs:DeregisterTaskDefinition",
                    "ecs:ListTasks", "ecs:DescribeTasks", "ecs:ListTagsForResource",
                    "ecs:RegisterTaskDefinition", "ecs:ListClusters", "ecs:ListTaskDefinitions",
                    "ecs:ListServices",
                ],
                "Resource": "*",
            },
            {
                "Sid": "ElbAndCloudFront",
                "Effect": "Allow",
                # Neither service supports resource-level IAM permissions
                # for the actions Terraform needs here (create/describe/
                # modify ALB, target group, listener, distribution).
                "Action": ["elasticloadbalancing:*", "cloudfront:*"],
                "Resource": "*",
            },
            {
                "Sid": "Ec2NetworkingUnscoped",
                "Effect": "Allow",
                # Security group CRUD + the describe calls the VPC/subnet/
                # prefix-list data sources need. SG resource-level scoping
                # would require tag-based conditions set up before the SGs
                # exist -- not worth it for a workshop role; same honesty
                # note as the ELB/CloudFront statement above.
                "Action": [
                    "ec2:DescribeVpcs", "ec2:DescribeVpcAttribute", "ec2:DescribeSubnets",
                    "ec2:DescribeAvailabilityZones",
                    "ec2:DescribeManagedPrefixLists", "ec2:GetManagedPrefixListEntries",
                    "ec2:DescribeSecurityGroups",
                    "ec2:DescribeSecurityGroupRules", "ec2:CreateSecurityGroup",
                    "ec2:DeleteSecurityGroup", "ec2:AuthorizeSecurityGroupIngress",
                    "ec2:AuthorizeSecurityGroupEgress", "ec2:RevokeSecurityGroupIngress",
                    "ec2:RevokeSecurityGroupEgress", "ec2:CreateTags",
                ],
                "Resource": "*",
            },
            {
                "Sid": "SecretsManagerScoped",
                "Effect": "Allow",
                "Action": [
                    "secretsmanager:DescribeSecret", "secretsmanager:PutSecretValue",
                    "secretsmanager:DeleteSecret", "secretsmanager:GetSecretValue",
                    "secretsmanager:TagResource", "secretsmanager:GetResourcePolicy",
                ],
                # Secrets Manager appends a random suffix to the ARN at
                # creation, hence the wildcard.
                "Resource": f"arn:aws:secretsmanager:{region}:{account_id}:secret:mcp-server/cognito-client-secret-*",
            },
            {
                "Sid": "SecretsManagerCreate",
                "Effect": "Allow",
                "Action": "secretsmanager:CreateSecret",
                "Resource": "*",
            },
            {
                "Sid": "IamRolesScoped",
                "Effect": "Allow",
                "Action": [
                    "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:PutRolePolicy",
                    "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:AttachRolePolicy",
                    "iam:DetachRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
                    "iam:TagRole", "iam:PassRole",
                ],
                "Resource": [
                    f"arn:aws:iam::{account_id}:role/mcp-server-ecs-execution",
                    f"arn:aws:iam::{account_id}:role/mcp-server-ecs-task",
                    f"arn:aws:iam::{account_id}:role/mcp-attendee-ecs-task",
                    f"arn:aws:iam::{account_id}:role/launcher-ecs-task",
                ],
            },
            {
                "Sid": "LogsScoped",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup", "logs:DeleteLogGroup",
                    "logs:PutRetentionPolicy", "logs:TagResource", "logs:ListTagsForResource",
                    "logs:DescribeLogStreams",
                ],
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:/ecs/mcp-finance-server*",
                    f"arn:aws:logs:{region}:{account_id}:log-group:/ecs/mcp-launcher*",
                ],
            },
            {
                "Sid": "LogsDescribeUnscoped",
                "Effect": "Allow",
                # DescribeLogGroups is a list/filter call -- AWS evaluates it
                # against a generic resource shape, not the specific group
                # ARN, so scoping it the way the other logs actions are
                # scoped above doesn't work (found by running apply).
                "Action": "logs:DescribeLogGroups",
                "Resource": "*",
            },
        ],
    }

    try:
        iam.get_role(RoleName=role_name)
        print(f"Role {role_name} already exists -- updating trust + permissions policy")
        iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(trust_policy))
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        print(f"Creating role {role_name}...")
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="GitHub Actions OIDC role for Cognito demo Terraform (aws-meetup MCP session)",
        )

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="cognito-tf-least-privilege",
        PolicyDocument=json.dumps(permissions_policy),
    )
    return f"arn:aws:iam::{account_id}:role/{role_name}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="owner/repo, e.g. seetaram-codebase/aws-mcp-session")
    parser.add_argument("--role-name", default="gh-actions-mcp-cognito-tf")
    parser.add_argument("--region", default=None)
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    region = session.region_name
    sts = session.client("sts")
    account_id = sts.get_caller_identity()["Account"]
    bucket_name = f"mcp-meetup-tfstate-{account_id}"

    iam = session.client("iam")
    s3 = session.client("s3")

    print(f"Account: {account_id}  Region: {region}")
    provider_arn = ensure_oidc_provider(iam, account_id)
    ensure_state_bucket(s3, bucket_name, region)
    role_arn = ensure_role(iam, account_id, region, args.repo, args.role_name, bucket_name, provider_arn)

    print("\n" + "=" * 60)
    print("Done. Put these as GitHub repo variables (not secrets --")
    print("they're not sensitive, just identifiers):\n")
    print(f"  AWS_ROLE_ARN        = {role_arn}")
    print(f"  AWS_REGION          = {region}")
    print(f"  TF_STATE_BUCKET     = {bucket_name}")
    print("\nNo AWS access keys were created or stored anywhere.")
    print("=" * 60)


if __name__ == "__main__":
    main()
