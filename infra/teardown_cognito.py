"""
Removes everything setup_cognito.py created: the Hosted UI domain, then the
user pool (which cascades the app client, resource server, and test user).

Usage:
    python teardown_cognito.py --user-pool-id us-east-1_XXXXXXXXX --domain mcp-demo-finance-abcd1234
Or, with no args, reads infra/cognito_demo_env.json written by setup_cognito.py.
"""

import argparse
import json
import os
import sys

import boto3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-pool-id")
    parser.add_argument("--domain", help="Domain prefix, not the full amazoncognito.com host")
    parser.add_argument("--region", default=None)
    parser.add_argument(
        "--workshop", action="store_true",
        help="Read cognito_workshop_env.json instead of cognito_demo_env.json",
    )
    args = parser.parse_args()

    user_pool_id = args.user_pool_id
    domain = args.domain
    region = args.region

    env_filename = "cognito_workshop_env.json" if args.workshop else "cognito_demo_env.json"
    env_path = os.path.join(os.path.dirname(__file__), env_filename)
    if not user_pool_id and os.path.exists(env_path):
        with open(env_path) as f:
            saved = json.load(f)
        user_pool_id = saved["COGNITO_USER_POOL_ID"]
        domain = saved["domain_prefix"]
        region = region or saved["AWS_REGION"]

    if not user_pool_id or not domain:
        print(f"Need --user-pool-id and --domain (or a {env_filename} to read from).",
              file=sys.stderr)
        sys.exit(1)

    session = boto3.Session(region_name=region)
    cognito = session.client("cognito-idp")

    print(f"Deleting domain {domain}...")
    cognito.delete_user_pool_domain(Domain=domain, UserPoolId=user_pool_id)

    print(f"Deleting user pool {user_pool_id}...")
    cognito.delete_user_pool(UserPoolId=user_pool_id)

    if os.path.exists(env_path):
        os.remove(env_path)

    print("Done.")


if __name__ == "__main__":
    main()
