"""
Submit your tool to go live -- reads the code you wrote in server_template.py
(between the SUBMIT START/END markers) and sends it to the Launcher, which
stands up a brand new, dedicated MCP server running it, with its own public
HTTPS URL.

Same OAuth flow as test_client.py -- opens your browser to Cognito's Hosted
UI (skips it if you're already logged in from earlier).

Usage:
    python submit.py "your-name-or-team"
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import Client

load_dotenv()

LAUNCHER_BASE_URL = os.environ.get("LAUNCHER_BASE_URL", "http://localhost:8001")
SERVER_FILE = Path(__file__).parent / "server_template.py"
START_MARKER = "# >>> SUBMIT START"
END_MARKER = "# >>> SUBMIT END"


def extract_tool_code() -> str:
    text = SERVER_FILE.read_text()
    try:
        start = text.index(START_MARKER) + len(START_MARKER)
        end = text.index(END_MARKER, start)
    except ValueError:
        raise SystemExit(
            f"Couldn't find the SUBMIT START/END markers in {SERVER_FILE.name} -- "
            "don't remove or reorder them."
        )
    code = text[start:end].strip()
    if not code:
        raise SystemExit(
            "Nothing found between the SUBMIT markers yet -- add your tool "
            f"function(s) there in {SERVER_FILE.name} first."
        )
    return code


async def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python submit.py <your-name-or-team>")
    attendee_name = sys.argv[1]
    tool_code = extract_tool_code()

    print(f"Submitting as '{attendee_name}'...")
    async with Client(f"{LAUNCHER_BASE_URL}/mcp", auth="oauth") as client:
        result = await client.call_tool(
            "submit_server", {"attendee_name": attendee_name, "tool_code": tool_code}
        )
        print(result.data)


if __name__ == "__main__":
    asyncio.run(main())
