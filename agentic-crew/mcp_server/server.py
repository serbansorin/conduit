"""MCP server exposing the crew's agents to OpenCode and other MCP clients.

Run with: python3 -m mcp_server.server
Or via:   agentic-crew mcp
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

__all__ = ["CrewMcpServer", "run_mcp_server"]

log = logging.getLogger(__name__)

CREW_URL = os.environ.get("AGENTIC_CREW_URL", "http://127.0.0.1:8787")
CREW_KEY = os.environ.get("AGENTIC_CREW_API_KEY", "")


class CrewMcpServer:
    """MCP server that proxies tool calls to the crew's HTTP API."""

    def __init__(self, crew_url: str = CREW_URL, api_key: str = CREW_KEY) -> None:
        """Create the server.

        Args:
            crew_url: Base URL of the crew service.
            api_key: Bearer token for the crew API.
        """
        self.crew_url = crew_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(timeout=120.0)

    def _headers(self) -> Dict[str, str]:
        """Build request headers.

        Returns:
            The headers.
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def list_agents(self) -> List[Dict[str, Any]]:
        """List all available agents.

        Returns:
            The agent catalogue.
        """
        response = self._client.get(f"{self.crew_url}/agents", headers=self._headers())
        response.raise_for_status()
        return response.json()

    def run(self, question: str, agent: Optional[str] = None) -> Dict[str, Any]:
        """Ask the crew a question.

        Args:
            question: The question to ask.
            agent: Optional agent name to force.

        Returns:
            The orchestrator result.
        """
        body: Dict[str, Any] = {"question": question}
        if agent:
            body["agent"] = agent
        response = self._client.post(
            f"{self.crew_url}/run", json=body, headers=self._headers()
        )
        response.raise_for_status()
        return response.json()

    def ingest(self, agent: str, force: bool = False) -> Dict[str, Any]:
        """Ingest an agent's knowledge base.

        Args:
            agent: The agent name.
            force: Whether to force re-ingestion.

        Returns:
            The ingest report.
        """
        response = self._client.post(
            f"{self.crew_url}/agents/{agent}/ingest",
            json={"force": force},
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    def health(self) -> Dict[str, Any]:
        """Check the crew's health.

        Returns:
            The health status.
        """
        response = self._client.get(f"{self.crew_url}/health", headers=self._headers())
        response.raise_for_status()
        return response.json()


def run_mcp_server() -> None:
    """Run the MCP server using stdio transport."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import Tool, TextContent
    except ImportError:
        print("MCP SDK not installed. Install with: pip install mcp")
        print("Or use the HTTP API directly: agentic-crew serve")
        raise SystemExit(1)

    import asyncio

    server = Server("agentic-crew")
    crew = CrewMcpServer()

    @server.list_tools()
    async def list_tools() -> List[Tool]:
        return [
            Tool(
                name="crew_list_agents",
                description="List all available agents in the crew",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="crew_ask",
                description="Ask the agent crew a question. The orchestrator will route it to the right specialist.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The question to ask"},
                        "agent": {"type": "string", "description": "Optional agent name to force"},
                    },
                    "required": ["question"],
                },
            ),
            Tool(
                name="crew_ingest",
                description="Ingest an agent's knowledge base from its configured sources",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string", "description": "The agent name"},
                        "force": {"type": "boolean", "description": "Force re-ingestion", "default": False},
                    },
                    "required": ["agent"],
                },
            ),
            Tool(
                name="crew_health",
                description="Check the crew's health status",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
        if name == "crew_list_agents":
            result = crew.list_agents()
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        if name == "crew_ask":
            result = crew.run(
                question=arguments["question"],
                agent=arguments.get("agent"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        if name == "crew_ingest":
            result = crew.ingest(
                agent=arguments["agent"],
                force=arguments.get("force", False),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        if name == "crew_health":
            result = crew.health()
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        raise ValueError(f"Unknown tool: {name}")

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(main())


if __name__ == "__main__":
    run_mcp_server()
