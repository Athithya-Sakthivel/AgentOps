"""
MCP client wrapper using the official FastMCP client library.

All tool results are plain Python types (str, dict, list of dicts).
Includes a retry loop so the agent survives a cold MCP start.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import settings
from fastmcp import Client

log = logging.getLogger("agent-service")


class MCPClientManager:
    """Manages connection to the mcp-server and exposes tools as callable methods."""

    def __init__(self):
        self._client: Client | None = None
        self._tools: dict[str, Any] = {}

    async def connect(self):
        client = Client(settings.mcp_server_url)
        for attempt in range(5):
            try:
                await client.__aenter__()
                tools_list = await client.list_tools()
                self._tools = {tool.name: tool for tool in tools_list}
                self._client = client
                log.info(
                    "MCP client connected - %d tools loaded: %s",
                    len(self._tools),
                    list(self._tools.keys()),
                )
                return
            except Exception:
                if attempt == 4:
                    raise
                log.warning(
                    "MCP connection attempt %d failed, retrying in 2s...",
                    attempt + 1,
                )
                await asyncio.sleep(2)

    async def close(self):
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
        self._client = None
        self._tools.clear()

    async def call_tool(self, name: str, arguments: dict[str, Any], run_id: str = "") -> Any:
        if self._client is None:
            raise RuntimeError("MCP client not connected")
        if name not in self._tools:
            raise ValueError(f"Unknown tool: {name}. Available: {list(self._tools.keys())}")

        arguments = {**arguments, "run_id": run_id}

        try:
            result = await self._client.call_tool(name, arguments)
            if hasattr(result, "data") and result.data is not None:
                return result.data
            return result
        except Exception:
            log.exception("MCP tool call failed: %s", name)
            raise
