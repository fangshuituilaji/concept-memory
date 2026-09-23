"""MCP server exposing concept memory to coding agents."""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from .activation import SpreadingActivationSearch, record_usage
from . import __version__ as _VERSION
from .cache import ConceptCache
from .incremental import incremental_scan
from .retrieval import QwenFlashRetriever, RetrievalConfig
from .storage import ConceptStore
from .web_server import (
    get_init_state,
    mark_agent_seen,
    record_search_event,
    set_init_state,
    start_web_server,
)

_lock = threading.Lock()
_lifecycle_lock = threading.Lock()
_store: ConceptStore | None = None
_cache: ConceptCache | None = None
_project_root: str = ""
_database_path: str = ""
_web_server: Any | None = None
_web_url: str = ""
_scan_thread: threading.Thread | None = None
_retriever: Any | None = None

# Readiness gate: how long search_concepts waits for an in-flight scan.
_SCAN_WAIT_TIMEOUT_SECONDS = 1800.0
_SCAN_NOTE = (
    "background scan in progress; search_concepts automatically waits for "
    "it to complete - no need to sleep or poll manually"
)


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


def _definition_preview(definition: str, max_chars: int = 120) -> str:
    """First meaningful line of a definition, truncated for relevance judgment."""

    for line in definition.splitlines():
        text = line.strip()
        if text:
            return text[:max_chars] + ("…" if len(text) > max_chars else "")
    return ""


def _card_to_summary(card: Any) -> dict[str, Any]:
    """Thin catalog entry: enough to pick a card, not enough to read it.

    Full cards (complete definition plus ``source_excerpt``) are only
    returned by card mode, so actually reading several cards together stays
    a deliberate act that feeds the usage graph.
    """

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
        "definition_preview": _definition_preview(card.definition),
        "file_path": card.location.file_path,
        "symbols": list(symbols[:5]),
        "locations": locations[:3],
    }


def _provided(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return bool(value)


def _scan_worker(root: str) -> None:
    """Build concept cards off-thread, reporting per-file progress to the web UI."""

    try:

        def _progress(done: int, total: int, current_file: str) -> None:
            set_init_state("scanning", done=done, total=total, current_file=current_file)

        assert _store is not None and _cache is not None
        result = incremental_scan(
            root,
            store=_store,
            database_path=_database_path,
            cache=_cache,
            progress_callback=_progress,
            store_lock=_lock,
        )
        set_init_state(
            "done",
            concept_count=len(result.cards),
            pruned=result.pruned_cards,
            current_file="",
            changed_files=list(result.changed_files),
            skipped_files=result.skipped_files,
        )
    except Exception as exc:  # the progress page is the user-visible surface
        set_init_state("error", message=str(exc))


def scan_codebase(
    path: str, *, open_browser: bool = True, from_agent: bool = True
) -> dict[str, Any]:
    """Start a background index build and open the concept network progress page.

    Returns immediately with the page URL; ``search_concepts`` waits for
    the scan to finish instead of reading a half-built index.  ``from_agent``
    is False only when the scan was triggered from the web page itself,
    which must not light up the agent-connection button.
    """

    global _scan_thread, _web_server, _web_url
    if from_agent:
        mark_agent_seen()
    root = str(Path(path).expanduser().resolve()) if path.strip() else str(Path.cwd().resolve())
    with _lifecycle_lock:
        if _scan_thread is not None and _scan_thread.is_alive():
            return {
                "status": "scanning",
                "project_root": _project_root,
                "progress": get_init_state(),
                "url": _web_url,
                "note": _SCAN_NOTE,
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
        "note": _SCAN_NOTE,
    }


def _active_scan() -> dict[str, Any] | None:
    """Progress payload when a background scan is still running."""

    if _scan_thread is not None and _scan_thread.is_alive():
        return {"status": "scanning", "progress": get_init_state(), "url": _web_url}
    return None


def _wait_for_scan(timeout: float | None = None) -> None:
    """Readiness gate: block until any in-flight background scan finishes.

    Searching while a scan runs would answer from a half-built index, so
    the tool makes the caller wait instead of racing the scan worker.
    """

    if timeout is None:
        timeout = _SCAN_WAIT_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout
    while True:
        with _lifecycle_lock:
            thread = _scan_thread
        if thread is None or not thread.is_alive():
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                "concept index is still building; retry search_concepts "
                "later. progress: "
                + json.dumps(get_init_state(), ensure_ascii=False)
            )
        thread.join(timeout=min(remaining, 1.0))


def _get_retriever() -> Any:
    """Online-only retriever; tests may inject a stand-in via _retriever."""

    global _retriever
    if _retriever is None:
        _retriever = QwenFlashRetriever(RetrievalConfig())
    return _retriever


_NEXT_STEP = (
    "call search_concepts again with card_ids=[...] (several at once) to "
    "read the full cards: complete definition plus line-indexed source "
    "excerpts"
)


