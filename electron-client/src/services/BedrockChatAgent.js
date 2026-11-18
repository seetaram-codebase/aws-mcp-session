const { BedrockRuntimeClient, ConverseCommand } = require("@aws-sdk/client-bedrock-runtime");

/**
 * Drives a tool-calling conversation loop against Amazon Bedrock's Converse
 * API, using an McpToolClient as the tool backend. This is the actual
 * "agent" piece -- it decides which MCP tool to call, not a human clicking
 * a button in an IDE.
 */
class BedrockChatAgent {
  constructor({ modelId, region, mcpToolClient }) {
    this._modelId = modelId;
    this._mcpToolClient = mcpToolClient;
    this._bedrock = new BedrockRuntimeClient({ region });
    this._messages = [];
    this._tools = null;
  }

  async _getToolConfig() {
    if (!this._tools) {
      const mcpTools = await this._mcpToolClient.listTools();
      this._tools = mcpTools.map((tool) => ({
        toolSpec: {
          name: tool.name,
          description: tool.description || tool.name,
          inputSchema: { json: tool.inputSchema },
        },
      }));
    }
    return { tools: this._tools };
  }

  /** Sends a user message, runs the tool loop, returns the final assistant text. */
  async sendMessage(userText) {
    this._messages.push({ role: "user", content: [{ text: userText }] });
    const toolConfig = await this._getToolConfig();

    // Bounded to avoid a runaway loop if the model keeps requesting tools.
    for (let turn = 0; turn < 6; turn++) {
      const response = await this._bedrock.send(
        new ConverseCommand({
          modelId: this._modelId,
          messages: this._messages,
          toolConfig,
        })
      );

      const message = response.output.message;
      this._messages.push(message);

      if (response.stopReason !== "tool_use") {
        return message.content
          .filter((block) => block.text)
          .map((block) => block.text)
          .join("\n");
      }

      const toolResults = [];
      for (const block of message.content) {
        if (!block.toolUse) continue;
        const { toolUseId, name, input } = block.toolUse;
        let resultContent;
        try {
          const toolResult = await this._mcpToolClient.callTool(name, input);
          if (toolResult.structuredContent) {
            resultContent = [{ json: toolResult.structuredContent }];
          } else {
            const text = (toolResult.content ?? [])
              .filter((block) => block.type === "text")
              .map((block) => block.text)
              .join("\n");
            resultContent = [{ text }];
          }
        } catch (err) {
          resultContent = [{ text: `Tool error: ${err.message}` }];
        }
        toolResults.push({
          toolResult: { toolUseId, content: resultContent },
        });
      }
      this._messages.push({ role: "user", content: toolResults });
    }

    return "(stopped after too many tool-call turns)";
  }
}

module.exports = { BedrockChatAgent };
