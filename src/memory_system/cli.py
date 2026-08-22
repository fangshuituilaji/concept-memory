"""Command-line wrapper around the independent Python API."""

from __future__ import annotations

import argparse
import sys

from .pipeline import analyze_path, cards_to_json
from .storage import ConceptStore
from .synthesis import ConceptSynthesisConfig, OfflineConceptSynthesizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize Python source into a small set of semantic concept cards"
    )
    parser.add_argument("path", help="Python file or source directory")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = ConceptSynthesisConfig(
        model=args.model,
        target_concepts=args.target_concepts,
        max_concepts=args.max_concepts,
    )
    synthesizer = OfflineConceptSynthesizer(config) if args.offline else None
    cards = analyze_path(args.path, synthesizer=synthesizer, config=config)
    payload = cards_to_json(cards)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload)
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


if __name__ == "__main__":
    raise SystemExit(main())
