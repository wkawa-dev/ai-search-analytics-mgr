# asam - notes for agents working on this repo

- Python package in `src/asam`. Every user-facing operation is a function in `ops.py` registered with `@op(...)`;
  the CLI (`cli.py`) and MCP server (`mcp_server.py`) are generated from it. To add a capability: implement it in the
  domain module (`gsc.py`, `ga4.py`, `web.py`, `setup.py`, `indexing.py`, `analysis.py`), then add a typed, documented
  `@op` wrapper. The docstring is the MCP tool description and CLI help: keep it precise and example-rich.
- Mark mutating ops `write=True`; irreversible ones `destructive=True` and require `confirm`.
- Raise `AsamError(code, message, fix=...)` for anything a caller can act on; the `fix` must be concrete.
- Keep `analysis.py` functions pure (rows in, rows out) and tested.
- Never print to stdout outside `cli.py` (the MCP server speaks JSON-RPC over stdout).
- After changing ops, regenerate `docs/TOOLS.md` (see README "Development") and keep skills in `skills/` in sync.
- Checks: `.venv/bin/pytest -q`, `.venv/bin/ruff check src tests`, `claude plugin validate .`.
- `mcp` is pinned to 2.x (`mcp.server.mcpserver.MCPServer`; FastMCP no longer exists in 2.x).
