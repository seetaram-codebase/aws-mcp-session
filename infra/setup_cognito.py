"""
Provisions the single, shared AWS Cognito User Pool that acts as the OAuth
2.1 Authorization Server for the MCP demo (session-outline section 05,
SEP-990 enterprise-auth path). This is the boto3 equivalent of
infra/terraform/main.tf -- same resource shape, for anyone who'd rather
provision locally without touching Terraform state.

One pool backs both server/finance_mcp_server.py (speaker's stage demo,
via a dedicated admin-created login) and kickstart/server_template.py
(what attendees run themselves in the live hands-on segment, via
self-signup). Creates:
    1. A Cognito User Pool ("mcp-workshop-pool"), self-service sign-up
       left open
    2. A Hosted UI domain (prefix "mcp-workshop-pool-<random>")
    3. A resource server with a custom scope "mcp/access"
    4. An app client wired for authorization-code + PKCE, with both
       http://localhost and http://127.0.0.1 callback variants on port
       8000 (every attendee runs the template on their own laptop
       against this one shared app client)
    5. One admin-created test user for the speaker's own login (everyone
       else clicks "Sign up" on Hosted UI themselves)

Nothing else in your account is touched. Run teardown_cognito.py afterwards
to remove everything this script creates.

Usage:
    python setup_cognito.py
"""

import json
import secrets
import sys
import argparse

import boto3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default=None, help="Defaults to your AWS CLI/session region")
    parser.add_argument(
        "--test-user-email", default="demo@example.com",
        help="Email for the speaker's own admin-created test login",
    )
    parser.add_argument(
        "--test-user-password",
        default=None,
        help="Password for the test user. If omitted, a random one is generated and printed.",
    )
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    region = session.region_name
    if not region:
        print("No AWS region configured. Pass --region or set AWS_DEFAULT_REGION.", file=sys.stderr)
        sys.exit(1)

    cognito = session.client("cognito-idp")

    pool_name = "mcp-workshop-pool"
    resource_id = "mcp"
    scope_name = "access"
    scope_desc = "Access MCP workshop tools"
    full_scope = f"{resource_id}/{scope_name}"
    client_name = "mcp-workshop-client"

    print(f"Region: {region}")
    print("1/5 Creating user pool...")
    pool = cognito.create_user_pool(
        PoolName=pool_name,
        AutoVerifiedAttributes=["email"],
        UsernameAttributes=["email"],
        Policies={"PasswordPolicy": {"MinimumLength": 8, "RequireUppercase": False,
                                      "RequireLowercase": False, "RequireNumbers": False,
                                      "RequireSymbols": False}},
        # Explicit, not just relying on the default: self-service sign-up
        # must stay open so attendees can create their own Hosted UI login.
        AdminCreateUserConfig={"AllowAdminCreateUserOnly": False},
    )
    user_pool_id = pool["UserPool"]["Id"]
    print(f"    UserPoolId = {user_pool_id}")

    print("2/5 Creating Hosted UI domain...")
    domain_prefix = f"{pool_name}-{secrets.token_hex(4)}"
    cognito.create_user_pool_domain(Domain=domain_prefix, UserPoolId=user_pool_id)
    hosted_ui_domain = f"{domain_prefix}.auth.{region}.amazoncognito.com"
    print(f"    Domain = {hosted_ui_domain}")

    print(f"3/5 Creating resource server with custom scope {full_scope}...")
    cognito.create_resource_server(
        UserPoolId=user_pool_id,
        Identifier=resource_id,
        Name="MCP Workshop API",
        Scopes=[{"ScopeName": scope_name, "ScopeDescription": scope_desc}],
    )

    print("4/5 Creating app client...")
    client = cognito.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=client_name,
        GenerateSecret=True,
        AllowedOAuthFlows=["code"],
        AllowedOAuthFlowsUserPoolClient=True,
        AllowedOAuthScopes=["openid", full_scope],
        CallbackURLs=[
            "http://localhost:8000/auth/callback",
            "http://127.0.0.1:8000/auth/callback",
        ],
        SupportedIdentityProviders=["COGNITO"],
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    )
    client_id = client["UserPoolClient"]["ClientId"]
    client_secret = client["UserPoolClient"]["ClientSecret"]
    print(f"    ClientId = {client_id}")

    print("5/5 Creating speaker's test user...")
    password = args.test_user_password or secrets.token_urlsafe(12)
    cognito.admin_create_user(
        UserPoolId=user_pool_id,
        Username=args.test_user_email,
        UserAttributes=[
            {"Name": "email", "Value": args.test_user_email},
            {"Name": "email_verified", "Value": "true"},
        ],
        MessageAction="SUPPRESS",
        TemporaryPassword=password,
    )
    cognito.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username=args.test_user_email,
        Password=password,
        Permanent=True,
    )

    env = {
        "COGNITO_USER_POOL_ID": user_pool_id,
        "COGNITO_CLIENT_ID": client_id,
        "COGNITO_CLIENT_SECRET": client_secret,
        "AWS_REGION": region,
        "MCP_BASE_URL": "http://localhost:8000",
    }

    print("\n" + "=" * 60)
    print("Done. Put these on a slide / handout for attendees (they paste")
    print("them into kickstart/.env) and into server/.env for your own demo:\n")
    for k, v in env.items():
        print(f"  {k}={v}")
    print("\nEveryone shares this one app client -- that's a workshop-only")
    print("shortcut (see kickstart/README.md's note on Dynamic Client")
    print("Registration); each attendee still signs up their OWN Hosted UI")
    print("login. Yours is already created:")
    print(f"  email:    {args.test_user_email}")
    print(f"  password: {password}")
    print("\nSave this output -- the client secret is not retrievable again")
    print("without calling describe_user_pool_client. Teardown with:")
    print(f"  python teardown_cognito.py --user-pool-id {user_pool_id} --domain {domain_prefix}")
    print("=" * 60)

    with open("cognito_env.json", "w") as f:
        json.dump({**env, "domain_prefix": domain_prefix,
                    "test_user_email": args.test_user_email,
                    "test_user_password": password}, f, indent=2)
    print("\nAlso wrote cognito_env.json (gitignore this -- it has the client secret).")


if __name__ == "__main__":
    main()
