"""
Exercises the OAuth flow against your locally running server_template.py --
the same authorization-code + PKCE dance Claude Desktop/Cursor do. Opens
your browser to Cognito's Hosted UI; sign up for your own account the
first time you run this.

Usage (server_template.py must already be running):
    python test_client.py
"""

import asyncio
import os

from dotenv import load_dotenv
from fastmcp import Client

load_dotenv()

BASE_URL = os.environ.get("MCP_BASE_URL", "http://localhost:8000")


async def main():
    async with Client(f"{BASE_URL}/mcp", auth="oauth") as client:
        tools = await client.list_tools()
        print("Tools your server exposes:", [t.name for t in tools])

        result = await client.call_tool("ping", {"name": "workshop"})
        print("ping ->", result.data)


if __name__ == "__main__":
    asyncio.run(main())
