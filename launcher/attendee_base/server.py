"""
Shared base image for attendee-submitted MCP servers.

ONE Docker image, reused for every attendee -- no per-attendee Docker build.
What makes each running task different is a single environment variable,
ATTENDEE_TOOL_CODE, containing the Python source an attendee wrote below the
"ADD YOUR OWN TOOL(S) BELOW" marker in kickstart/server_template.py (sent here
by launcher/launcher_server.py's submit_server tool, which already validated
it once). This process re-validates independently at boot -- defense in
depth, not because the Launcher is trusted any less, but because this is the
process that actually executes the code, so it should not rely solely on a
check performed somewhere else.

Same AWSCognitoProvider shape as kickstart/server_template.py and
server/finance_mcp_server.py -- same shared Cognito pool, just a different
MCP_BASE_URL (this task's own CloudFront domain, set by the Launcher at
registration time).
"""

import ast
import os

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.providers.aws import AWSCognitoProvider

load_dotenv()

# Kept in sync manually with launcher/launcher_server.py's validate() --
# small enough that a shared package wasn't worth the extra deploy complexity
# for a workshop demo.
ALLOWED_IMPORTS = {"random", "math", "datetime", "json", "re"}
DISALLOWED_CALLS = {"open", "exec", "eval", "__import__", "compile", "input"}


def validate(code: str) -> None:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [n.name for n in node.names]
            if not set(names).issubset(ALLOWED_IMPORTS):
                raise ValueError(f"disallowed import: {names}")
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in DISALLOWED_CALLS:
            raise ValueError(f"disallowed call: {node.func.id}")
        if isinstance(node, (ast.Attribute,)) and node.attr.startswith("__"):
            raise ValueError(f"disallowed dunder access: {node.attr}")


auth = AWSCognitoProvider(
    user_pool_id=os.environ["COGNITO_USER_POOL_ID"],
    client_id=os.environ["COGNITO_CLIENT_ID"],
    client_secret=os.environ["COGNITO_CLIENT_SECRET"],
    aws_region=os.environ.get("AWS_REGION", "us-east-1"),
    base_url=os.environ["MCP_BASE_URL"],
    required_scopes=["openid", "mcp/access"],
)

mcp = FastMCP("Attendee MCP Server", auth=auth)


def _register_attendee_tools() -> None:
    code = os.environ.get("ATTENDEE_TOOL_CODE", "").strip()
    if not code:
        return

    validate(code)

    # __import__ is included so plain `import random` etc. works inside a
    # tool body -- safe despite being in DISALLOWED_CALLS above, because that
    # check is against the SOURCE AST (blocks writing `__import__("os")`
    # directly); the module actually reachable via a real `import` statement
    # is still constrained to ALLOWED_IMPORTS by validate()'s own AST walk,
    # which runs before this exec ever happens.
    allowed_builtin_names = (
        "len", "range", "str", "int", "float", "bool", "dict", "list", "tuple",
        "min", "max", "sum", "round", "abs", "__import__",
    )
    namespace: dict = {}
    builtins_source = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
    restricted_builtins = {name: builtins_source[name] for name in allowed_builtin_names if name in builtins_source}
    exec(compile(code, "<attendee_tool>", "exec"), {"__builtins__": restricted_builtins}, namespace)

    registered = 0
    for value in namespace.values():
        if callable(value) and getattr(value, "__module__", None) is None:
            mcp.add_tool(value)
            registered += 1

    if registered == 0:
        raise ValueError("no top-level function found in submitted code")


_register_attendee_tools()


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
