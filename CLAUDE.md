# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
uv sync                    # install dependencies
uv sync --extra dev        # install with dev/test dependencies
uv run pytest              # run all tests
uv run pytest tests/test_mcp_tools.py::test_ping  # run single test
uv run pytest --cov=src/qgis_mcp --cov-report=term-missing  # coverage
uv run ruff check .        # lint (includes isort import sorting via I rule)
uv run ruff check --fix .  # auto-fix lint issues (import sorting, etc.)
uv run ruff format --check .  # format check
uv run ruff format .       # auto-format
uv run pylint src/qgis_mcp/   # static analysis (MCP server only)
uv run mypy src/qgis_mcp/     # type check (MCP server only)
```

### Linting pipeline order
1. `ruff check --fix .` — lint + auto-fix (import sorting via isort, pyupgrade, etc.)
2. `ruff format .` — auto-format
3. `pylint src/qgis_mcp/` — static analysis
4. `mypy src/qgis_mcp/` — type checking

## Architecture

Two-process bridge between Claude and QGIS Desktop via MCP:

```
Claude / Claude Code → MCP Server (FastMCP, stdio) ↔ TCP socket (localhost:9876) ↔ QGIS Plugin (inside QGIS)
```

### MCP Server (`src/qgis_mcp/qgis_mcp_server.py`)

Runs as a separate Python process managed by `uv`. Contains:

- **`QgisMCPServer`** class — TCP socket client with reconnection, timeout, chunked response assembly
- **`get_qgis_connection()`** — module-level singleton managing a persistent connection
- **32 `@mcp.tool()` functions** — each calls `get_qgis_connection()`, `send_command("type", {params})`, returns JSON

### QGIS Plugin (`qgis_mcp_plugin/qgis_mcp_plugin.py`)

Runs inside QGIS's Python runtime. Contains:

- **`QgisMCPServer`** (different class, same name) — TCP socket server using `QTimer` polling (100ms)
- **`execute_command()`** with `handlers` dict mapping command strings to handler methods
- **30+ handler methods** calling PyQGIS APIs, organized by phase (introspection, filtering, styling, cartography)
- **Helpers:** `_find_layer_by_name()`, `_transform_to_wgs84()`, `_geometry_type_name()`, `_get_page_dimensions()`
- **UI:** `QgisMCPDockWidget`, `QgisMCPPlugin`

### Protocol

JSON over TCP. Request: `{"type": "command_name", "params": {...}}`. Response: `{"status": "success|error", "result": {...}}` or `{"status": "error", "message": "..."}`.

## Adding a New Tool

Every new tool requires changes in **BOTH** files:

1. **Plugin:** add handler method + register in `handlers` dict inside `execute_command()`
2. **MCP server:** add `@mcp.tool()` function calling `qgis.send_command("my_tool", {params})`
3. Update `tools.md` with parameters and return values

## Design Conventions

- All coordinate I/O uses **WGS84 (EPSG:4326)**. Plugin helpers handle reprojection internally.
- New tools use **layer name** (not ID) for lookup. `_find_layer_by_name()` raises with available names on failure.
- Memory layers from filtering/tracing are always WGS84.
- Plugin handlers accept `**kwargs` to tolerate extra JSON parameters.
- The plugin file cannot be imported outside QGIS. Only `_get_page_dimensions()` is pure Python.

## Testing

Tests cover the MCP server side only (no QGIS needed):

- Socket client tests mock `socket.socket`
- Tool function tests mock `get_qgis_connection()` and verify `send_command()` calls
- The `_qgis_connection` module global must be reset between tests (autouse fixture)
- Plugin handlers require a running QGIS instance and are not in the automated suite
