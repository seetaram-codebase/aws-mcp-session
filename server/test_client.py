"""
Exercises the full OAuth 2.1 flow against the running finance_mcp_server.py,
the same way Claude Desktop / Cursor would as a remote MCP client: opens
your default browser to Cognito's Hosted UI, completes authorization-code +
PKCE, and calls a tool with the resulting bearer token.

Usage (server must already be running):
    export MCP_BASE_URL=http://localhost:8000
    python test_client.py
"""

import asyncio
import os

from fastmcp import Client

BASE_URL = os.environ.get("MCP_BASE_URL", "http://localhost:8000")


async def main():
    async with Client(f"{BASE_URL}/mcp", auth="oauth") as client:
        tools = await client.list_tools()
        print("Tools:", [t.name for t in tools])

        portfolio = await client.call_tool("get_portfolio", {})
        print("get_portfolio ->", portfolio.data)

        quote = await client.call_tool("get_quote", {"symbol": "AAPL"})
        print("get_quote(AAPL) ->", quote.data)

        allocation = await client.call_tool("analyze_allocation", {})
        print("analyze_allocation ->", allocation.data)


if __name__ == "__main__":
    asyncio.run(main())