def search_concepts(
    query: str | None = None,
    limit: int = 10,
    card_ids: str | list[str] | None = None,
) -> dict[str, Any]:
    """One tool, two steps: locate cards by query, then read them by ID.

    Query mode (``query``): Qwen-Flash reads the full catalog of card names
    and definitions and returns the relevant card IDs; complete cards are
    then loaded from the store by ID.  There is no lexical recall step that
    could drop a relevant card before the model sees it, and when the model
    is unreachable the search fails after retries instead of silently
    degrading.  Results are thin catalog entries (card_id, name, one-line
    definition preview, file and line ranges) - enough to choose cards, not
    to read them.  Cards reached over the relation graph by spreading
    activation follow under ``related`` with the propagation path.

    Card mode (``card_ids``): the model's way of reading code through
    memory.  One call may fetch several cards; each fetched pair then gets
    a co-usage edge incremented, so the concept web reflects what real
    sessions used together and future searches spread activation over
    those links.
    """

    mark_agent_seen()
    has_query = _provided(query)
    has_cards = _provided(card_ids)
    if has_query == has_cards:
        return {
            "error": (
                "pass exactly one of query (locate cards) or card_ids "
                "(read full cards)"
            )
        }
    try:
        _wait_for_scan()
    except RuntimeError as exc:
        return {"error": str(exc)}
    if has_cards:
        return _fetch_cards(card_ids)  # type: ignore[arg-type]
    return _search_by_query(query if isinstance(query, str) else str(query), limit)


def _search_by_query(query: str, limit: int) -> dict[str, Any]:
    """Query mode: online ranking plus spreading-activation neighbours."""

    with _lock:
        store = _get_store()
        if not store.all_cards():
            return {
                "error": (
                    "concept index is empty for this project; run "
                    "scan_codebase once and wait for the scan to finish "
                    "before searching"
                )
            }
        capped = max(1, min(limit, 50))
        results = _get_retriever().search(store, query, capped)
        payload: dict[str, Any] = {
            "query": query,
            "results": [_card_to_summary(result.card) for result in results],
        }
        related = _related_concepts(query, results, capped)
        if related:
            payload["related"] = related
        payload["next_step"] = _NEXT_STEP
        record_search_event(
            query,
            [result.card.id for result in results],
            [item["card_id"] for item in related],
        )
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
        location = item.card.get("location") or {}
        related.append(
            {
                "card_id": card_id,
                "name": item.card.get("name", ""),
                "file_path": location.get("file_path", ""),
                "activation": item.activation,
                "hop": item.hop,
                "via": " → ".join(item.path),
            }
        )
        if len(related) >= limit:
            break
    return related


def _fetch_cards(card_ids: str | list[str]) -> dict[str, Any]:
    """Card mode: full cards by ID; multi-card fetches record real usage."""

    with _lock:
        store = _get_store()
        if isinstance(card_ids, str):
            text = card_ids.strip()
            if text.startswith("["):
                # some clients stringify array arguments; accept JSON too
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
                card_ids = (
                    [str(item) for item in parsed]
                    if isinstance(parsed, list) and parsed
                    else [text]
                )
            else:
                card_ids = [card_ids]
        cards: list[dict[str, Any]] = []
        missing: list[str] = []
        for card_id in card_ids:
            card = store.get(card_id)
            if card is None:
                missing.append(card_id)
            else:
                cards.append(card.to_dict())
        if not cards:
            return {"error": f"cards {missing!r} not found"}
        if len(cards) >= 2 and _database_path:
            record_usage(_database_path, [card["id"] for card in cards])
        payload: dict[str, Any] = {"cards": cards}
        if missing:
            payload["not_found"] = missing
        return payload


def build_server() -> Any:
    """Build the MCP server instance (requires the mcp package)."""

    import mcp.types as mcp_types
    from mcp.server.lowlevel import Server

    # version 会出现在 MCP initialize 返回的 serverInfo 里，
    # 用户和 agent 靠它确认自己装的是哪一版。
    server: Any = Server("concept-memory", version=_VERSION)

    _TOOLS = [
        mcp_types.Tool(
            name="scan_codebase",
            description=(
                "Build the concept memory index for a project directory. "
                "Run this once per project before searching: it reads every "
                "source file once so that later code location needs no grep "
                "and no whole-file reads. Rescans are incremental and cheap: "
                "only files whose content changed are re-read and re-"
                "synthesized, so calling it again after edits is fast and "
                "keeps accumulated card history. Returns immediately and "
                "opens a live progress page; search_concepts automatically "
                "waits for an in-progress scan, so you can call it right "
                "away without sleeping or polling."
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
                "PREFERRED over Grep/ripgrep when you need to find where "
                "code lives; one tool, two steps. Step 1: pass query "
                "(natural-language concept or keyword, e.g. '使用边', "
                "'retry logic') to list matching cards - card_id, name, "
                "one-line definition preview, file and exact line numbers "
                "- followed by related cards reached over the real-usage "
                "graph. Step 2: pass card_ids (several together) to read "
                "the full cards - complete definition plus line-indexed "
                "source excerpts, tens of lines instead of a whole file. "
                "Multi-card fetches record co-usage edges that improve "
                "future searches. If a scan is still running, the call "
                "waits for it to finish instead of searching a half-built "
                "index. Use Read only for the few extra lines "
                "the cards do not already cover."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language concept or keyword to locate cards.",
                    },
                    "limit": {"type": "integer", "default": 10},
                    "card_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Card IDs from a previous query, fetched together in full.",
                    },
                },
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
            card_ids = arguments.get("card_ids")
            if card_ids is None:
                card_ids = arguments.get("card_id")
            result = await asyncio.to_thread(
                search_concepts,
                arguments.get("query"),
                arguments.get("limit", 10),
                card_ids,
            )
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
