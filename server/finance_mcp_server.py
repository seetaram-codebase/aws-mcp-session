"""
Personal Finance Guide -- an OAuth 2.1-protected FastMCP server.

Demo server for the "Building an AWS-Native MCP Server" session. Exposes a
few finance tools over Streamable HTTP, protected end-to-end by AWS Cognito
acting as the OAuth 2.1 Authorization Server (SEP-990's enterprise-auth
path). No API keys, no static secrets handed to the agent -- Claude
Desktop / Cursor complete a real authorization-code + PKCE flow against
Cognito's Hosted UI and present a short-lived bearer token on every call.

Run locally:
    export COGNITO_USER_POOL_ID=...
    export COGNITO_CLIENT_ID=...
    export COGNITO_CLIENT_SECRET=...
    export AWS_REGION=us-east-1
    export MCP_BASE_URL=http://localhost:8000
    python finance_mcp_server.py

All values above are printed by infra/setup_cognito.py after provisioning.
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.aws import AWSCognitoProvider

auth = AWSCognitoProvider(
    user_pool_id=os.environ["COGNITO_USER_POOL_ID"],
    client_id=os.environ["COGNITO_CLIENT_ID"],
    client_secret=os.environ["COGNITO_CLIENT_SECRET"],
    aws_region=os.environ.get("AWS_REGION", "us-east-1"),
    base_url=os.environ["MCP_BASE_URL"],
    required_scopes=["openid", "finance/read"],
)

mcp = FastMCP("Personal Finance Guide", auth=auth)

# Mock data -- stands in for a DynamoDB-backed portfolio table in production.
# Swap this for a real read behind a least-privilege IAM task role once this
# server is deployed to ECS Fargate (see session-outline section 04).
_PORTFOLIO = {
    "cash_usd": 12500.00,
    "holdings": [
        {"symbol": "VTI", "shares": 42, "cost_basis": 215.30},
        {"symbol": "AAPL", "shares": 15, "cost_basis": 178.10},
        {"symbol": "BND", "shares": 60, "cost_basis": 72.40},
    ],
}

_QUOTES = {"VTI": 268.91, "AAPL": 231.44, "BND": 71.85}


@mcp.tool
def get_portfolio() -> dict:
    """Return the authenticated user's current holdings and cash balance."""
    return _PORTFOLIO


@mcp.tool
def get_quote(symbol: str) -> dict:
    """Look up the latest quote for a ticker symbol (e.g. 'AAPL')."""
    symbol = symbol.upper()
    if symbol not in _QUOTES:
        return {"error": f"no quote available for {symbol}"}
    return {"symbol": symbol, "price_usd": _QUOTES[symbol]}


@mcp.tool
def analyze_allocation() -> dict:
    """Compute current asset allocation percentages across cash and holdings."""
    total = _PORTFOLIO["cash_usd"]
    values = {}
    for h in _PORTFOLIO["holdings"]:
        value = h["shares"] * _QUOTES.get(h["symbol"], h["cost_basis"])
        values[h["symbol"]] = value
        total += value

    allocation = {"cash": round(_PORTFOLIO["cash_usd"] / total * 100, 1)}
    allocation.update({sym: round(v / total * 100, 1) for sym, v in values.items()})
    return {"total_value_usd": round(total, 2), "allocation_pct": allocation}


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
