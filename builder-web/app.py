"""
MCP Builder Web -- a browser-based front end for the Launcher
(launcher/launcher_server.py). Where submit.py is "write Python, run a
script" and the Electron agent is "chat with an LLM", this is "fill a form,
click Deploy" -- the easiest on-ramp of the three, aimed at someone who
just wants to see their own MCP server come alive without touching a
terminal.

Architecture note: this app is an OAuth *client* of the Launcher, the same
way MCP Inspector, submit.py, and the Electron app all are -- it just runs
the authorization-code + PKCE dance itself (hand-rolled below) instead of
through an MCP SDK's CLI-oriented OAuth helper, because a web app needs a
real public redirect_uri, not a localhost callback listener. Dynamic Client
Registration (DCR) against the Launcher's own OAuth proxy means this needs
zero Cognito/Terraform changes to add -- same mechanism documented in
server/finance_mcp_server.py's module docstring.

Once a user is logged in, deploying their tool reuses the real fastmcp
Client with a plain bearer token (fastmcp.client.auth.BearerAuth) -- no
protocol code of our own for the actual MCP call, only for the OAuth login
that gets us the token in the first place.
"""

import base64
import hashlib
import os
import re
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

LAUNCHER_BASE_URL = os.environ["LAUNCHER_BASE_URL"].rstrip("/")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
REDIRECT_URI = f"{PUBLIC_BASE_URL}/callback"

app = FastAPI(title="MCP Builder Web")

# In-memory session + OAuth-client state -- fine for a single-instance
# workshop demo (desired_count=1, same posture as the Launcher and finance
# server). A real multi-instance deployment would move this to a shared
# store (DynamoDB, Redis) instead.
SESSIONS: dict[str, dict] = {}
_oauth_client: dict | None = None

SESSION_COOKIE = "mcp_builder_sid"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


async def _ensure_oauth_client() -> dict:
    """Dynamically registers this app as an OAuth client of the Launcher,
    once, lazily. Shared by every visitor -- same "one app client" pattern
    kickstart/ and the Electron app already use, just registered via DCR
    here instead of pre-provisioned in Terraform."""
    global _oauth_client
    if _oauth_client is None:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"{LAUNCHER_BASE_URL}/register",
                json={
                    "client_name": "MCP Builder Web",
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
    SESSIONS[sid] = {}
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
        f"{LAUNCHER_BASE_URL}/authorize",
        params={
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": REDIRECT_URI,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "resource": f"{LAUNCHER_BASE_URL}/mcp",
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
            f"{LAUNCHER_BASE_URL}/token",
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


# Kept in sync manually with launcher/launcher_server.py's own allowlist --
# this is just for a nicer client-side error message before hitting the
# network; the Launcher re-validates independently regardless, so this
# duplication can't create a security gap, only a slightly worse UX if it
# drifts.
ALLOWED_IMPORTS_HINT = "random, math, datetime, json, re"


@app.post("/api/draft")
async def api_draft(request: Request):
    body = await request.json()
    description = (body.get("description") or "").strip()
    if not description:
        return JSONResponse({"error": "Describe what the tool should do."}, status_code=400)

    bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    prompt = (
        "Write exactly one Python function for an MCP tool. Requirements:\n"
        "- A single top-level function, type-annotated signature, a docstring.\n"
        f"- Only these imports are allowed, and only if actually needed: {ALLOWED_IMPORTS_HINT}.\n"
        "- No file, network, or subprocess access.\n"
        "- Return ONLY the function source code -- no markdown fences, no explanation.\n\n"
        f"The tool should: {description}"
    )
    response = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
    )
    code = response["output"]["message"]["content"][0]["text"].strip()
    code = re.sub(r"^```(?:python)?\n?|```$", "", code, flags=re.MULTILINE).strip()

    match = re.search(r"^def\s+(\w+)\s*\(", code, flags=re.MULTILINE)
    suggested_name = match.group(1) if match else "my_tool"

    return {"code": code, "suggested_name": suggested_name}


@app.post("/api/deploy")
async def api_deploy(request: Request):
    session = _get_session(request)
    token = session.get("access_token")
    if not token:
        return JSONResponse({"error": "Not logged in."}, status_code=401)

    body = await request.json()
    attendee_name = (body.get("attendee_name") or "").strip()
    tool_code = (body.get("tool_code") or "").strip()
    if not attendee_name or not tool_code:
        return JSONResponse({"error": "attendee_name and tool_code are both required."}, status_code=400)

    try:
        async with Client(f"{LAUNCHER_BASE_URL}/mcp", auth=BearerAuth(token)) as client:
            result = await client.call_tool("submit_server", {"attendee_name": attendee_name, "tool_code": tool_code})
            return result.data
    except Exception as err:  # noqa: BLE001 -- surfaced directly to the UI, this is a demo app
        return JSONResponse({"error": str(err)}, status_code=502)


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
