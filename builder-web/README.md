# MCP Builder Web

The click-to-deploy front end for the Launcher (`../launcher/`). Describe a
tool or paste your own code, click Deploy, get back a real MCP server
running in this AWS account -- no terminal needed.

## Run locally

```
pip install -r requirements.txt
cp .env.example .env   # defaults already point at the deployed Launcher
python app.py
```

Open `http://localhost:8000`. Sign in with the shared workshop Cognito
login -- this app registers itself with the Launcher via Dynamic Client
Registration on first use, so no Cognito configuration is needed here at
all, same as Inspector, `submit.py`, or the Electron client.

## How it works

This app is itself an OAuth *client* of the Launcher. Unlike the other
clients in this repo (which use a CLI-style local redirect listener),
this one hand-rolls the authorization-code + PKCE exchange in `app.py`
because a web app needs a real public `redirect_uri`, not a localhost
callback. Once logged in, the actual deploy call reuses the real
`fastmcp` client with a plain bearer token
(`fastmcp.client.auth.BearerAuth`) -- no custom MCP protocol code, only
the OAuth login that gets the token in the first place.

"Draft with AI" calls Bedrock server-side to turn a plain-English
description into a tool function; the result is shown for review before
deploying, same validation (`submit_server`'s own AST allowlist) applies
regardless of whether a human or an LLM wrote the code.
