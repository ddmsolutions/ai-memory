"""#61: stdio MCP server registration. Requires the [mcp] extra."""

from __future__ import annotations

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise SystemExit(
        "the MCP server needs the optional dependency: pip install 'ai-memory[mcp]'"
    ) from exc

from . import tools


def build_server() -> "FastMCP":
    server = FastMCP(
        "ai-memory",
        instructions=(
            "Persistent memory for this machine's ai-memory store. Writes pass"
            " the capture funnel (redaction, injection screen); reads honour"
            " quarantine. Destructive operations (purge, import, tuning) are"
            " deliberately not exposed - they are CLI-only, human-run."
        ),
    )
    for fn in tools.TOOL_FUNCTIONS:
        server.tool(name=f"memory_{fn.__name__}")(fn)
    return server


def main() -> int:
    build_server().run()
    return 0
