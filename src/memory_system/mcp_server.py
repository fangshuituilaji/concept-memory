"""MCP server exposing concept memory to coding agents."""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from pathlib import Path
from typing import Any

from .activation import SpreadingActivationSearch, store_relations
from .cache import ConceptCache
from .pipeline import analyze_path
from .relations import cooccurrence_relations, discover_relations_from_cards
from .storage import ConceptStore
from .web_server import get_init_state, set_init_state, start_web_server

_lock = threading.Lock()
_lifecycle_lock = threading.Lock()
_store: ConceptStore | None = None
_cache: ConceptCache | None = None
_project_root: str = ""
_database_path: str = ""
_web_server: Any | None = None
_web_url: str = ""
_scan_thread: threading.Thread | None = None


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


def _scan_worker(root: str) -> None:
    """Build concept cards off-thread, reporting per-file progress to the web UI."""

    try:

        def _progress(done: int, total: int, current_file: str) -> None:
            set_init_state("scanning", done=done, total=total, current_file=current_file)

        cards = analyze_path(
            root,
            cache=_cache,
            security_policy=None,
            progress_callback=_progress,
        )
        with _lock:
            assert _store is not None and _cache is not None
            _store.upsert_cards(cards)
        _discover_and_store_relations(cards)
        set_init_state("done", concept_count=len(cards), current_file="")
    except Exception as exc:  # the progress page is the user-visible surface
        set_init_state("error", message=str(exc))


def _discover_and_store_relations(cards: list) -> None:
    """Build concept relation edges after a scan; failures keep cards usable.

    Uses one online model call when DASHSCOPE_API_KEY is available, and a
    deterministic background-concept co-occurrence fallback otherwise.
    """

    assert _database_path
    set_init_state("relations", current_file="")
    try:
        cards_payload = [card.to_dict() for card in cards]
        if os.getenv("DASHSCOPE_API_KEY"):
            relations = discover_relations_from_cards(cards_payload)
        else:
            relations = cooccurrence_relations(cards_payload)
        if relations:
            with _lock:
                store_relations(_database_path, relations)
    except Exception:
        pass  # relations are additive; an empty graph degrades search gracefully


def scan_codebase(path: str, *, open_browser: bool = True) -> dict[str, Any]:
    """Start a background index build and open the concept network progress page.

    Returns immediately with the page URL; ``search_concepts`` reads whatever
    is already committed while the scan continues.
    """

    global _scan_thread, _web_server, _web_url
    root = str(Path(path).expanduser().resolve()) if path.strip() else str(Path.cwd().resolve())
    with _lifecycle_lock:
        if _scan_thread is not None and _scan_thread.is_alive():
            return {
                "status": "scanning",
                "project_root": _project_root,
                "progress": get_init_state(),
                "url": _web_url,
            }
        _init(root)
        assert _store is not None and _cache is not None
        if _web_server is None:
            _web_server = start_web_server(_database_path, scan_root=_project_root)
            _web_url = f"http://127.0.0.1:{_web_server.server_address[1]}"
        if open_browser:
            try:
                webbrowser.open(_web_url)
            except Exception:
                pass  # headless hosts still get the URL in the tool result
        set_init_state("scanning", done=0, total=0, current_file="")
        _scan_thread = threading.Thread(target=_scan_worker, args=(root,), daemon=True)
        _scan_thread.start()
    return {
        "status": "scanning",
        "project_root": root,
        "url": _web_url,
        "database": _database_path,
    }


def _active_scan() -> dict[str, Any] | None:
    """Progress payload when a background scan is still running."""

    if _scan_thread is not None and _scan_thread.is_alive():
        return {"status": "scanning", "progress": get_init_state(), "url": _web_url}
    return None


def search_concepts(query: str, limit: int = 10) -> dict[str, Any]:
    """Search stored concept cards, ordered as a task-oriented card sequence.

    Direct FTS matches come first; cards reached over the relation graph by
    spreading activation follow under ``related`` with the propagation path,
    so the agent gets directly-matched concepts plus their context in one
    ordered read instead of a flat hit list.
    """

    with _lock:
        store = _get_store()
        capped = max(1, min(limit, 50))
        results = store.search(query, limit=capped)
        payload: dict[str, Any] = {
            "query": query,
            "results": [_card_to_summary(result.card) for result in results],
        }
        related = _related_concepts(query, results, capped)
        if related:
            payload["related"] = related
        active = _active_scan()
        if active is not None:
            payload["scan"] = active
        return payload


def _related_concepts(query: str, results: list, limit: int) -> list[dict[str, Any]]:
    """Spreading-activation neighbours of the direct hits (hop >= 1)."""

    if _database_path == "":
        return []
    seeds = {
        result.card.id: max(0.4, 1.0 - 0.2 * position)
        for position, result in enumerate(results)
    }
    try:
        searcher = SpreadingActivationSearch(_database_path)
    except Exception:
        return []
    try:
        spread = (
            searcher.search_from_seeds(seeds)
            if seeds
            else searcher.search(query)
        )
    except Exception:
        return []
    finally:
        searcher.close()
    direct_ids = {result.card.id for result in results}
    related: list[dict[str, Any]] = []
    for item in spread:
        card_id = str(item.card.get("id", ""))
        if not card_id or card_id in direct_ids or item.hop == 0:
            continue
        related.append(
            {
                "card_id": card_id,
                "name": item.card.get("name", ""),
                "activation": item.activation,
                "hop": item.hop,
                "via": " → ".join(item.path),
            }
        )
        if len(related) >= limit:
            break
    return related


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
            description=(
                "Scan a local codebase directory and build the concept card index "
                "in the background. Opens the concept network page (live init "
                "progress) and returns its URL immediately."
            ),
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        mcp_types.Tool(
            name="search_concepts",
            description=(
                "Search concept cards by natural language query. Returns the "
                "directly matched cards first, then related concepts reached "
                "over the concept relation graph (with hop count and the "
                "propagation path), forming an ordered reading sequence."
            ),
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
