"""
Provisions the AWS Cognito User Pool that acts as the OAuth 2.1 Authorization
Server for the MCP demo (session-outline section 05, SEP-990 enterprise-auth
path).

Two modes:

  Solo demo (default) -- for your own stage walkthrough with
  finance_mcp_server.py. Creates:
    1. A Cognito User Pool ("mcp-demo-finance-pool")
    2. A Hosted UI domain (prefix "mcp-demo-finance-<random>")
    3. A resource server with a custom scope "finance/read"
    4. An app client wired for authorization-code + PKCE
    5. One test user (demo@example.com) for the Hosted UI login

  Workshop mode (--workshop) -- one shared pool for every attendee's own
  kickstart/server_template.py. Creates the same pool/domain/client shape,
  but:
    - Self-service sign-up is left open, so each attendee creates their own
      Hosted UI login on the spot (no AWS account or admin step needed).
    - No single test user is created.
    - The resource server scope is generic ("mcp/access") instead of
      finance-specific, since attendees build arbitrary tools.
    - Both http://localhost and http://127.0.0.1 callback variants are
      registered on port 8000, since every attendee runs the template on
      their own laptop against the same shared app client.

Nothing else in your account is touched. Run teardown_cognito.py afterwards
to remove everything this script creates.

Usage:
    python setup_cognito.py --callback-url http://localhost:8000/auth/callback
    python setup_cognito.py --workshop
"""

import argparse
import json
import secrets
import sys

import boto3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workshop",
        action="store_true",
        help="Provision the shared, self-signup pool for the live kickstart segment instead of the solo demo pool.",
    )
    parser.add_argument(
        "--callback-url",
        default="http://localhost:8000/auth/callback",
        help="Must match MCP_BASE_URL + the server's redirect_path (default /auth/callback). "
             "Ignored in --workshop mode, which always registers the localhost:8000 variants.",
    )
    parser.add_argument("--region", default=None, help="Defaults to your AWS CLI/session region")
    parser.add_argument(
        "--test-user-email", default="demo@example.com",
        help="Email for the Hosted UI test login (solo demo mode only)",
    )
    parser.add_argument(
        "--test-user-password",
        default=None,
        help="Password for the test user (solo demo mode only). If omitted, a random one is generated and printed.",
    )
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    region = session.region_name
    if not region:
        print("No AWS region configured. Pass --region or set AWS_DEFAULT_REGION.", file=sys.stderr)
        sys.exit(1)

    cognito = session.client("cognito-idp")

    pool_name = "mcp-workshop-pool" if args.workshop else "mcp-demo-finance-pool"
    resource_id = "mcp" if args.workshop else "finance"
    scope_name = "access" if args.workshop else "read"
    scope_desc = "Access MCP workshop tools" if args.workshop else "Read portfolio and quote data"
    full_scope = f"{resource_id}/{scope_name}"
    client_name = "mcp-workshop-client" if args.workshop else "mcp-demo-finance-client"

    print(f"Mode: {'workshop (shared, self-signup)' if args.workshop else 'solo demo'}")
    print(f"Region: {region}")
    print("1/5 Creating user pool...")
    create_kwargs = dict(
        PoolName=pool_name,
        AutoVerifiedAttributes=["email"],
        UsernameAttributes=["email"],
        Policies={"PasswordPolicy": {"MinimumLength": 8, "RequireUppercase": False,
                                      "RequireLowercase": False, "RequireNumbers": False,
                                      "RequireSymbols": False}},
    )
    if args.workshop:
        # Explicit, not just relying on the default: self-service sign-up must
        # stay open so attendees can create their own login from Hosted UI.
        create_kwargs["AdminCreateUserConfig"] = {"AllowAdminCreateUserOnly": False}
    pool = cognito.create_user_pool(**create_kwargs)
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
        Name="MCP Workshop API" if args.workshop else "Finance MCP API",
        Scopes=[{"ScopeName": scope_name, "ScopeDescription": scope_desc}],
    )

    print("4/5 Creating app client...")
    if args.workshop:
        callback_urls = [
            "http://localhost:8000/auth/callback",
            "http://127.0.0.1:8000/auth/callback",
        ]
    else:
        callback_urls = [args.callback_url]
    client = cognito.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=client_name,
        GenerateSecret=True,
        AllowedOAuthFlows=["code"],
        AllowedOAuthFlowsUserPoolClient=True,
        AllowedOAuthScopes=["openid", full_scope],
        CallbackURLs=callback_urls,
        SupportedIdentityProviders=["COGNITO"],
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    )
    client_id = client["UserPoolClient"]["ClientId"]
    client_secret = client["UserPoolClient"]["ClientSecret"]
    print(f"    ClientId = {client_id}")

    password = None
    if args.workshop:
        print("5/5 Skipping test user -- self-service sign-up is enabled.")
        print("    Each attendee clicks 'Sign up' on the Hosted UI page themselves.")
    else:
        print("5/5 Creating test user...")
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
        "MCP_BASE_URL": "http://localhost:8000" if args.workshop
                        else args.callback_url.rsplit("/auth/callback", 1)[0],
    }

    print("\n" + "=" * 60)
    if args.workshop:
        print("Done. Put these three values on a slide / handout for attendees:\n")
        print(f'  COGNITO_USER_POOL_ID={user_pool_id}')
        print(f'  COGNITO_CLIENT_ID={client_id}')
        print(f'  COGNITO_CLIENT_SECRET={client_secret}')
        print(f'  AWS_REGION={region}')
        print("\nThey paste these into kickstart/.env (see kickstart/README.md).")
        print("Everyone shares this one app client -- that's a workshop-only")
        print("shortcut (see README note on Dynamic Client Registration);")
        print("each attendee still signs up their OWN Hosted UI login.")
    else:
        print("Done. Export these before running finance_mcp_server.py:\n")
        for k, v in env.items():
            print(f'export {k}="{v}"')
        print(f"\nHosted UI login for the demo:")
        print(f"  email:    {args.test_user_email}")
        print(f"  password: {password}")
    print("\nSave this output -- the client secret is not retrievable again")
    print("without calling describe_user_pool_client. Teardown with:")
    print(f"  python teardown_cognito.py --user-pool-id {user_pool_id} --domain {domain_prefix}")
    print("=" * 60)

    out_file = "cognito_workshop_env.json" if args.workshop else "cognito_demo_env.json"
    with open(out_file, "w") as f:
        json.dump({**env, "domain_prefix": domain_prefix,
                    "test_user_email": args.test_user_email if not args.workshop else None,
                    "test_user_password": password}, f, indent=2)
    print(f"\nAlso wrote {out_file} (gitignore this -- it has the client secret).")


if __name__ == "__main__":
    main()
