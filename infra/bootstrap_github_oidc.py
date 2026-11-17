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
                "Sid": "CognitoCreate",
                "Effect": "Allow",
                # CreateUserPool can't be scoped to a resource ARN -- the pool
                # doesn't exist yet. Everything else above is resource-scoped.
                "Action": ["cognito-idp:CreateUserPool", "cognito-idp:ListUserPools"],
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
