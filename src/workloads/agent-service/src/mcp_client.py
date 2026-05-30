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
    - ToolMessage objects (prioritises .artifact over .content)
    - Plain lists of {"type":"text","text":"..."} dicts
    - Complex dicts (order objects, etc.) preserved as-is
    - MCP envelope {"result": <payload>}
    - Already-plain strings / dicts / lists
    - JSON strings inside text blocks (parsed into dicts)
    """

    # 1.  ToolMessage - check for .artifact first (holds structuredContent from MCP)
    if hasattr(result, "content") and hasattr(result, "artifact"):
        if result.artifact is not None:
            return _normalise_tool_result(result.artifact)
        if result.content:
            return _normalise_tool_result(result.content)
        return ""

    # 2.  List of MCP content blocks or other items
    if isinstance(result, list):
        if len(result) == 0:
            return []
        extracted = []
        for item in result:
            if isinstance(item, dict):
                keys = set(item.keys())
                # Pure MCP text block - extract the text
                if keys == {"type", "text"} or keys == {"text"}:
                    extracted.append(_try_parse_json(item["text"]))
                else:
                    # Complex dict (e.g. order object) - keep as-is
                    extracted.append(item)
            else:
                extracted.append(item)
        return extracted

    # 3.  MCP envelope: {"result": <payload>}
    if isinstance(result, dict) and "result" in result:
        if set(result.keys()) == {"result"}:
            return _normalise_tool_result(result["result"])

    # 4.  Plain dict - keep as-is, don't recurse (preserves order objects)
    if isinstance(result, dict):
        return result

    # 5.  Plain string - try to parse as JSON
    if isinstance(result, str):
        return _try_parse_json(result)

    # 6.  Anything else
    return result


def _try_parse_json(text: str) -> Any:
    """Attempt to parse a string as JSON.  Return parsed dict or original string."""
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(stripped)
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
            normalised = _normalise_tool_result(result)

            # Debug: log what we got vs what we returned
            if name == "get_recent_orders":
                log.info(
                    "DEBUG %s: raw_type=%s, normalised_type=%s, normalised_len=%s",
                    name,
                    type(result).__name__,
                    type(normalised).__name__,
                    len(normalised) if isinstance(normalised, list) else "N/A",
                )
                if isinstance(normalised, list) and len(normalised) > 0:
                    log.info(
                        "DEBUG %s: first_item_keys=%s",
                        name,
                        list(normalised[0].keys()) if isinstance(normalised[0], dict) else "N/A",
                    )

            return normalised
        except Exception:
            log.exception("MCP tool call failed: %s", name)
            raise
