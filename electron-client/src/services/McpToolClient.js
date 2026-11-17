const { Client } = require("@modelcontextprotocol/sdk/client/index.js");
const { StreamableHTTPClientTransport } = require("@modelcontextprotocol/sdk/client/streamableHttp.js");
const { UnauthorizedError } = require("@modelcontextprotocol/sdk/client/auth.js");

const { OAuthSessionStore } = require("./OAuthSessionStore");

/**
 * Thin wrapper around the MCP SDK's Client + StreamableHTTPClientTransport,
 * handling the OAuth 2.1 handshake (SEP-990) against the finance MCP
 * server: real authorization-code + PKCE through Cognito's Hosted UI, not
 * a static bearer token.
 */
class McpToolClient {
  constructor(baseUrl) {
    this._baseUrl = baseUrl;
    this._authProvider = new OAuthSessionStore();
    this._client = new Client({ name: "mcp-finance-chat", version: "1.0.0" });
    this._transport = null;
    this._connected = false;
  }

  async connect() {
    if (this._connected) return;
    await this._attemptConnection();
    this._connected = true;
  }

  async _attemptConnection() {
    // A fresh transport per attempt, deliberately -- the SDK's own
    // reference OAuth client does the same (examples/client/simpleOAuthClient).
    // Calling client.connect() a second time on the SAME transport throws
    // "already started", since start() already ran (and set internal
    // state) on the attempt that failed with UnauthorizedError.
    const transport = new StreamableHTTPClientTransport(new URL(`${this._baseUrl}/mcp`), {
      authProvider: this._authProvider,
    });

    try {
      await this._client.connect(transport);
      this._transport = transport;
    } catch (err) {
      if (!(err instanceof UnauthorizedError)) throw err;

      // The transport already called authProvider.redirectToAuthorization()
      // internally (opening the browser) before throwing this. Wait for the
      // code to land on the local redirect listener, finish the exchange,
      // then retry with a brand-new transport.
      const code = await this._authProvider.waitForAuthorizationCode();
      await transport.finishAuth(code);
      await this._attemptConnection();
    }
  }

  async listTools() {
    await this.connect();
    const { tools } = await this._client.listTools();
    return tools;
  }

  async callTool(name, args) {
    await this.connect();
    const result = await this._client.callTool({ name, arguments: args });
    return result;
  }
}

module.exports = { McpToolClient };
