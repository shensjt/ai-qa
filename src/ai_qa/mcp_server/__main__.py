"""`python -m ai_qa.mcp_server`：独立 stdio MCP Server 入口。"""

from __future__ import annotations

import asyncio
import os

from ai_qa.log import configure_logging
from ai_qa.mcp_server.server import main

if __name__ == "__main__":
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    asyncio.run(main())
