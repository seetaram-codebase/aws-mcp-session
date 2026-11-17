// Standalone smoke test for the MCP OAuth client, independent of the
// Electron UI and Bedrock. Opens your real default browser for the
// Cognito Hosted UI login/signup -- complete it there, then this prints
// the tool list and a sample tool call.
//
// Usage: npx electron test-mcp-client.js
require("dotenv").config();
const { app } = require("electron");
const { McpToolClient } = require("./src/services/McpToolClient");

app.whenReady().then(async () => {
  try {
    const client = new McpToolClient(process.env.MCP_BASE_URL || "http://localhost:8000");
    console.log("Connecting -- a browser window should open for login/signup...");
    const tools = await client.listTools();
    console.log("Tools:", tools.map((t) => t.name));

    const result = await client.callTool(tools[0].name, {});
    console.log(`${tools[0].name}() ->`, JSON.stringify(result.content ?? result));
    process.exit(0);
  } catch (err) {
    console.error("ERROR:", err);
    process.exit(1);
  }
});
