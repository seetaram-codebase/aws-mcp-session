"""
MCP Builder "Launcher" -- the one tool that turns an attendee's submitted
tool code into a brand new, dedicated, live MCP server.

OAuth-protected by the SAME shared Cognito pool as kickstart/server_template.py
and server/finance_mcp_server.py -- only someone who has already completed
the normal Hosted UI login can call submit_server. This is the thing that
closes off the "anyone on the internet" risk a public-GitHub-PR pipeline
would have had: the attacker surface here is "anyone with a Cognito login in
this workshop's pool", not "anyone on GitHub".

What submit_server actually does, with no Terraform/GitHub Actions involved:
  1. AST-validates the submitted code (same allowlist as
     launcher/attendee_base/server.py's boot-time check -- this is the
     first of two independent checks, not the only one)
  2. Registers a new ECS task definition that reuses the ONE shared
     attendee_base Docker image (no image build per attendee) with the
     validated code injected via the ATTENDEE_TOOL_CODE env var
  3. Creates a target group + ALB listener rule (Host-header matched) on
     the EXISTING shared ALB from infra/terraform/ecs.tf
  4. Creates a CloudFront distribution -- gives this attendee their own
     free, unique HTTPS domain (required: Cognito's callback URL list is
     exact-match, no wildcards, so every attendee needs a distinct domain)
  5. Appends that domain's callback URL to the shared Cognito app client
  6. Creates the ECS service and waits for it to go steady-state
  7. Returns the new server's public URL

All the ARNs/ids this needs (cluster, listener, subnets, security group,
roles, image URI, secret ARN) come from environment variables populated by
Terraform (infra/terraform/launcher.tf) -- nothing here is hardcoded.
"""

import ast
import os
import re

import boto3
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.providers.aws import AWSCognitoProvider

load_dotenv()

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
CLUSTER_NAME = os.environ["ECS_CLUSTER_NAME"]
ATTENDEE_IMAGE_URI = os.environ["ATTENDEE_IMAGE_URI"]
ATTENDEE_TASK_ROLE_ARN = os.environ["ATTENDEE_TASK_ROLE_ARN"]
ECS_EXECUTION_ROLE_ARN = os.environ["ECS_EXECUTION_ROLE_ARN"]
COGNITO_CLIENT_SECRET_ARN = os.environ["COGNITO_CLIENT_SECRET_ARN"]
ALB_LISTENER_ARN = os.environ["ALB_LISTENER_ARN"]
ALB_DNS_NAME = os.environ["ALB_DNS_NAME"]
SUBNET_IDS = os.environ["SUBNET_IDS"].split(",")
SECURITY_GROUP_ID = os.environ["ECS_TASKS_SECURITY_GROUP_ID"]
VPC_ID = os.environ["VPC_ID"]
COGNITO_USER_POOL_ID = os.environ["COGNITO_USER_POOL_ID"]
COGNITO_CLIENT_ID = os.environ["COGNITO_CLIENT_ID"]
MAX_ATTENDEE_SERVERS = int(os.environ.get("MAX_ATTENDEE_SERVERS", "20"))

ecs = boto3.client("ecs", region_name=AWS_REGION)
elbv2 = boto3.client("elbv2", region_name=AWS_REGION)
cloudfront = boto3.client("cloudfront", region_name=AWS_REGION)
cognito_idp = boto3.client("cognito-idp", region_name=AWS_REGION)
logs = boto3.client("logs", region_name=AWS_REGION)

# Kept in sync manually with launcher/attendee_base/server.py's validate() --
# this is the FIRST of two independent checks; the base image re-checks at
# boot regardless of what happens here.
ALLOWED_IMPORTS = {"random", "math", "datetime", "json", "re"}
DISALLOWED_CALLS = {"open", "exec", "eval", "__import__", "compile", "input"}


