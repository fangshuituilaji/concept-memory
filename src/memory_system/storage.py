"""SQLite/FTS5 persistence for concept cards."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import ConceptCard


@dataclass(frozen=True, slots=True)
class ConceptSearchResult:
    """A card plus the evidence used to explain why it matched."""

    card: ConceptCard
    rank: float
    matched_fields: tuple[str, ...]
    explanation: str

    @property
    def file_path(self) -> str:
        return self.card.location.file_path

    @property
    def score(self) -> float:
        """Expose the backend rank for lower-level inspection."""

        return self.rank

class ConceptStore:
    """A small local store whose JSON card row is the source of truth."""

    def __init__(self, database_path: str | Path):
        self.database_path = str(Path(database_path).expanduser())
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> "ConceptStore":
        self.open()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def open(self) -> None:
        if self._connection is not None:
            return
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        # The MCP server shares one store across the tool-call and background
        # scan threads; every access is serialized behind mcp_server._lock.
        connection = sqlite3.connect(self.database_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS concept_cards (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    definition TEXT NOT NULL,
                    card_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS concept_search USING fts5(
                    id UNINDEXED,
                    name,
                    definition,
                    background
                )
                """
            )
            connection.commit()
        except sqlite3.OperationalError as exc:
            connection.close()
            raise RuntimeError("SQLite FTS5 is required for ConceptStore") from exc
        self._connection = connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def upsert_cards(self, cards: Iterable[ConceptCard]) -> None:
        connection = self._require_connection()
        rows = list(cards)
        with connection:
            for card in rows:
                connection.execute(
                    """
                    INSERT INTO concept_cards(id, kind, name, definition, card_json)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        kind = excluded.kind,
                        name = excluded.name,
                        definition = excluded.definition,
                        card_json = excluded.card_json,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        card.id,
                        card.kind.value,
                        card.name,
                        card.definition,
                        json.dumps(card.to_dict(), ensure_ascii=False, sort_keys=True),
                    ),
                )
                connection.execute("DELETE FROM concept_search WHERE id = ?", (card.id,))
                connection.execute(
                    "INSERT INTO concept_search(id, name, definition, background) VALUES (?, ?, ?, ?)",
                    (
                        card.id,
                        card.name,
                        card.definition,
                        " ".join((card.background, *card.background_concepts)),
                    ),
                )

    def get(self, card_id: str) -> ConceptCard | None:
        row = self._require_connection().execute(
            "SELECT card_json FROM concept_cards WHERE id = ?", (card_id,)
        ).fetchone()
        return ConceptCard.from_dict(json.loads(row["card_json"])) if row else None

    def all_cards(self, *, limit: int | None = None) -> list[ConceptCard]:
        """Return all stored cards in stable ID order for semantic selection.

        The JSON card row remains the source of truth.  This API deliberately
        does not expose FTS ranking: a semantic selector needs the complete,
        deterministic card context rather than a lexical candidate shortlist.
        """

        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1 when provided")
        sql = "SELECT card_json FROM concept_cards ORDER BY id"
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        rows = self._require_connection().execute(sql, params).fetchall()
        return [ConceptCard.from_dict(json.loads(row["card_json"])) for row in rows]

    list_cards = all_cards

    def cards_by_file(self, file_path: str) -> list[ConceptCard]:
        """Cards anchored in one file, in stable ID order.

        Incremental rescans reuse these cards verbatim when a file's content
        has not changed, and match against them (to inherit card IDs) when it
        has; see :mod:`memory_system.incremental`.
        """

        rows = self._require_connection().execute(
            """
            SELECT card_json FROM concept_cards
            WHERE json_extract(card_json, '$.location.file_path') = ?
            ORDER BY id
            """,
            (file_path,),
        ).fetchall()
        return [ConceptCard.from_dict(json.loads(row["card_json"])) for row in rows]

    def stored_file_paths(self) -> set[str]:
        """Root-relative paths that currently have at least one stored card."""

        rows = self._require_connection().execute(
            """
            SELECT DISTINCT json_extract(card_json, '$.location.file_path') AS path
            FROM concept_cards
            """
        ).fetchall()
        return {row["path"] for row in rows if row["path"]}

    def search(self, query: str, *, limit: int = 20) -> list[ConceptSearchResult]:
        if not query.strip():
            return []
        if limit < 1:
            raise ValueError("limit must be at least 1")
        connection = self._require_connection()
        fts_query = _fts_query(query)
        try:
            rows = connection.execute(
                """
                SELECT concept_search.id, bm25(concept_search) AS rank,
                       concept_search.name, concept_search.definition,
                       concept_search.background, concept_cards.card_json
                FROM concept_search
                JOIN concept_cards ON concept_cards.id = concept_search.id
                WHERE concept_search MATCH ?
                ORDER BY rank, concept_search.id
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise ValueError(f"Invalid full-text query: {query!r}") from exc

        if not rows:
            # unicode61 may not segment every CJK query as users expect. Keep
            # FTS as the primary index, then use a deterministic substring
            # fallback so short Chinese concepts remain searchable locally.
            like_query = f"%{query.strip()}%"
            rows = connection.execute(
                """
                SELECT concept_cards.id, 0.0 AS rank,
                       concept_cards.name, concept_cards.definition,
                       json_extract(concept_cards.card_json, '$.background') AS background,
                       concept_cards.card_json
                FROM concept_cards
                WHERE name LIKE ? OR definition LIKE ?
                   OR json_extract(concept_cards.card_json, '$.background') LIKE ?
                   OR json_extract(concept_cards.card_json, '$.background_concepts') LIKE ?
                ORDER BY id
                LIMIT ?
                """,
                (like_query, like_query, like_query, like_query, limit),
            ).fetchall()

        return [
            ConceptSearchResult(
                card=ConceptCard.from_dict(json.loads(row["card_json"])),
                rank=float(row["rank"]),
                matched_fields=_matched_fields(query, row),
                explanation=_explanation(query, row),
            )
            for row in rows
        ]

    def search_any(self, query: str, *, limit: int = 20) -> list[ConceptSearchResult]:
        """Recall candidates matching ANY term, as a reranking pool.

        Unlike :meth:`search` (which ANDs every term), this widens recall for
        the model reranker: a pool that missed the right card can never be
        fixed downstream.
        """

        terms = [term.strip() for term in query.split() if term.strip()]
        if not terms:
            return []
        if limit < 1:
            raise ValueError("limit must be at least 1")
        connection = self._require_connection()
        escaped = [term.replace('"', '""') for term in terms]
        fts_query = " OR ".join(f'"{term}"' for term in escaped)
        try:
            rows = connection.execute(
                """
                SELECT concept_search.id, bm25(concept_search) AS rank,
                       concept_search.name, concept_search.definition,
                       concept_search.background, concept_cards.card_json
                FROM concept_search
                JOIN concept_cards ON concept_cards.id = concept_search.id
                WHERE concept_search MATCH ?
                ORDER BY rank, concept_search.id
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise ValueError(f"Invalid full-text query: {query!r}") from exc

        if not rows:
            # CJK substring fallback: any single term matching is enough here,
            # because recall (not precision) is this method's job.
            clauses = []
            params: list[str] = []
            for term in terms:
                clauses.append(
                    "name LIKE ? OR definition LIKE ?"
                    " OR json_extract(concept_cards.card_json, '$.background') LIKE ?"
                    " OR json_extract(concept_cards.card_json, '$.background_concepts') LIKE ?"
                )
                like = f"%{term}%"
                params.extend([like, like, like, like])
            rows = connection.execute(
                f"""
                SELECT concept_cards.id, 0.0 AS rank,
                       concept_cards.name, concept_cards.definition,
                       json_extract(concept_cards.card_json, '$.background') AS background,
                       concept_cards.card_json
                FROM concept_cards
                WHERE {' OR '.join(clauses)}
                ORDER BY id
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()

        return [
            ConceptSearchResult(
                card=ConceptCard.from_dict(json.loads(row["card_json"])),
                rank=float(row["rank"]),
                matched_fields=(),
                explanation="候选池召回，最终顺序由 qwen-flash 重排决定。",
            )
            for row in rows
        ]

    def rebuild_index(self) -> None:
        connection = self._require_connection()
        with connection:
            connection.execute("DELETE FROM concept_search")
            rows = connection.execute(
                "SELECT id, name, definition, card_json FROM concept_cards ORDER BY id"
            ).fetchall()
            for row in rows:
                card = ConceptCard.from_dict(json.loads(row["card_json"]))
                connection.execute(
                    "INSERT INTO concept_search(id, name, definition, background) VALUES (?, ?, ?, ?)",
                    (card.id, card.name, card.definition, " ".join((card.background, *card.background_concepts))),
                )

    def prune_not_in(self, keep_ids: set[str]) -> int:
        """Delete cards the latest scan did not produce (gone or renamed files).

        The store would otherwise accumulate orphaned cards forever and
        inflate the concept count.  Usage edges touching pruned cards are
        removed too; returns the number of deleted cards.
        """

        connection = self._require_connection()
        with connection:
            rows = connection.execute("SELECT id FROM concept_cards").fetchall()
            stale = [row["id"] for row in rows if row["id"] not in keep_ids]
            for card_id in stale:
                connection.execute("DELETE FROM concept_cards WHERE id = ?", (card_id,))
                connection.execute("DELETE FROM concept_search WHERE id = ?", (card_id,))
                try:
                    connection.execute(
                        "DELETE FROM concept_usage_edges WHERE source_id = ? OR target_id = ?",
                        (card_id, card_id),
                    )
                except sqlite3.OperationalError:
                    pass  # usage table not created yet
        return len(stale)

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self.open()
        assert self._connection is not None
        return self._connection


def _fts_query(query: str) -> str:
    terms = [term.replace('"', '""') for term in query.split() if term.strip()]
    return " AND ".join(f'"{term}"' for term in terms) or '""'


def _matched_fields(query: str, row: sqlite3.Row) -> tuple[str, ...]:
    normalized = query.casefold()
    fields: list[str] = []
    for field in ("name", "definition", "background"):
        value = str(row[field]).casefold()
        if normalized in value or any(part.casefold() in value for part in query.split()):
            fields.append(field)
    if not row["background"]:
        try:
            card_data = json.loads(row["card_json"])
            background = " ".join(card_data.get("background_concepts", [])).casefold()
        except (TypeError, json.JSONDecodeError):
            background = ""
        if normalized in background or any(part.casefold() in background for part in query.split()):
            fields.append("background")
    return tuple(dict.fromkeys(fields))


def _explanation(query: str, row: sqlite3.Row) -> str:
    fields = _matched_fields(query, row)
    if not fields:
        return "全文索引命中该卡片，但未在单字段字符串中找到完整查询短语。"
    labels = {"name": "概念名称", "definition": "概念定义", "background": "概念背景"}
    return "；".join(labels[field] for field in fields) + "包含查询词。"

