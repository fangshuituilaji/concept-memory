"""Spreading activation retrieval over the concept graph."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .relations import ConceptRelation


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
        threshold: float = 0.1,
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
        # Load edges
        edge_rows = self.db.execute(
            "SELECT source_id, target_id, confidence FROM concept_relations"
        ).fetchall()
        for row in edge_rows:
            src, tgt, conf = row["source_id"], row["target_id"], row["confidence"]
            # Bidirectional traversal (undirected for retrieval)
            self._edges.setdefault(src, []).append((tgt, conf))
            self._edges.setdefault(tgt, []).append((src, conf))

    def search(self, query: str) -> list[ActivationResult]:
        # Step 1: seed matching with field-weighted activation scores
        like = f"%{query.strip()}%"
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
        term = query.strip()
        seeds: dict[str, float] = {}
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


def store_relations(database_path: str, relations: list[ConceptRelation]) -> None:
    """Persist relations into SQLite."""
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS concept_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.5,
            explanation TEXT NOT NULL DEFAULT '',
            UNIQUE(source_id, target_id)
        )
    """)
    with conn:
        for r in relations:
            conn.execute("""
                INSERT INTO concept_relations(source_id, target_id, relation_type, confidence, explanation)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, target_id) DO UPDATE SET
                    relation_type = excluded.relation_type,
                    confidence = excluded.confidence,
                    explanation = excluded.explanation
            """, (r.source_id, r.target_id, r.relation_type, r.confidence, r.explanation))
    conn.close()
