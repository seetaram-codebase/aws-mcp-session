"""
Finance Chat Web -- a browser-based chat front end for the Personal
Finance Guide MCP server (../server/finance_mcp_server.py). Same idea as
the Electron chat agent (../electron-client/) -- Bedrock Claude does the
reasoning, the finance MCP server supplies the tools (get_portfolio,
get_quote, analyze_allocation), OAuth 2.1 against Cognito secures the
connection -- just in a browser tab instead of a desktop window.

Architecture note: this app is an OAuth *client* of the finance server,
the exact same pattern builder-web/app.py uses against the Launcher --
hand-rolled authorization-code + PKCE (a web app needs a real public
redirect_uri, not a localhost callback listener) via Dynamic Client
Registration, so zero Cognito/Terraform changes were needed to add this.

Once logged in, the actual tool calls reuse the real fastmcp Client with
a plain bearer token (fastmcp.client.auth.BearerAuth); the Bedrock
Converse tool-calling loop below is a direct Python port of
electron-client/src/services/BedrockChatAgent.js, minus the
CONFIRM_REQUIRED_TOOLS gate -- every tool on the finance server is a
read-only mock lookup, so there's nothing here that needs a human
approval click before running (unlike submit_server in the other apps).
"""

import asyncio
import base64
import hashlib
import os
import secrets
import time

import boto3
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastmcp import Client
from fastmcp.client.auth import BearerAuth

load_dotenv()

FINANCE_MCP_BASE_URL = os.environ["FINANCE_MCP_BASE_URL"].rstrip("/")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
REDIRECT_URI = f"{PUBLIC_BASE_URL}/callback"
MAX_TOOL_TURNS = 6

app = FastAPI(title="Finance Chat Web")
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

# In-memory session + OAuth-client state -- fine for a single-instance demo,
# same posture as builder-web and the Launcher. A real deployment would move
# this to a shared store (DynamoDB, Redis) instead.
SESSIONS: dict[str, dict] = {}
_oauth_client: dict | None = None

SESSION_COOKIE = "finance_chat_sid"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


async def _ensure_oauth_client() -> dict:
    """Dynamically registers this app as an OAuth client of the finance
    server, once, lazily -- same DCR pattern builder-web uses against the
    Launcher. AWSCognitoProvider backs both servers, so /register works
    identically here."""
    global _oauth_client
    if _oauth_client is None:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"{FINANCE_MCP_BASE_URL}/register",
                json={
                    "client_name": "Finance Chat Web",
                    "redirect_uris": [REDIRECT_URI],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
            )
            resp.raise_for_status()
            _oauth_client = resp.json()
    return _oauth_client


def _get_session(request: Request) -> dict:
    sid = request.cookies.get(SESSION_COOKIE)
    if sid and sid in SESSIONS:
        return SESSIONS[sid]
    return {}


def _new_session() -> tuple[str, dict]:
    sid = secrets.token_urlsafe(24)
    SESSIONS[sid] = {"messages": []}
    return sid, SESSIONS[sid]


@app.get("/api/session")
async def api_session(request: Request):
    session = _get_session(request)
    return {"logged_in": bool(session.get("access_token"))}


@app.get("/login")
async def login(request: Request):
    client = await _ensure_oauth_client()

    sid = request.cookies.get(SESSION_COOKIE)
    if not sid or sid not in SESSIONS:
        sid, session = _new_session()
    else:
        session = SESSIONS[sid]

    code_verifier = _b64url(secrets.token_bytes(40))
    code_challenge = _b64url(hashlib.sha256(code_verifier.encode()).digest())
    state = secrets.token_urlsafe(16)
    session["pending"] = {"code_verifier": code_verifier, "state": state}

    authorize_url = httpx.URL(
        f"{FINANCE_MCP_BASE_URL}/authorize",
        params={
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": REDIRECT_URI,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "resource": f"{FINANCE_MCP_BASE_URL}/mcp",
            "scope": "openid mcp/access",
        },
    )

    response = RedirectResponse(str(authorize_url))
    response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
    return response


