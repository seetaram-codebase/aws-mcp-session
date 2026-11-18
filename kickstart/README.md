# MCP Kickstart -- build your own OAuth-protected MCP server

15 minutes. By the end you'll have a real MCP server running on your
laptop, protected by real OAuth 2.1 (not a fake API key), with your own
tool in it that Claude Desktop or Cursor can call.

## 0. Prerequisites

- Python 3.10+
- Claude Desktop or Cursor installed
- The four `COGNITO_*` values from the speaker's slide

## 1. Install

```
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
```

## 2. Configure

```
cp .env.example .env          # macOS/Linux
copy .env.example .env        # Windows
```

Open `.env` and paste in the four values from the slide. Leave
`MCP_BASE_URL` as `http://localhost:8000`.

## 3. Run the server

```
python server_template.py
```

Leave this running. It's now listening on `http://localhost:8000`,
protected by Cognito -- try hitting it with a plain browser/curl and
you'll get a 401, not your data.

## 4. Prove the OAuth flow works

In a **second terminal** (same venv activated):

```
python test_client.py
```

This opens your browser to Cognito's Hosted UI.

- First time: click **Sign up**, create your own login (any email,
  doesn't need to be real/verified for this pool). This is genuinely
  your own account -- nobody else's.
- Log in. The browser redirects back to localhost and the terminal
  prints the result of calling `ping`.

If you see `pong, workshop! your MCP server is OAuth-protected.` -- it
worked end to end: browser consent, authorization code, PKCE exchange,
bearer token, verified tool call.

## 5. Connect your IDE

**Claude Desktop** -- Settings -> Connectors -> Add custom connector,
URL: `http://localhost:8000/mcp`. It'll pop the same Hosted UI login on
first use.

**Cursor** -- add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "kickstart": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Ask the agent to "ping me" -- it should trigger the browser auth prompt
(skip it if you already authorized in step 4) and call your tool.

## 6. Add your own tool

Open `server_template.py`. Copy the `ping` function, rename it, change
what it does. A tool is just a typed function with a docstring:

```python
@mcp.tool
def roll_dice(sides: int = 6) -> dict:
    """Roll a die with the given number of sides."""
    import random
    return {"result": random.randint(1, sides)}
```

Save, `Ctrl+C` the server, rerun `python server_template.py`. Ask your
IDE's agent to use your new tool.

## 7. Submit it live

Once your tool works locally, go live on its own dedicated server with a
real public URL:

```
python submit.py "your-name-or-team"
```

This reads whatever you wrote between the `# >>> SUBMIT START` /
`# >>> SUBMIT END` markers in `server_template.py` and sends it to the
Launcher (same OAuth login as step 4). The Launcher stands up a brand new
ECS task running your code, under its own HTTPS domain -- takes a couple of
minutes (mostly CloudFront propagation), then prints your URL. Hand that URL
to the speaker to add to the shared MCP Inspector screen, or add it yourself
if you've got Inspector running.

Only plain functions using `random`/`math`/`datetime`/`json`/`re` are
allowed -- no file, network, or subprocess access. Validation happens twice
(once before anything is deployed, once again when your server boots), so
if it's going to be rejected, you'll know quickly.

## FAQ

**Why do we all share one `COGNITO_CLIENT_ID`?**
Real MCP clients are supposed to dynamically self-register with the
Authorization Server (RFC 7591). Cognito doesn't support that, so for
a 50-minute talk we're all sharing one pre-registered app client as a
shortcut. Your Hosted UI *login*, though, is 100% your own -- that part
isn't shared.

**My browser didn't redirect / hung on localhost.**
Make sure nothing else is using port 8000 (`server_template.py` needs
it free), and that you ran `test_client.py` *while* the server was
still running in the other terminal.

**Does this touch AWS infrastructure beyond Cognito?**
No -- `server_template.py` runs entirely on your laptop. Only the OAuth
handshake talks to AWS (Cognito). Nothing is deployed to your AWS
account, and you don't need one.
