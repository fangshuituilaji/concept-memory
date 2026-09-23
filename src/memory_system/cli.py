"""Command-line entry point for the independent Python API."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .pipeline import analyze_path, cards_to_json
from .security import JsonlAuditRecorder, SecurityPolicy
from .storage import ConceptStore
from .synthesis import ConceptSynthesisConfig, OfflineConceptSynthesizer


def build_parser() -> argparse.ArgumentParser:
    """Build the legacy ``memory-concepts`` parser."""

    parser = argparse.ArgumentParser(
        description="Summarize supported source files into semantic concept cards"
    )
    parser.add_argument(
        "path",
        help=(
            "Source file or directory; Python is built in, and TypeScript/"
            "JavaScript require the optional tree-sitter extra"
        ),
    )
    parser.add_argument("--output", help="Write cards to this JSON file instead of stdout")
    parser.add_argument("--database", help="Also persist cards in this SQLite database")
    parser.add_argument("--search", help="Search persisted cards after indexing")
    parser.add_argument(
        "--model",
        default="qwen-flash",
        help="DashScope model name; default: qwen-flash",
    )
    parser.add_argument(
        "--target-concepts",
        type=int,
        default=3,
        help="Target number of concepts per file; default: 3",
    )
    parser.add_argument(
        "--max-concepts",
        type=int,
        default=9,
        help="Hard maximum number of concepts per file; default: 9",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use the deterministic fallback instead of DashScope",
    )
    parser.add_argument(
        "--cache",
        help="Optional JSON concept-draft cache path for incremental generation",
    )
    parser.add_argument(
        "--audit-log",
        help="Optional JSONL source-send decision audit path",
    )
    parser.add_argument(
        "--allow-sensitive",
        action="append",
        default=[],
        metavar="PATH",
        help="Explicitly allow a sensitive source path for online generation",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the legacy ``memory-concepts`` command unchanged."""

    _load_dotenv()
    args = build_parser().parse_args(argv)
    config = ConceptSynthesisConfig(
        model=args.model,
        target_concepts=args.target_concepts,
        max_concepts=args.max_concepts,
    )
    synthesizer = OfflineConceptSynthesizer(config) if args.offline else None
    target = Path(args.path).expanduser().resolve()
    root = target if target.is_dir() else target.parent
    policy = SecurityPolicy(
        root=root,
        source_sending_policy="offline" if args.offline else "online",
        allowlist=args.allow_sensitive,
    )
    audit = JsonlAuditRecorder(args.audit_log) if args.audit_log else None
    cards = analyze_path(
        args.path,
        synthesizer=synthesizer,
        config=config,
        cache_path=args.cache,
        security_policy=policy,
        audit_recorder=audit,
        source_sending_policy="offline" if args.offline else "online",
    )
    payload = cards_to_json(cards)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    if args.database:
        with ConceptStore(args.database) as store:
            store.upsert_cards(cards)
            if args.search:
                for result in store.search(args.search):
                    print(
                        f"{result.card.name} [{result.card.kind.value}] "
                        f"{result.card.location.file_path}:{result.card.location.start_line} "
                        f"— {result.explanation}"
                    )
    return 0


def _load_dotenv(path: str | Path | None = None) -> None:
    """Load simple KEY=VALUE entries without overriding the process environment."""

    dotenv_path = Path(path or ".env").expanduser()
    if not dotenv_path.is_file():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key.startswith("#") or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


if __name__ == "__main__":
    raise SystemExit(main())
