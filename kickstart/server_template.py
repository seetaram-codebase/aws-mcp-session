"""
MCP Workshop Kickstart -- a FastMCP server protected by real OAuth 2.1.

This is the starting point for the live build segment. OAuth is already
wired up for you against a shared Cognito User Pool (provisioned by the
speaker) -- you don't need your own AWS account for this. Your job is just
to add a tool.

SETUP (do this first):
    1. pip install -r requirements.txt
    2. Copy .env.example to .env and fill in the four values from the
       slide/handout.
    3. python server_template.py
    4. In a second terminal: python test_client.py
       -- this opens your browser to Cognito's Hosted UI. Click "Sign up"
       and create your OWN login (just you, nobody else's). Then it calls
       `ping`, the one example tool below.
    5. Point Claude Desktop / Cursor at http://localhost:8000/mcp as a
       remote MCP server (see README.md for the exact config). Authorize
       in the browser when it prompts you. Ask it to "ping" you.

THEN: add your own tool. Copy the `ping` function below, rename it,
change what it does, save, and restart the server (Ctrl+C, rerun step 3).
No server restart needed on the auth side -- that part doesn't change.

NOTE ON THE SHARED CLIENT: everyone in the room is using the same
Cognito app client (COGNITO_CLIENT_ID/SECRET) -- that's a workshop-only
shortcut, not how a real deployment works. In production, each MCP
client dynamically registers its own client (RFC 7591); we're sharing one
because Cognito doesn't support that natively and provisioning one per
attendee isn't worth the setup time for a 50-minute talk. Your own Hosted
UI login, though, is genuinely yours.
"""

import os

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.providers.aws import AWSCognitoProvider

load_dotenv()

auth = AWSCognitoProvider(
    user_pool_id=os.environ["COGNITO_USER_POOL_ID"],
    client_id=os.environ["COGNITO_CLIENT_ID"],
    client_secret=os.environ["COGNITO_CLIENT_SECRET"],
    aws_region=os.environ.get("AWS_REGION", "us-east-1"),
    base_url=os.environ.get("MCP_BASE_URL", "http://localhost:8000"),
    required_scopes=["openid", "mcp/access"],
)

mcp = FastMCP("Workshop Kickstart", auth=auth)


@mcp.tool
def ping(name: str = "world") -> dict:
    """Example tool -- proves the OAuth round-trip works. Replace me."""
    return {"message": f"pong, {name}! your MCP server is OAuth-protected."}


# ---------------------------------------------------------------------------
# ADD YOUR OWN TOOL(S) BELOW. A tool is just a typed Python function with a
# docstring -- FastMCP turns the signature + docstring into the schema the
# agent sees. Delete `ping` once you've got your own, or leave it as a
# sanity check.
#
# Ideas if you want a starting point: a joke generator, a unit converter, a
# tiny todo list (in-memory dict is fine for the workshop), a "roll dice"
# tool, a lookup against any free public API you already know.
#
# Keep your function(s) between the two SUBMIT markers below -- that's the
# exact text submit.py sends to the Launcher when you're ready to go live
# (see "7. Submit it live" in README.md). Only plain functions using
# random/math/datetime/json/re -- no file, network, or subprocess access,
# it won't pass validation.
# ---------------------------------------------------------------------------

# >>> SUBMIT START (only edit between these two lines)

# >>> SUBMIT END


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
