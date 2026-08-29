"""`python -m ai_memory_mcp` starts the stdio MCP server (#61)."""

import sys

from .server import main

if __name__ == "__main__":
    sys.exit(main())
