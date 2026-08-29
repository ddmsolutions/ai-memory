"""ai-memory MCP server package (#61).

Separately maintained from the engine: dependency flows one way, server ->
engine, and the core package never imports this one. Installable via the
extra `ai-memory[mcp]`; the base install is unchanged.

tools.py holds the pure tool functions (no mcp dependency, unit-testable);
server.py registers them with FastMCP; `python -m ai_memory_mcp` runs stdio.
"""

__all__ = ["tools"]
