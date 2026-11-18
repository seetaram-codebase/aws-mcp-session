# Finance Chat Web

A browser-based chat front end for `../server/finance_mcp_server.py` --
Bedrock Claude does the reasoning, the finance MCP server supplies the
tools (`get_portfolio`, `get_quote`, `analyze_allocation`), OAuth 2.1
against Cognito secures the connection. Same idea as `../electron-client/`,
just in a tab instead of a desktop window.

## Run locally

```
pip install -r requirements.txt
cp .env.example .env   # defaults already point at the deployed finance server
python app.py
```

Open `http://localhost:8000`. Sign in with your Cognito login (the
speaker's test user, or your own self-signed-up `kickstart/` login) --
this app registers itself with the finance server via Dynamic Client
Registration on first use, so no Cognito configuration is needed here at
all, same as the Electron client or `builder-web`.

Requires real AWS credentials in your environment with Bedrock
`Converse` access to `BEDROCK_MODEL_ID` (and that model's "use case
details" submitted once in the Bedrock console -- same one-time step
`electron-client/README.md` describes).

## How it works

OAuth client of the finance server, hand-rolled authorization-code +
PKCE in `app.py` (a web app needs a real public `redirect_uri`, not a
localhost listener) -- identical pattern to `builder-web/app.py` against
the Launcher. Once logged in, tool calls reuse the real `fastmcp` Client
with a plain bearer token (`fastmcp.client.auth.BearerAuth`).

The Bedrock Converse <-> MCP tool loop (`_run_loop` in `app.py`) is a
direct Python port of `electron-client/src/services/BedrockChatAgent.js`,
minus the `CONFIRM_REQUIRED_TOOLS` gate -- every tool on the finance
server is a read-only mock lookup, so nothing here needs a human
approval click before running.
