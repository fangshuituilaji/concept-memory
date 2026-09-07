"""Qwen-Flash driven retrieval: query expansion plus candidate reranking.

Retrieval is online-only by design: when the model is unreachable the search
fails loudly after retries instead of silently degrading to local FTS.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

REQUEST_TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    """Controls the online retrieval behaviour for one search call."""

    model: str = "qwen-flash"
    api_key_env: str = "DASHSCOPE_API_KEY"
    candidate_pool: int = 30
    max_keywords: int = 8


class CandidateSource(Protocol):
    def search_any(self, query: str, *, limit: int) -> list: ...


class QwenFlashRetriever:
    """Online-only retriever; there is intentionally no offline fallback."""

    generated_by = "qwen-flash"

    def __init__(self, config: RetrievalConfig | None = None):
        self.config = config or RetrievalConfig()

    def search(self, store: CandidateSource, query: str, limit: int) -> list:
        keywords = self.expand(query)
        recall = " ".join([query, *keywords])
        candidates = store.search_any(recall, limit=self.config.candidate_pool)
        if not candidates:
            return []
        ranked = self.rerank(query, candidates)
        return ranked[:limit]

    def expand(self, query: str) -> list[str]:
        """Ask the model for extra recall keywords (synonyms, translations)."""

        content = self._call(
            [
                {
                    "role": "system",
                    "content": (
                        "你是代码概念检索的查询扩展器。把用户的自然语言查询改写为"
                        f"最多 {self.config.max_keywords} 个适合全文检索的关键词或短语，"
                        "包含中文与英文同义词、相关代码术语。只返回 JSON："
                        '{"keywords": ["...", "..."]}，不要其他内容。'
                    ),
                },
                {"role": "user", "content": query},
            ]
        )
        try:
            payload = json.loads(_strip_code_fence(content))
            keywords = payload.get("keywords", []) if isinstance(payload, dict) else []
        except json.JSONDecodeError:
            keywords = []
        return [str(item).strip() for item in keywords if str(item).strip()][
            : self.config.max_keywords
        ]

    def rerank(self, query: str, candidates: list) -> list:
        """Order candidates by relevance to the query using the model."""

        listing = "\n".join(
            f"{index}. {item.card.name} | {item.card.definition[:100]}"
            for index, item in enumerate(candidates)
        )
        content = self._call(
            [
                {
                    "role": "system",
                    "content": (
                        "你是代码概念检索的重排器。给定用户查询和候选概念卡片列表，"
                        "按与查询的相关性从高到低排序，只返回相关卡片的编号，格式："
                        '{"order": [2, 0, 5]}，不要其他内容。'
                    ),
                },
                {"role": "user", "content": f"查询：{query}\n\n候选卡片：\n{listing}"},
            ]
        )
        try:
            payload = json.loads(_strip_code_fence(content))
            order = payload.get("order", []) if isinstance(payload, dict) else []
        except json.JSONDecodeError:
            order = []
        ranked: list = []
        seen: set[int] = set()
        for raw in order:
            try:
                index = int(raw)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(candidates) and index not in seen:
                seen.add(index)
                ranked.append(candidates[index])
        # Model-missed candidates keep their recall order after the ranked ones
        # so a malformed rerank never loses direct hits entirely.
        ranked.extend(item for index, item in enumerate(candidates) if index not in seen)
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
