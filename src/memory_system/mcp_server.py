"""MCP server exposing concept memory to coding agents."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .cache import ConceptCache
from .pipeline import analyze_path
from .storage import ConceptStore

_lock = threading.Lock()
_store: ConceptStore | None = None
_cache: ConceptCache | None = None
_project_root: str = ""
_database_path: str = ""


def _init(project_root: str) -> None:
    """Open a per-project store and cache under the project root."""

    global _store, _cache, _project_root, _database_path
    if _store is not None and _project_root == project_root:
        return
    if _store is not None:
        _store.close()
        _store = None
    root = Path(project_root).expanduser().resolve()
    data_dir = root / ".concept-memory"
    data_dir.mkdir(parents=True, exist_ok=True)
    _project_root = str(root)
    _database_path = str(data_dir / "concepts.sqlite")
    _store = ConceptStore(_database_path)
    _store.open()
    _cache = ConceptCache(str(data_dir / "concept-cache.json"))


def _get_store() -> ConceptStore:
    if _store is None:
        raise RuntimeError("concept memory is not initialized; call scan_codebase first")
    return _store


def _card_to_summary(card: Any) -> dict[str, Any]:
    """Compact card representation for MCP tool output."""

    locations: list[dict[str, Any]] = []
    raw = card.metadata.get("evidence_locations", ())
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, dict):
                locations.append(
                    {
                        "file_path": item.get("file_path") or card.location.file_path,
                        "start_line": item.get("start_line"),
                        "end_line": item.get("end_line"),
                    }
                )
    if not locations:
        locations.append(
            {
                "file_path": card.location.file_path,
                "start_line": card.location.start_line,
                "end_line": card.location.end_line,
            }
        )
    symbols = tuple(
        str(item) for item in card.metadata.get("evidence_symbols", ()) if str(item).strip()
    )
    return {
        "card_id": card.id,
        "name": card.name,
        "definition": card.definition,
        "file_path": card.location.file_path,
        "symbols": list(symbols),
        "locations": locations,
        "source_excerpt": card.source_excerpt,
        "validation_status": card.metadata.get("validation_status"),
    }


def scan_codebase(path: str) -> dict[str, Any]:
    """Scan a directory, generate concept cards, and store them locally."""

    with _lock:
        _init(path)
        assert _store is not None and _cache is not None
        cards = analyze_path(
            path,
            cache=_cache,
            security_policy=None,
        )
        _store.upsert_cards(cards)
        return {
            "status": "ok",
            "project_root": _project_root,
            "concept_count": len(cards),
            "database": _database_path,
        }


def search_concepts(query: str, limit: int = 10) -> dict[str, Any]:
    """Search stored concept cards by query text."""

    with _lock:
        store = _get_store()
        results = store.search(query, limit=max(1, min(limit, 50)))
        return {
            "query": query,
            "results": [_card_to_summary(result.card) for result in results],
        }


def get_card(card_id: str) -> dict[str, Any]:
    """Return the full concept card with all evidence by ID."""

    with _lock:
        card = _get_store().get(card_id)
        if card is None:
            return {"error": f"card {card_id!r} not found"}
        return card.to_dict()


def build_server() -> Any:
    """Build the MCP server instance (requires the mcp package)."""

    import mcp.types as mcp_types
    from mcp.server.lowlevel import Server

    server: Any = Server("concept-memory")

    _TOOLS = [
        mcp_types.Tool(
            name="scan_codebase",
            description="Scan a local codebase directory and build concept card index.",
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        mcp_types.Tool(
            name="search_concepts",
            description="Search concept cards by natural language query.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        ),
        mcp_types.Tool(
            name="get_card",
            description="Get the full concept card and source evidence by card ID.",
            inputSchema={
                "type": "object",
                "properties": {"card_id": {"type": "string"}},
                "required": ["card_id"],
            },
        ),
    ]

    async def _list_tools(ctx: Any, params: Any) -> Any:
        return mcp_types.ListToolsResult(tools=_TOOLS)

    async def _call_tool(ctx: Any, params: Any) -> Any:
        import asyncio

        name = params.name
        arguments = params.arguments or {}

        if name == "scan_codebase":
            result = await asyncio.to_thread(scan_codebase, arguments.get("path", ""))
        elif name == "search_concepts":
            result = await asyncio.to_thread(
                search_concepts,
                arguments.get("query", ""),
                arguments.get("limit", 10),
            )
        elif name == "get_card":
            result = await asyncio.to_thread(get_card, arguments.get("card_id", ""))
        else:
            raise ValueError(f"unknown tool: {name}")
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        )

    from mcp.types import CallToolRequestParams, PaginatedRequestParams

    server.add_request_handler("tools/list", PaginatedRequestParams, _list_tools)
    server.add_request_handler("tools/call", CallToolRequestParams, _call_tool)

    return server


def main() -> None:
    """Start the MCP server over stdio."""

    import anyio
    from mcp.server.stdio import stdio_server

    server = build_server()

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    anyio.run(_run)


if __name__ == "__main__":
    main()
