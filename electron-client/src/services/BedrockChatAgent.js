const { BedrockRuntimeClient, ConverseCommand } = require("@aws-sdk/client-bedrock-runtime");

// Tools in this set never execute automatically -- sendMessage() pauses the
// loop and hands control back to the UI, which must call
// resolveConfirmation() before anything actually happens. submit_server
// deploys real infrastructure, so an LLM deciding to call it isn't enough
// authority on its own; a human has to see the generated code first.
const CONFIRM_REQUIRED_TOOLS = new Set(["submit_server"]);

/**
 * Drives a tool-calling conversation loop against Amazon Bedrock's Converse
 * API, using one or more McpToolClients as the tool backend. This is the
 * actual "agent" piece -- it decides which MCP tool to call, not a human
 * clicking a button in an IDE.
 */
class BedrockChatAgent {
  constructor({ modelId, region, mcpToolClients }) {
    this._modelId = modelId;
    this._mcpToolClients = mcpToolClients;
    this._bedrock = new BedrockRuntimeClient({ region });
    this._messages = [];
    this._tools = null;
    this._toolOwner = new Map(); // tool name -> owning McpToolClient
    this._pending = null; // { assistantBlocks, resolved: Map<toolUseId, {approved}> }
  }

  async _getToolConfig() {
    if (!this._tools) {
      this._tools = [];
      for (const client of this._mcpToolClients) {
        const mcpTools = await client.listTools();
        for (const tool of mcpTools) {
          this._toolOwner.set(tool.name, client);
          this._tools.push({
            toolSpec: {
              name: tool.name,
              description: tool.description || tool.name,
              inputSchema: { json: tool.inputSchema },
            },
          });
        }
      }
    }
    return { tools: this._tools };
  }

  async _callTool(name, input) {
    const client = this._toolOwner.get(name);
    const toolResult = await client.callTool(name, input);
    if (toolResult.structuredContent) {
      return [{ json: toolResult.structuredContent }];
    }
    const text = (toolResult.content ?? [])
      .filter((block) => block.type === "text")
      .map((block) => block.text)
      .join("\n");
    return [{ text }];
  }

  /**
   * Runs one assistant message's tool_use blocks to completion, given a
   * resolutions map for any confirm-required ones (name -> {approved}).
   * Returns the toolResult content blocks to feed back to Bedrock.
   */
  async _resolveBlocks(blocks, resolutions) {
    const toolResults = [];
    for (const block of blocks) {
      if (!block.toolUse) continue;
      const { toolUseId, name, input } = block.toolUse;
      let resultContent;
      try {
        if (CONFIRM_REQUIRED_TOOLS.has(name)) {
          const resolution = resolutions.get(toolUseId);
          resultContent = resolution.approved
            ? await this._callTool(name, input)
            : [{ text: "The user declined to run this action. Do not retry it without being asked again." }];
        } else {
          resultContent = await this._callTool(name, input);
        }
      } catch (err) {
        resultContent = [{ text: `Tool error: ${err.message}` }];
      }
      toolResults.push({ toolResult: { toolUseId, content: resultContent } });
    }
    return toolResults;
  }

  /** Runs the Bedrock <-> tool loop until final text or a confirmation is needed. */
  async _runLoop() {
    const toolConfig = await this._getToolConfig();

    for (let turn = 0; turn < 6; turn++) {
      const response = await this._bedrock.send(
        new ConverseCommand({ modelId: this._modelId, messages: this._messages, toolConfig })
      );

      const message = response.output.message;
      this._messages.push(message);

      if (response.stopReason !== "tool_use") {
        return {
          type: "text",
          text: message.content
            .filter((block) => block.text)
            .map((block) => block.text)
            .join("\n"),
        };
      }

      const needsConfirmation = message.content
        .filter((block) => block.toolUse && CONFIRM_REQUIRED_TOOLS.has(block.toolUse.name))
        .map((block) => block.toolUse);

      if (needsConfirmation.length > 0) {
        // Pause here -- caller must resolveConfirmation() for each of these
        // (and any non-confirm tools in the same batch run only once all
        // confirm-required ones are resolved, to keep the batch atomic).
        this._pending = { assistantBlocks: message.content, resolutions: new Map() };
        const first = needsConfirmation[0];
        return { type: "confirm", toolUseId: first.toolUseId, name: first.name, input: first.input };
      }

      const toolResults = await this._resolveBlocks(message.content, new Map());
      this._messages.push({ role: "user", content: toolResults });
    }

    return { type: "text", text: "(stopped after too many tool-call turns)" };
  }

  /** Sends a user message. Returns {type:"text", text} or {type:"confirm", toolUseId, name, input}. */
  async sendMessage(userText) {
    this._messages.push({ role: "user", content: [{ text: userText }] });
    return this._runLoop();
  }

  /**
   * Approves or denies a pending confirm-required tool call, then resumes
   * the loop. Returns the same shape as sendMessage().
   */
  async resolveConfirmation(toolUseId, approved) {
    if (!this._pending) throw new Error("No pending confirmation to resolve.");
    this._pending.resolutions.set(toolUseId, { approved });

    const stillWaiting = this._pending.assistantBlocks.some(
      (block) =>
        block.toolUse &&
        CONFIRM_REQUIRED_TOOLS.has(block.toolUse.name) &&
        !this._pending.resolutions.has(block.toolUse.toolUseId)
    );
    if (stillWaiting) {
      const next = this._pending.assistantBlocks
        .map((b) => b.toolUse)
        .find((tu) => tu && CONFIRM_REQUIRED_TOOLS.has(tu.name) && !this._pending.resolutions.has(tu.toolUseId));
      return { type: "confirm", toolUseId: next.toolUseId, name: next.name, input: next.input };
    }

    const { assistantBlocks, resolutions } = this._pending;
    this._pending = null;
    const toolResults = await this._resolveBlocks(assistantBlocks, resolutions);
    this._messages.push({ role: "user", content: toolResults });
    return this._runLoop();
  }
}

module.exports = { BedrockChatAgent };
