"""MCP server (stdio) exposing every registry op as a tool: `gsc_query`, `ga4_report`, `analyze_report`, ..."""

import functools
import inspect
import json

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .errors import AsamError
from .guide import GUIDES
from .ops import REGISTRY, Op

INSTRUCTIONS = GUIDES["overview"]


def _wrap(o: Op):
    fn = o.fn

    @functools.wraps(fn)
    async def tool(**kwargs):
        try:
            return await anyio.to_thread.run_sync(functools.partial(fn, **kwargs))
        except AsamError as e:
            raise ToolError(json.dumps({"error": e.to_dict()}, default=str)) from e
        except Exception as e:  # surface the real message instead of a generic crash
            raise ToolError(json.dumps({"error": {"code": "UNEXPECTED", "message": f"{type(e).__name__}: {e}",
                                                  "fix": "Run doctor_run; if it persists this is a bug."}})) from e

    tool.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
    return tool


def build_server() -> MCPServer:
    server = MCPServer(name="asam", title="AI Search Analytics Manager", instructions=INSTRUCTIONS,
                       version=__version__, website_url="https://github.com/wkawa-dev/ai-search-analytics-mgr")
    for o in REGISTRY.values():
        if o.cli_only:
            continue
        server.add_tool(
            _wrap(o), name=o.tool_name, description=o.doc,
            annotations=ToolAnnotations(readOnlyHint=not o.write, destructiveHint=o.destructive,
                                        openWorldHint=True, idempotentHint=not o.write),
            structured_output=False,
        )
    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
