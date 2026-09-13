"""Incremental rescans: skip unchanged files instead of re-reading the repo.

A per-project ``file_state`` table records each file's ``mtime``, ``size`` and
content digest.  On rescan, files whose ``(mtime, size)`` match the record are
skipped entirely (no read, no parse, no cache lookup) and their stored cards
are reused verbatim.  Files whose stat changed are re-read and hashed: when
the digest still matches (git checkout/clone rewriting identical content) they
are skipped too.  Only genuinely changed files go through the analysis
pipeline, so rescan cost drops from O(repository) to O(changed files).
"""

from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable, Iterable

from .cache import ConceptCache
from .models import ConceptCard
from .pipeline import analyze_path
from .readers import discover_code_files
from .storage import ConceptStore


@dataclass(frozen=True, slots=True)
class FileStateRecord:
    """One file's last-scanned identity (root-relative posix path keyed)."""

    path: str
    mtime: float
    size: int
    source_digest: str


@dataclass(frozen=True, slots=True)
class IncrementalScanResult:
    """Outcome of one incremental scan, surfaced on the progress page."""

    cards: list[ConceptCard]
    changed_files: tuple[str, ...]
    skipped_files: int
    pruned_cards: int


class FileStateStore:
    """Own connection to the project DB holding the ``file_state`` table."""

    def __init__(self, database_path: str | Path):
        self._database_path = str(Path(database_path).expanduser())

    def load(self) -> dict[str, FileStateRecord]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT path, mtime, size, source_digest FROM file_state"
            ).fetchall()
        finally:
            connection.close()
        return {
            row["path"]: FileStateRecord(
                path=row["path"],
                mtime=float(row["mtime"]),
                size=int(row["size"]),
                source_digest=row["source_digest"],
            )
            for row in rows
        }

    def commit_scan(
        self,
        records: Iterable[FileStateRecord],
        keep_paths: Iterable[str] | None,
    ) -> None:
        """Persist scan outcomes in one transaction.

        ``records`` covers every file examined this scan (changed files plus
        digest-identical stat refreshes); ``keep_paths`` is the discovered
        file set, so rows for deleted or renamed-away files drop out here.
        """

        connection = self._connect()
        try:
            with connection:
                for record in records:
                    connection.execute(
                        """
                        INSERT INTO file_state(path, mtime, size, source_digest, scanned_at)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(path) DO UPDATE SET
                            mtime = excluded.mtime,
                            size = excluded.size,
                            source_digest = excluded.source_digest,
                            scanned_at = CURRENT_TIMESTAMP
                        """,
                        (record.path, record.mtime, record.size, record.source_digest),
                    )
                if keep_paths is not None:
                    keep = set(keep_paths)
                    rows = connection.execute("SELECT path FROM file_state").fetchall()
                    for row in rows:
                        if row["path"] not in keep:
                            connection.execute(
                                "DELETE FROM file_state WHERE path = ?", (row["path"],)
                            )
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        Path(self._database_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS file_state (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL,
                size INTEGER NOT NULL,
                source_digest TEXT NOT NULL,
                scanned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()
        return connection


def _source_digest(file_path: Path) -> str:
    """Hash decoded text exactly like every analyzer in ``extractor`` does."""

    text = file_path.read_text(encoding="utf-8")
    return sha256(text.encode("utf-8")).hexdigest()


def incremental_scan(
    root: str | Path,
    *,
    store: ConceptStore,
    database_path: str | Path,
    cache: ConceptCache | None = None,
    synthesizer: object | None = None,
    config: object | None = None,
    max_workers: int = 6,
    progress_callback: Callable[[int, int, str], None] | None = None,
    store_lock: AbstractContextManager | None = None,
) -> IncrementalScanResult:
    """Rescan ``root``, analyzing only files whose content actually changed.

    ``store_lock`` serializes access to the shared :class:`ConceptStore`
    connection (the MCP server passes its global lock); file IO, parsing and
    model synthesis run outside it so searches stay responsive.
    """

    root_path = Path(root).expanduser().resolve()
    files = discover_code_files(root_path)
    rel_files = {file.relative_to(root_path).as_posix(): file for file in files}
    state_store = FileStateStore(database_path)
    states = state_store.load()
    lock = store_lock if store_lock is not None else nullcontext()
    with lock:
        # A state row without stored cards means a previous scan was
        # interrupted; re-analyzing such files self-heals the store.
        stored_paths = store.stored_file_paths()

    changed: list[Path] = []
    changed_records: list[FileStateRecord] = []
    refreshed_records: list[FileStateRecord] = []
    unchanged: list[str] = []
    for rel, file_path in rel_files.items():
        stat = file_path.stat()
        record = states.get(rel)
        if (
            record is not None
            and rel in stored_paths
            and record.mtime == stat.st_mtime
            and record.size == stat.st_size
        ):
            unchanged.append(rel)
            continue
        digest = _source_digest(file_path)
        if record is not None and record.source_digest == digest and rel in stored_paths:
            # Stat changed but content is identical (git rewrote the file);
            # remember the new stat so the fast path hits next time.
            unchanged.append(rel)
            refreshed_records.append(
                FileStateRecord(rel, stat.st_mtime, stat.st_size, digest)
            )
            continue
        changed.append(file_path)
        changed_records.append(FileStateRecord(rel, stat.st_mtime, stat.st_size, digest))

    new_cards: list[ConceptCard] = []
    if changed:
        new_cards = analyze_path(
            root_path,
            files=changed,
            synthesizer=synthesizer,  # type: ignore[arg-type]
            config=config,  # type: ignore[arg-type]
            cache=cache,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )
    elif progress_callback is not None:
        progress_callback(0, 0, "")

    with lock:
        reused: list[ConceptCard] = []
        for rel in unchanged:
            reused.extend(store.cards_by_file(rel))
        store.upsert_cards(new_cards)
        keep_ids = {card.id for card in reused} | {card.id for card in new_cards}
        pruned = store.prune_not_in(keep_ids)

    state_store.commit_scan(
        [*changed_records, *refreshed_records],
        set(rel_files),
    )
    if cache is not None:
        cache.prune_missing(set(rel_files), root=root_path)

    return IncrementalScanResult(
        cards=[*reused, *new_cards],
        changed_files=tuple(record.path for record in changed_records),
        skipped_files=len(unchanged),
        pruned_cards=pruned,
    )
