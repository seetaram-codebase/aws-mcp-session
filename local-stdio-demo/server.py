"""
Local stdio MCP server -- no network, no OAuth, no CloudFront.

Contrast with every other server in this repo: this one is launched
directly by the MCP client as a subprocess and talks over stdin/stdout.
Trust comes from "you're the one who launched it," not a bearer token --
which is exactly why its two tools are scoped to touch only this folder's
own files/ subdirectory, never the rest of your filesystem.
"""

# /// script
# dependencies = ["fastmcp"]
# ///

from pathlib import Path

from fastmcp import FastMCP

BASE_DIR = (Path(__file__).parent / "files").resolve()
BASE_DIR.mkdir(exist_ok=True)

mcp = FastMCP("Local File Demo")


def _safe_path(filename: str) -> Path:
    path = (BASE_DIR / filename).resolve()
    if not path.is_relative_to(BASE_DIR):
        raise ValueError(f"'{filename}' escapes the sandboxed directory")
    return path


@mcp.tool
def create_file(filename: str, content: str) -> dict:
    """Create (or overwrite) a text file inside the sandboxed local demo folder."""
    path = _safe_path(filename)
    path.write_text(content, encoding="utf-8")
    return {"created": str(path.relative_to(BASE_DIR)), "bytes": len(content)}


@mcp.tool
def list_files() -> list[dict]:
    """List all files currently in the sandboxed local demo folder."""
    return [
        {"name": p.name, "bytes": p.stat().st_size}
        for p in sorted(BASE_DIR.iterdir())
        if p.is_file()
    ]


if __name__ == "__main__":
    mcp.run(transport="stdio")
