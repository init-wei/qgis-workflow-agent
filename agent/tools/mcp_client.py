#!/usr/bin/env python3
"""
MCP客户端工具模块（官方方式，极简实现）
使用 langchain-mcp-adapters 的 MultiServerMCPClient
"""

import os
import asyncio
import logging
from typing import Dict, Any, Optional, List

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools import BaseTool

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

QGIS_MCP_SERVER_URL = os.getenv("QGIS_MCP_SERVER_URL", "http://localhost:8002/mcp")


class QGISMCPClient:
    """极简 MCP 客户端（供 Agent 使用）"""

    def __init__(self, url: str):
        self.url = url
        self._client = MultiServerMCPClient(
            {
                "qgis": {
                    "transport": "http",
                    "url": self.url,
                }
            }
        )
        self._tools_cache: Optional[List[BaseTool]] = None
        self._tool_map_cache: Dict[str, BaseTool] = {}

    async def get_tools(self, refresh: bool = False) -> List[BaseTool]:
        if refresh or self._tools_cache is None:
            self._tools_cache = await self._client.get_tools()
            self._tool_map_cache = {tool.name: tool for tool in self._tools_cache}
        return list(self._tools_cache)

    async def list_tools(self) -> List[Dict[str, Any]]:
        tools = await self.get_tools()
        return [
            {
                "name": tool.name,
                "description": getattr(tool, "description", "") or "",
            }
            for tool in tools
        ]

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        await self.get_tools()
        tool = self._tool_map_cache.get(tool_name)
        if not tool:
            raise RuntimeError(f"MCP工具不存在: {tool_name}")
        if hasattr(tool, "ainvoke"):
            return await tool.ainvoke(arguments)
        return tool.invoke(arguments)

    async def capture_screenshot(
        self,
        path: str,
        width: int = 800,
        height: int = 600,
        timeout: int = 30,
    ) -> Any:
        _ = timeout
        return await self.call_tool(
            "capture_map_canvas",
            {"path": path, "width": width, "height": height},
        )

    async def close(self):
        await self._client.close()


_mcp_client: Optional[QGISMCPClient] = None
_client_lock = asyncio.Lock()


async def get_mcp_client() -> QGISMCPClient:
    global _mcp_client
    async with _client_lock:
        if _mcp_client is None:
            url = QGIS_MCP_SERVER_URL
            _mcp_client = QGISMCPClient(url=url)
        return _mcp_client


async def close_mcp_client():
    global _mcp_client
    async with _client_lock:
        if _mcp_client:
            await _mcp_client.close()
            _mcp_client = None


async def reset_mcp_client():
    await close_mcp_client()
