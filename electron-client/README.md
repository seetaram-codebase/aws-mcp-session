# MCP Finance Chat (Electron)

A desktop chat agent: Amazon Bedrock (Claude) does the reasoning, the
finance MCP server (`server/finance_mcp_server.py` or
`kickstart/server_template.py`) supplies the tools, OAuth 2.1 against
Cognito secures the connection -- no IDE in the loop. This is the
"MCP isn't just an IDE feature" demo.

## Structure

```
src/
  main.js                     Electron main process -- owns the agent, IPC
  preload.js                  contextBridge: exposes chatApi.sendMessage()
  services/
    McpToolClient.js          MCP SDK client + OAuth handshake
    OAuthSessionStore.js      OAuthClientProvider impl (browser + loopback listener)
    BedrockChatAgent.js       Converse API tool-calling loop
  renderer/
    index.html, renderer.js, styles.css   the chat window itself
```

## Setup

```
npm install
cp .env.example .env    # MCP_BASE_URL, BEDROCK_MODEL_ID, AWS_REGION
```

Requires:
- An MCP server already running (`python ../server/finance_mcp_server.py`
  or `../kickstart/server_template.py`) at `MCP_BASE_URL`.
- Real AWS credentials in your environment/profile with Bedrock
  `InvokeModel`/`Converse` access to the model in `BEDROCK_MODEL_ID` --
  unlike the MCP server, this app DOES call AWS directly (Bedrock), using
  whatever credentials your AWS CLI/SDK default chain resolves.
- That model's "use case details" submitted in the Bedrock console (Model
  access page) -- Anthropic models on Bedrock require this one-time,
  console-only step per account; there's no API for it.

## Run

```
npm start
```

A chat window opens. First message triggers the OAuth flow (browser pops
to Cognito's Hosted UI); ask something that needs a tool, e.g. "how is my
portfolio allocated?"

## Standalone smoke test (no UI)

```
npx electron test-mcp-client.js
```

Validates the OAuth + MCP leg by itself -- useful for isolating whether a
problem is "MCP client can't connect" vs. "Bedrock can't be invoked."
