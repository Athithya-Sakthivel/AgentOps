"""
MCP client wrapper using langchain-mcp-adapters.

All tool results are normalised to plain Python types (str, dict, list of dicts)
before being returned to the caller.  No LangChain wrappers escape.
"""

from __future__ import annotations

import logging
from typing import Any

from config import settings
from langchain_mcp_adapters.client import MultiServerMCPClient

log = logging.getLogger("agent-service")


def _normalise_tool_result(result: Any) -> Any:
    """
    Convert any LangChain / MCP wrapper into a plain Python value.

    Handles:
    - ToolMessage objects (with a .content attribute)
    - Plain lists of {"type":"text","text":"..."} dicts
    - Already-plain strings / dicts / lists
    """

    # ── 1.  ToolMessage (has .content) ──────────────────────────────
    if hasattr(result, "content"):
        content = result.content
        # content can be a list of blocks or a string
        if isinstance(content, list) and len(content) > 0:
            return _normalise_tool_result(content)  # recurse
        if isinstance(content, str):
            return content
        # fallback just stringify
        return str(content)

    # ── 2.  List of MCP content blocks ─────────────────────────────
    if isinstance(result, list):
        if len(result) == 0:
            return ""
        # If the list contains a single dict with a "text" key, return that text
        if len(result) == 1 and isinstance(result[0], dict) and "text" in result[0]:
            return result[0]["text"]
        # Multiple items return a list of extracted text values
        extracted = []
        for item in result:
            if isinstance(item, dict) and "text" in item:
                extracted.append(item["text"])
            else:
                extracted.append(item)
        return extracted

    # ── 3.  Already a plain value ──────────────────────────────────
    return result


class MCPClientManager:
    """Manages connection to the mcp-server and exposes tools as callable methods."""

    def __init__(self):
        self._client: MultiServerMCPClient | None = None
        self._tools: dict[str, Any] = {}

    async def connect(self):
        self._client = MultiServerMCPClient(
            {
                "mcp-server": {
                    "transport": "http",
                    "url": settings.mcp_server_url,
                }
            }
        )
        if self._client is not None:
            tools = await self._client.get_tools()
            self._tools = {tool.name: tool for tool in tools}
            log.info(
                "MCP client connected - %d tools loaded: %s",
                len(self._tools),
                list(self._tools.keys()),
            )

    async def close(self):
        self._client = None
        self._tools.clear()

    async def call_tool(self, name: str, arguments: dict[str, Any], run_id: str = "") -> Any:
        """Call a tool by name, injecting run_id, and return a normalised result."""
        if not self._client:
            raise RuntimeError("MCP client not connected")
        if name not in self._tools:
            raise ValueError(f"Unknown tool: {name}. Available: {list(self._tools.keys())}")

        arguments = {**arguments, "run_id": run_id}

        tool = self._tools[name]
        try:
            result = await tool.ainvoke(arguments)
            return _normalise_tool_result(result)
        except Exception:
            log.exception("MCP tool call failed: %s", name)
            raise
