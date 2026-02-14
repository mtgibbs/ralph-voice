"""Multi-server MCP client.

Connects to every server defined in mcp_config.json, aggregates their
tools, and dispatches tool calls to the correct session.
"""

import json
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPClient:
    def __init__(self, config_path: str = "mcp_config.json"):
        self.config_path = config_path
        self.exit_stack = AsyncExitStack()

        # tool_name -> ClientSession that owns it
        self._tool_sessions: dict[str, ClientSession] = {}

        # all aggregated MCP tool objects
        self.tools: list = []

    async def connect(self):
        """Connect to all MCP servers in the config and aggregate tools."""
        with open(self.config_path) as f:
            mcp_servers = json.load(f)["mcpServers"]

        for server_name, server_cfg in mcp_servers.items():
            env = server_cfg.get("env")
            server_params = StdioServerParameters(
                command=server_cfg["command"],
                args=server_cfg.get("args", []),
                env=env,
            )

            transport = await self.exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            read_stream, write_stream = transport
            session = await self.exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()

            response = await session.list_tools()
            for tool in response.tools:
                self._tool_sessions[tool.name] = session
                self.tools.append(tool)

            print(
                f"[mcp] Connected to '{server_name}' — "
                f"tools: {[t.name for t in response.tools]}"
            )

    async def call_tool(self, name: str, arguments: dict) -> object:
        """Dispatch a tool call to the session that registered it."""
        session = self._tool_sessions.get(name)
        if session is None:
            raise ValueError(f"Unknown tool: {name}")
        return await session.call_tool(name=name, arguments=arguments)

    async def close(self):
        await self.exit_stack.aclose()
