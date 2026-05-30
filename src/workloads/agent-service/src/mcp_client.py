"""
MCP client wrapper using langchain-mcp-adapters.

All tool results are normalised to plain Python types (str, dict, list of dicts)
before being returned to the caller.  No LangChain wrappers escape.
"""

from __future__ import annotations

import json
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
    - JSON strings inside text blocks (parsed into dicts)
    """

    # 1.  ToolMessage (has .content)
    if hasattr(result, "content"):
        content = result.content
        if isinstance(content, list) and len(content) > 0:
            return _normalise_tool_result(content)  # recurse
        if isinstance(content, str):
            return _try_parse_json(content)
        return str(content)

    # 2.  List of MCP content blocks
    if isinstance(result, list):
        if len(result) == 0:
            return ""
        # Single item list with a dict containing "text"
        if len(result) == 1 and isinstance(result[0], dict) and "text" in result[0]:
            return _try_parse_json(result[0]["text"])
        # Multiple items return a list of extracted text values
        extracted = []
        for item in result:
            if isinstance(item, dict) and "text" in item:
                extracted.append(_try_parse_json(item["text"]))
            else:
                extracted.append(item)
        return extracted

    # 3.  Plain string try to parse as JSON
    if isinstance(result, str):
        return _try_parse_json(result)

    # 4.  Already a plain dict or other value
    return result


def _try_parse_json(text: str) -> Any:
    """Attempt to parse a string as JSON. If successful and returns a dict, return it.
    Otherwise return the original string."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return text


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
