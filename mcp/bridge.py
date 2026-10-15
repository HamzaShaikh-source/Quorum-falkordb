"""
Quorum stdio bridge: lets clients that only speak stdio (Claude Desktop, older editors) use the shared hub.

Why this and not `server.py --stdio`: that one runs its own copy of the tools beside the API, so the API process and the
console can lag behind what it wrote. The bridge holds no state. It forwards every tool call to the hub over HTTPS/HTTP
with your token in the Authorization header, so there is one writer, one graph, one rate limit and one audit log.

  python mcp/bridge.py --url http://localhost:8000/mcp --token qrm_...
  (or QUORUM_MCP_URL / QUORUM_MCP_TOKEN in the environment, so the token never appears in the config file's args)
"""
import argparse
import asyncio
import os
import sys

import mcp_types as types
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared._httpx_utils import create_mcp_http_client


def _err(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


async def serve(url: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    http = create_mcp_http_client(headers=headers)
    async with Client(streamable_http_client(url, http_client=http), mode="legacy") as upstream:

        async def on_list_tools(ctx, params):
            return types.ListToolsResult(tools=(await upstream.list_tools()).tools)

        async def on_call_tool(ctx, params):
            try:
                return await upstream.call_tool(params.name, params.arguments or {})
            except Exception as e:  # the hub is down, the token was rejected, the rate limit hit...
                return _err(f"Quorum hub error: {type(e).__name__}: {e}")

        server = Server("quorum-bridge", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


def main() -> None:
    ap = argparse.ArgumentParser(description="stdio to Quorum hub bridge")
    ap.add_argument("--url", default=os.getenv("QUORUM_MCP_URL", "http://localhost:8000/mcp"))
    ap.add_argument("--token", default=os.getenv("QUORUM_MCP_TOKEN", ""))
    a = ap.parse_args()
    if not a.token:
        print("quorum-bridge: no token. Pass --token or set QUORUM_MCP_TOKEN.", file=sys.stderr)
        sys.exit(2)
    asyncio.run(serve(a.url, a.token))


if __name__ == "__main__":
    main()