@app.get("/callback")
async def callback(request: Request, code: str, state: str):
    sid = request.cookies.get(SESSION_COOKIE)
    session = SESSIONS.get(sid) if sid else None
    pending = (session or {}).get("pending")
    if not session or not pending or pending["state"] != state:
        return HTMLResponse("Login session expired or invalid -- go back and try again.", status_code=400)

    client = await _ensure_oauth_client()
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{FINANCE_MCP_BASE_URL}/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": client["client_id"],
                "code_verifier": pending["code_verifier"],
            },
        )
        resp.raise_for_status()
        token_data = resp.json()

    session["access_token"] = token_data["access_token"]
    session["token_expires_at"] = time.time() + token_data.get("expires_in", 3600)
    session.pop("pending", None)

    return RedirectResponse("/")


@app.post("/logout")
async def logout(request: Request):
    sid = request.cookies.get(SESSION_COOKIE)
    if sid in SESSIONS:
        del SESSIONS[sid]
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


def _tool_config(tools) -> dict:
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": t.name,
                    "description": t.description or t.name,
                    "inputSchema": {"json": t.input_schema},
                }
            }
            for t in tools
        ]
    }


async def _call_tool(mcp_client: Client, name: str, arguments: dict) -> list[dict]:
    result = await mcp_client.call_tool(name, arguments)
    if result.data is not None:
        return [{"json": result.data}]
    if result.structured_content is not None:
        return [{"json": result.structured_content}]
    text = "\n".join(b.text for b in result.content if getattr(b, "type", None) == "text")
    return [{"text": text}]


async def _run_loop(mcp_client: Client, tool_config: dict, messages: list[dict]) -> str:
    """Bedrock Converse <-> MCP tool loop. Direct port of
    BedrockChatAgent.js's _runLoop, minus the confirm-required-tools gate --
    every finance-server tool is a read-only lookup."""
    for _ in range(MAX_TOOL_TURNS):
        response = await asyncio.to_thread(
            bedrock.converse,
            modelId=BEDROCK_MODEL_ID,
            messages=messages,
            toolConfig=tool_config,
        )
        message = response["output"]["message"]
        messages.append(message)

        if response["stopReason"] != "tool_use":
            return "\n".join(block.get("text", "") for block in message["content"] if "text" in block).strip()

        tool_results = []
        for block in message["content"]:
            if "toolUse" not in block:
                continue
            tool_use = block["toolUse"]
            try:
                content = await _call_tool(mcp_client, tool_use["name"], tool_use.get("input") or {})
            except Exception as err:  # noqa: BLE001 -- surfaced back to the model, then to the UI
                content = [{"text": f"Tool error: {err}"}]
            tool_results.append({"toolResult": {"toolUseId": tool_use["toolUseId"], "content": content}})
        messages.append({"role": "user", "content": tool_results})

    return "(stopped after too many tool-call turns)"


@app.post("/api/chat")
async def api_chat(request: Request):
    session = _get_session(request)
    token = session.get("access_token")
    if not token:
        return JSONResponse({"error": "Not logged in."}, status_code=401)

    body = await request.json()
    user_text = (body.get("message") or "").strip()
    if not user_text:
        return JSONResponse({"error": "message is required."}, status_code=400)

    session["messages"].append({"role": "user", "content": [{"text": user_text}]})

    try:
        async with Client(f"{FINANCE_MCP_BASE_URL}/mcp", auth=BearerAuth(token)) as mcp_client:
            if "tool_config" not in session:
                tools = await mcp_client.list_tools()
                session["tool_config"] = _tool_config(tools)
            reply = await _run_loop(mcp_client, session["tool_config"], session["messages"])
        return {"reply": reply}
    except Exception as err:  # noqa: BLE001 -- surfaced directly to the UI, this is a demo app
        return JSONResponse({"error": str(err)}, status_code=502)


@app.post("/api/reset")
async def api_reset(request: Request):
    session = _get_session(request)
    session["messages"] = []
    session.pop("tool_config", None)
    return {"ok": True}


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
