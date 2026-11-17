const http = require("http");
const { shell } = require("electron");

const REDIRECT_PORT = 51823;
const REDIRECT_PATH = "/callback";

/**
 * Implements the MCP SDK's OAuthClientProvider interface for a desktop app:
 * stores client registration + tokens in memory for the process lifetime,
 * and handles the redirect leg by opening the system browser and running a
 * one-shot local HTTP server to catch the authorization code.
 *
 * This talks to the FastMCP server's own OAuth proxy (AWSCognitoProvider),
 * not to Cognito directly -- the proxy is what's DCR-compliant (Cognito
 * itself isn't), so this class needs nothing Cognito-specific.
 */
class OAuthSessionStore {
  constructor() {
    this._clientInformation = undefined;
    this._tokens = undefined;
    this._codeVerifier = undefined;
    this._pendingCode = null;
    this._server = null;
  }

  get redirectUrl() {
    return `http://127.0.0.1:${REDIRECT_PORT}${REDIRECT_PATH}`;
  }

  get clientMetadata() {
    return {
      client_name: "MCP Finance Chat (Electron)",
      redirect_uris: [this.redirectUrl],
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
      token_endpoint_auth_method: "none",
    };
  }

  clientInformation() {
    return this._clientInformation;
  }

  saveClientInformation(info) {
    this._clientInformation = info;
  }

  tokens() {
    return this._tokens;
  }

  saveTokens(tokens) {
    this._tokens = tokens;
  }

  saveCodeVerifier(verifier) {
    this._codeVerifier = verifier;
  }

  codeVerifier() {
    return this._codeVerifier;
  }

  /** Opens the system browser to the authorization URL. Does not block. */
  redirectToAuthorization(authorizationUrl) {
    this._startRedirectListener();
    shell.openExternal(authorizationUrl.toString());
  }

  /** Resolves once the local redirect listener captures ?code=... */
  waitForAuthorizationCode() {
    if (this._pendingCode) return Promise.resolve(this._pendingCode);
    if (!this._codePromise) {
      this._codePromise = new Promise((resolve, reject) => {
        this._resolveCode = resolve;
        this._rejectCode = reject;
      });
    }
    return this._codePromise;
  }

  _startRedirectListener() {
    if (this._server) return;

    this._server = http.createServer((req, res) => {
      const url = new URL(req.url, this.redirectUrl);
      if (url.pathname !== REDIRECT_PATH) {
        res.writeHead(404).end();
        return;
      }

      const code = url.searchParams.get("code");
      const error = url.searchParams.get("error");

      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(
        error
          ? `<html><body><h2>Authorization failed: ${error}</h2>You can close this tab.</body></html>`
          : "<html><body><h2>Authorized.</h2>You can close this tab and return to the app.</body></html>"
      );

      this._server.close();
      this._server = null;

      if (error) {
        this._pendingCode = null;
        if (this._rejectCode) this._rejectCode(new Error(`OAuth error: ${error}`));
      } else {
        this._pendingCode = code;
        if (this._resolveCode) this._resolveCode(code);
      }
    });

    this._server.listen(REDIRECT_PORT);
  }
}

module.exports = { OAuthSessionStore };
