"""Qwen-Flash driven retrieval: the model reads the full card catalog.

Retrieval is online-only by design: when the model is unreachable the search
fails loudly after retries instead of silently degrading to local FTS.  There
is intentionally no lexical recall step (FTS/substring): the whole catalog of
card names and definitions is placed in the model context, so a relevant card
can never be lost to tokenizer or matching quirks before the model sees it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .models import ConceptCard
from .storage import ConceptSearchResult

REQUEST_TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    """Controls the online retrieval behaviour for one search call."""

    model: str = "qwen-flash"
    api_key_env: str = "DASHSCOPE_API_KEY"
    # Catalogs above one batch are ranked batch-by-batch first; winners meet
    # again in a final call. Small projects hit the single-call fast path.
    rank_batch_size: int = 150
    shortlist_per_batch: int = 10


class CandidateSource(Protocol):
    def all_cards(self, *, limit: int | None = None) -> list[ConceptCard]: ...


class QwenFlashRetriever:
    """Online-only retriever; there is intentionally no offline fallback."""

    generated_by = "qwen-flash"

    def __init__(self, config: RetrievalConfig | None = None):
        self.config = config or RetrievalConfig()

    def search(self, store: CandidateSource, query: str, limit: int) -> list[ConceptSearchResult]:
        """Select relevant cards by showing the model the full catalog."""

        cards = store.all_cards()
        if not cards:
            return []
        batch_size = max(1, self.config.rank_batch_size)
        batches = [cards[i : i + batch_size] for i in range(0, len(cards), batch_size)]
        if len(batches) == 1:
            finalists = batches[0]
        else:
            finalists = []
            for batch in batches:
                finalists.extend(
                    self.rank(query, batch)[: self.config.shortlist_per_batch]
                )
        ordered = self.rank(query, finalists)[: max(1, limit)]
        return [
            ConceptSearchResult(
                card=card,
                rank=float(position),
                matched_fields=(),
                explanation="selected by qwen-flash from the full catalog",
            )
            for position, card in enumerate(ordered)
        ]

    def rank(self, query: str, cards: list[ConceptCard]) -> list[ConceptCard]:
        """Order cards by relevance to the query using the model."""

        catalog = "\n".join(
            f"{card.id} | {card.name} | {card.definition}" for card in cards
        )
        content = self._call(
            [
                {
                    "role": "system",
                    "content": (
                        "你是代码概念检索器。给定用户查询和概念卡片目录（每行一张卡："
                        "卡片ID | 概念名 | 定义），从中挑选与查询相关的卡片，"
                        "按相关性从高到低排序，只返回相关卡片的ID，格式："
                        '{"ids": ["...", "..."]}，不要其他内容。'
                    ),
                },
                {"role": "user", "content": f"查询：{query}\n\n卡片目录：\n{catalog}"},
            ]
        )
        try:
            payload = json.loads(_strip_code_fence(content))
            ids = payload.get("ids", []) if isinstance(payload, dict) else []
        except json.JSONDecodeError as exc:
            raise RuntimeError("Online retrieval returned invalid JSON") from exc
        by_id = {card.id: card for card in cards}
        ranked: list[ConceptCard] = []
        seen: set[str] = set()
        for raw in ids:
            card_id = str(raw).strip()
            if card_id in by_id and card_id not in seen:
                seen.add(card_id)
                ranked.append(by_id[card_id])
        if not ranked:
            raise RuntimeError("Online retrieval returned no usable card ids")
        # Model-missed cards keep their catalog order after the ranked ones
        # so a partial answer never loses direct hits entirely.
        ranked.extend(card for card in cards if card.id not in seen)
        return ranked

    def _call(self, messages: list[dict[str, str]]) -> str:
        api_key = os.getenv(self.config.api_key_env)
        if not api_key:
            raise RuntimeError(
                "Online retrieval requires the DashScope API key environment "
                f"variable: {self.config.api_key_env}"
            )
        try:
            import dashscope
        except ImportError as exc:
            raise RuntimeError(
                "DashScope SDK is not installed; install the optional 'dashscope' dependency."
            ) from exc
        dashscope.api_key = api_key
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = dashscope.Generation.call(
                    model=self.config.model,
                    messages=messages,
                    result_format="message",
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                return _response_content(response)
            except Exception as exc:  # network/SDK errors retry, never fall back
                last_error = exc
                if attempt < MAX_ATTEMPTS:
                    time.sleep(attempt)
        raise RuntimeError(
            f"Online retrieval failed after {MAX_ATTEMPTS} attempts; "
            "no offline fallback is allowed."
        ) from last_error


def _response_content(response: Any) -> str:
    try:
        content = response.output.choices[0].message.content
    except (AttributeError, IndexError, KeyError) as exc:
        raise ValueError("DashScope response did not contain message content") from exc
    if not isinstance(content, str):
        raise ValueError("DashScope response content is not text")
    return content


def _strip_code_fence(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return stripped
