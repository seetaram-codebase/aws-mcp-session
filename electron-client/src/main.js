require("dotenv").config();
const path = require("path");
const { app, BrowserWindow, ipcMain } = require("electron");

const { McpToolClient } = require("./services/McpToolClient");
const { BedrockChatAgent } = require("./services/BedrockChatAgent");

const MCP_BASE_URL = process.env.MCP_BASE_URL || "http://localhost:8000";
// Optional: the MCP Builder Launcher (see launcher/). Lets the agent submit
// a new tool and get a brand new, dedicated MCP server for it -- adds the
// submit_server tool to the agent's toolset when set.
const LAUNCHER_BASE_URL = process.env.LAUNCHER_BASE_URL || null;
const BEDROCK_MODEL_ID = process.env.BEDROCK_MODEL_ID || "us.anthropic.claude-haiku-4-5-20251001-v1:0";
const AWS_REGION = process.env.AWS_REGION || "us-east-1";

let agent = null;

function getAgent() {
  if (!agent) {
    const mcpToolClients = [new McpToolClient(MCP_BASE_URL)];
    if (LAUNCHER_BASE_URL) mcpToolClients.push(new McpToolClient(LAUNCHER_BASE_URL));
    agent = new BedrockChatAgent({ modelId: BEDROCK_MODEL_ID, region: AWS_REGION, mcpToolClients });
  }
  return agent;
}

function createWindow() {
  const window = new BrowserWindow({
    width: 480,
    height: 680,
    title: "MCP Finance Chat",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  window.loadFile(path.join(__dirname, "renderer", "index.html"));
}

ipcMain.handle("chat:send", async (_event, text) => {
  try {
    return await getAgent().sendMessage(text);
  } catch (err) {
    return { type: "text", text: `Error: ${err.message}` };
  }
});

ipcMain.handle("chat:confirm", async (_event, { toolUseId, approved }) => {
  try {
    return await getAgent().resolveConfirmation(toolUseId, approved);
  } catch (err) {
    return { type: "text", text: `Error: ${err.message}` };
  }
});

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
