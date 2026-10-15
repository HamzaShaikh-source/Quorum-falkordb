import sys
import os
import asyncio

os.environ.setdefault("QUORUM_STANDALONE_MCP", "1")
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from mcp.server.mcpserver import MCPServer
from app.services import mcp_tools

mcp_server = MCPServer("quorum")

# Register tools directly on the server instance
mcp_server.tool()(mcp_tools.guarded(mcp_tools.remember_fact))
mcp_server.tool()(mcp_tools.guarded(mcp_tools.derive))
mcp_server.tool()(mcp_tools.guarded(mcp_tools.retrieve_context))
mcp_server.tool()(mcp_tools.guarded(mcp_tools.stage_action))
mcp_server.tool()(mcp_tools.guarded(mcp_tools.execute_action))
mcp_server.tool()(mcp_tools.guarded(mcp_tools.claim_handoff))
mcp_server.tool()(mcp_tools.guarded(mcp_tools.quarantine))
mcp_server.tool()(mcp_tools.guarded(mcp_tools.restore))
mcp_server.tool()(mcp_tools.guarded(mcp_tools.explain_blast_radius))
mcp_server.tool()(mcp_tools.guarded(mcp_tools.review_handoff))
mcp_server.tool()(mcp_tools.guarded(mcp_tools.get_map))
mcp_server.tool()(mcp_tools.guarded(mcp_tools.get_node))
mcp_server.tool()(mcp_tools.guarded(mcp_tools.find_path))
mcp_server.tool()(mcp_tools.guarded(mcp_tools.delta))
mcp_server.tool()(mcp_tools.guarded(mcp_tools.observe))


def main():
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Quorum Standalone MCP Server")
    parser.add_argument("--stdio", action="store_true", help="Run in stdio mode")
    parser.add_argument("--port", type=int, default=8001, help="Port for streamable HTTP transport (default: 8001)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host for streamable HTTP transport")
    args = parser.parse_args()

    if args.stdio:
        asyncio.run(mcp_server.run_stdio_async())
    else:
        from starlette.applications import Starlette
        import contextlib

        stream_app = mcp_server.streamable_http_app()
        sse_app = mcp_server.sse_app()

        @contextlib.asynccontextmanager
        async def lifespan(app):
            async with stream_app.router.lifespan_context(app):
                async with sse_app.router.lifespan_context(app):
                    yield

        app = Starlette(
            routes=[*stream_app.routes, *sse_app.routes],
            lifespan=lifespan,
        )
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
