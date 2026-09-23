"""Spreading activation retrieval over the real-usage concept graph.

Edges are never invented by a model.  The only edge source is recorded
usage: every ``search_concepts`` card-mode call that fetches several cards
at once adds one co-usage link between each fetched pair, and the link's
weight is how many times it has happened.  Retrieval spreads activation
over those links so future searches surface what past sessions actually
used together.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ActivationResult:
    """A concept card reached by spreading activation."""

    card: dict          # full card JSON
    activation: float   # final activation score
    hop: int            # how many hops from seed
    path: list[str]     # concept names along the propagation path


class SpreadingActivationSearch:
    """Retrieve concepts via keyword seed matching + graph propagation."""

    def __init__(
        self,
        database_path: str,
        *,
        decay: float = 0.5,
        threshold: float = 0.05,
        max_hops: int = 2,
        max_results: int = 20,
    ):
        self.db = sqlite3.connect(database_path)
        self.db.row_factory = sqlite3.Row
        self.decay = decay
        self.threshold = threshold
        self.max_hops = max_hops
        self.max_results = max_results
        self._edges: dict[str, list[tuple[str, float]]] = {}
        self._cards: dict[str, dict] = {}
        self._load_graph()

    def _load_graph(self) -> None:
        # Load cards
        rows = self.db.execute(
            "SELECT id, card_json FROM concept_cards"
        ).fetchall()
        for row in rows:
            self._cards[row["id"]] = json.loads(row["card_json"])
        # Load real-usage edges; a database with no recorded usage simply
        # yields an empty graph instead of failing the search.
        try:
            edge_rows = self.db.execute(
                "SELECT source_id, target_id, count FROM concept_usage_edges"
            ).fetchall()
        except sqlite3.OperationalError:
            return
        for row in edge_rows:
            src, tgt, count = row["source_id"], row["target_id"], row["count"]
            # Co-usage frequency saturates towards 1 as the pair is reused.
            conf = count / (count + 1)
            # Bidirectional traversal (undirected for retrieval)
            self._edges.setdefault(src, []).append((tgt, conf))
            self._edges.setdefault(tgt, []).append((src, conf))

    def search(self, query: str) -> list[ActivationResult]:
        like = f"%{query.strip()}%"
        term = query.strip()
        seeds: dict[str, float] = {}
        try:
            rows = self.db.execute(
                """
                SELECT id, name,
                       json_extract(card_json, '$.definition') AS definition,
                       json_extract(card_json, '$.background') AS background,
                       json_extract(card_json, '$.background_concepts') AS bg_concepts
                FROM concept_cards
                WHERE name LIKE ? OR definition LIKE ?
                   OR json_extract(card_json, '$.background') LIKE ?
                   OR json_extract(card_json, '$.background_concepts') LIKE ?
                """,
                (like, like, like, like),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for row in rows:
            if term in (row["name"] or ""):
                score = 1.0       # name match: strongest signal
            elif term in (row["definition"] or ""):
                score = 0.7       # definition match
            elif term in (row["background"] or ""):
                score = 0.4       # background context mention
            else:
                score = 0.3       # background_concepts only
            seeds[row["id"]] = max(seeds.get(row["id"], 0.0), score)
        return self.search_from_seeds(seeds)

    def search_from_seeds(self, seeds: dict[str, float]) -> list[ActivationResult]:
        """Spread activation from caller-provided seeds (id -> initial score)."""

        if not seeds:
            return []

        # Step 2: spreading activation
        activations: dict[str, float] = dict(seeds)
        hops: dict[str, int] = {cid: 0 for cid in seeds}
        paths: dict[str, list[str]] = {
            cid: [self._cards[cid]["name"]] for cid in seeds if cid in self._cards
        }
        frontier = set(seeds.keys())

        for _hop in range(self.max_hops):
            next_frontier: dict[str, tuple[float, str]] = {}  # target -> (activation, from_id)
            for node_id in frontier:
                node_act = activations.get(node_id, 0.0)
                if node_act < self.threshold:
                    continue
                for neighbor_id, edge_conf in self._edges.get(node_id, []):
                    spread = node_act * self.decay * edge_conf
                    if spread > activations.get(neighbor_id, 0.0) and spread >= self.threshold:
                        best = next_frontier.get(neighbor_id)
                        if best is None or spread > best[0]:
                            next_frontier[neighbor_id] = (spread, node_id)
            for neighbor_id, (spread, from_id) in next_frontier.items():
                activations[neighbor_id] = spread
                hops[neighbor_id] = _hop + 1
                from_name = self._cards[from_id]["name"] if from_id in self._cards else "?"
                neighbor_name = self._cards[neighbor_id]["name"] if neighbor_id in self._cards else "?"
                paths[neighbor_id] = paths.get(from_id, [from_name]) + [neighbor_name]
            frontier = set(next_frontier.keys())

        # Step 3: build results sorted by activation
        results = []
        for cid, act in sorted(activations.items(), key=lambda x: -x[1]):
            card = self._cards.get(cid)
            if not card:
                continue
            results.append(ActivationResult(
                card=card,
                activation=round(act, 4),
                hop=hops.get(cid, 0),
                path=paths.get(cid, [card["name"]]),
            ))
        return results[:self.max_results]

    def close(self) -> None:
        self.db.close()


def record_usage(database_path: str, card_ids: list[str]) -> None:
    """Persist one real-usage event: the co-use of several cards together.

    Called on every multi-card ``search_concepts`` fetch (card mode).  Each
    unordered pair in the fetch gets its counter incremented by one;
    repeated co-use makes the edge heavier.  Unknown or duplicate IDs are
    ignored.
    """

    unique_ids = list(dict.fromkeys(card_id for card_id in card_ids if card_id))
    if len(unique_ids) < 2:
        return
    conn = sqlite3.connect(database_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS concept_usage_edges (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source_id, target_id)
            )
        """)
        with conn:
            for i, source in enumerate(unique_ids):
                for target in unique_ids[i + 1:]:
                    first, second = sorted((source, target))
                    conn.execute("""
                        INSERT INTO concept_usage_edges(source_id, target_id, count)
                        VALUES (?, ?, 1)
                        ON CONFLICT(source_id, target_id) DO UPDATE SET
                            count = count + 1,
                            updated_at = CURRENT_TIMESTAMP
                    """, (first, second))
    finally:
        conn.close()