def validate(code: str) -> None:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [n.name for n in node.names]
            if not set(names).issubset(ALLOWED_IMPORTS):
                raise ValueError(f"disallowed import: {names}")
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in DISALLOWED_CALLS:
            raise ValueError(f"disallowed call: {node.func.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError(f"disallowed dunder access: {node.attr}")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if not slug:
        raise ValueError("attendee_name must contain at least one letter or digit")
    return slug[:24]


def _unique_slug(base_slug: str) -> str:
    arns = ecs.list_services(cluster=CLUSTER_NAME, maxResults=100)["serviceArns"]
    if arns:
        described = ecs.describe_services(cluster=CLUSTER_NAME, services=arns)["services"]
        existing_names = {s["serviceName"] for s in described}
    else:
        existing_names = set()

    slug = base_slug
    suffix = 1
    while f"mcp-attendee-{slug}" in existing_names:
        suffix += 1
        slug = f"{base_slug}-{suffix}"
    return slug


def _next_priority() -> int:
    rules = elbv2.describe_rules(ListenerArn=ALB_LISTENER_ARN)["Rules"]
    used = {int(r["Priority"]) for r in rules if r["Priority"] != "default"}
    candidate = 100
    while candidate in used:
        candidate += 1
    return candidate


def _count_active_attendee_services() -> int:
    arns = ecs.list_services(cluster=CLUSTER_NAME, maxResults=100)["serviceArns"]
    if not arns:
        return 0
    described = ecs.describe_services(cluster=CLUSTER_NAME, services=arns)["services"]
    return sum(1 for s in described if s["serviceName"].startswith("mcp-attendee-"))


auth = AWSCognitoProvider(
    user_pool_id=COGNITO_USER_POOL_ID,
    client_id=COGNITO_CLIENT_ID,
    client_secret=os.environ["COGNITO_CLIENT_SECRET"],
    aws_region=AWS_REGION,
    base_url=os.environ["MCP_BASE_URL"],
    required_scopes=["openid", "mcp/access"],
)

mcp = FastMCP("MCP Builder", auth=auth)


@mcp.tool
def submit_server(attendee_name: str, tool_code: str) -> dict:
    """Validate tool code and stand up a brand new, dedicated MCP server
    running it. Returns the new server's public HTTPS URL once it's live.
    Takes a couple of minutes (CloudFront distribution propagation) -- this
    is not instant.

    tool_code must be the source of exactly one plain, top-level Python
    function (not a class, not multiple functions) with a type-annotated
    signature and a docstring -- the docstring becomes the tool's
    description. Only these imports are allowed: random, math, datetime,
    json, re. No file, network, subprocess, or dunder/builtin-introspection
    access -- code using anything else is rejected before any deployment
    happens. Example:

    def roll_dice(sides: int = 6) -> dict:
        \"\"\"Roll a die with the given number of sides.\"\"\"
        import random
        return {"result": random.randint(1, sides)}
    """

    validate(tool_code)

    if _count_active_attendee_services() >= MAX_ATTENDEE_SERVERS:
        raise ValueError(
            f"at capacity ({MAX_ATTENDEE_SERVERS} active attendee servers) -- "
            "ask the speaker to free up a slot before submitting"
        )

    slug = _unique_slug(_slugify(attendee_name))
    name = f"mcp-attendee-{slug}"

    log_group = f"/ecs/{name}"
    logs.create_log_group(logGroupName=log_group)
    logs.put_retention_policy(logGroupName=log_group, retentionInDays=1)

    # --- target group -----------------------------------------------------
    tg = elbv2.create_target_group(
        Name=name[:32],
        Port=8000,
        Protocol="HTTP",
        VpcId=VPC_ID,
        TargetType="ip",
        HealthCheckPath="/mcp",
        Matcher={"HttpCode": "200,401"},
    )["TargetGroups"][0]["TargetGroupArn"]

    # --- CloudFront distribution (its own free *.cloudfront.net domain) ---
    # Origin points at the SAME shared ALB every other server uses; routing
    # between attendees happens via Host-header ALB rules below, not here.
    dist = cloudfront.create_distribution(
        DistributionConfig={
            "CallerReference": name,
            "Comment": f"attendee mcp server: {slug}",
            "Enabled": True,
            "Origins": {
                "Quantity": 1,
                "Items": [{
                    "Id": "alb",
                    "DomainName": ALB_DNS_NAME,
                    "CustomOriginConfig": {
                        "HTTPPort": 80,
                        "HTTPSPort": 443,
                        "OriginProtocolPolicy": "http-only",
                        "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
                    },
                }],
            },
            "DefaultCacheBehavior": {
                "TargetOriginId": "alb",
                "ViewerProtocolPolicy": "redirect-to-https",
                "AllowedMethods": {
                    "Quantity": 7,
                    "Items": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
                    "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
                },
                "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",  # Managed-CachingDisabled
                "OriginRequestPolicyId": "216adef6-5c7f-47e4-b989-5492eafa07d3",  # Managed-AllViewer
            },
        }
    )["Distribution"]
    cf_domain = dist["DomainName"]
    public_url = f"https://{cf_domain}"

    # --- ALB listener rule, matched on this distribution's Host header ----
    elbv2.create_rule(
        ListenerArn=ALB_LISTENER_ARN,
        Priority=_next_priority(),
        Conditions=[{"Field": "host-header", "HostHeaderConfig": {"Values": [cf_domain]}}],
        Actions=[{"Type": "forward", "TargetGroupArn": tg}],
    )

    # --- Cognito: allow this new domain to receive the OAuth redirect -----
    client = cognito_idp.describe_user_pool_client(
        UserPoolId=COGNITO_USER_POOL_ID, ClientId=COGNITO_CLIENT_ID
    )["UserPoolClient"]
    callback_urls = list(dict.fromkeys(client["CallbackURLs"] + [f"{public_url}/auth/callback"]))
    cognito_idp.update_user_pool_client(
        UserPoolId=COGNITO_USER_POOL_ID,
        ClientId=COGNITO_CLIENT_ID,
        CallbackURLs=callback_urls,
        AllowedOAuthFlows=client["AllowedOAuthFlows"],
        AllowedOAuthFlowsUserPoolClient=client["AllowedOAuthFlowsUserPoolClient"],
        AllowedOAuthScopes=client["AllowedOAuthScopes"],
        SupportedIdentityProviders=client["SupportedIdentityProviders"],
        ExplicitAuthFlows=client["ExplicitAuthFlows"],
    )

    # --- task definition + service -----------------------------------------
    task_def = ecs.register_task_definition(
        family=name,
        requiresCompatibilities=["FARGATE"],
        networkMode="awsvpc",
        cpu="256",
        memory="512",
        executionRoleArn=ECS_EXECUTION_ROLE_ARN,
        taskRoleArn=ATTENDEE_TASK_ROLE_ARN,
        containerDefinitions=[{
            "name": name,
            "image": ATTENDEE_IMAGE_URI,
            "essential": True,
            "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
            "environment": [
                {"name": "COGNITO_USER_POOL_ID", "value": COGNITO_USER_POOL_ID},
                {"name": "COGNITO_CLIENT_ID", "value": COGNITO_CLIENT_ID},
                {"name": "AWS_REGION", "value": AWS_REGION},
                {"name": "MCP_BASE_URL", "value": public_url},
                {"name": "PORT", "value": "8000"},
                {"name": "ATTENDEE_TOOL_CODE", "value": tool_code},
            ],
            "secrets": [{"name": "COGNITO_CLIENT_SECRET", "valueFrom": COGNITO_CLIENT_SECRET_ARN}],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": log_group,
                    "awslogs-region": AWS_REGION,
                    "awslogs-stream-prefix": name,
                },
            },
        }],
    )["taskDefinition"]["taskDefinitionArn"]

    ecs.create_service(
        cluster=CLUSTER_NAME,
        serviceName=name,
        taskDefinition=task_def,
        desiredCount=1,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": SUBNET_IDS,
                "securityGroups": [SECURITY_GROUP_ID],
                "assignPublicIp": "ENABLED",
            }
        },
        loadBalancers=[{"targetGroupArn": tg, "containerName": name, "containerPort": 8000}],
    )

    # Wait for the ECS service to settle so the URL we hand back is actually
    # live, not "accepted but still booting". CloudFront propagation itself
    # can take a couple more minutes beyond this -- flagged to the caller.
    waiter = ecs.get_waiter("services_stable")
    try:
        waiter.wait(cluster=CLUSTER_NAME, services=[name], WaiterConfig={"Delay": 10, "MaxAttempts": 18})
    except Exception:
        pass  # Return the URL regardless -- caller can check status themselves.

    return {
        "status": "deployed",
        "attendee": slug,
        "url": public_url,
        "note": "ECS service is up; CloudFront may take a couple more minutes to finish propagating globally.",
    }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
