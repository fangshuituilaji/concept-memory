"""Discover typed relations between concept cards using a small model call."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from .synthesis import REQUEST_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class ConceptRelation:
    """A directed edge between two concept cards."""

    source_id: str
    target_id: str
    relation_type: str  # upstream_of, enables, constrains, part_of, related_to
    confidence: float
    explanation: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type,
            "confidence": self.confidence,
            "explanation": self.explanation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConceptRelation":
        return cls(
            source_id=data["source_id"],
            target_id=data["target_id"],
            relation_type=data["relation_type"],
            confidence=float(data.get("confidence", 0.5)),
            explanation=data.get("explanation", ""),
        )


VALID_RELATION_TYPES = frozenset({
    "upstream_of", "enables", "constrains", "part_of", "related_to",
})


def discover_relations(
    cards_json_path: str,
    *,
    api_key_env: str = "DASHSCOPE_API_KEY",
    model: str = "qwen-flash",
) -> list[ConceptRelation]:
    """Read concept cards, ask the model for relations, return edges."""
    with open(cards_json_path, encoding="utf-8") as f:
        cards = json.load(f)

    # Build a compact index: id, name, definition (one line)
    entries = []
    id_set = set()
    for card in cards:
        cid = card["id"]
        if cid in id_set:
            continue
        id_set.add(cid)
        entries.append(f"{cid}|{card['name']}|{card['definition'][:60]}")

    prompt_text = "\n".join(entries)
    system_prompt = (
        "你是一个概念关系发现器。以下是代码库中的概念卡片列表（格式：ID|名称|定义）。"
        "请识别概念之间的有向关系。\n"
        "关系类型只能是以下五种之一：\n"
        "- upstream_of（上游输入）\n"
        "- enables（使能/支撑）\n"
        "- constrains（约束/限制）\n"
        "- part_of（组成部分）\n"
        "- related_to（相关但不确定方向）\n\n"
        "要求：\n"
        "1. 只使用列表中出现的 ID。\n"
        "2. 每条关系给出 confidence（0-1 浮点数）和一句话中文解释。\n"
        "3. 不要为每一对概念都生成关系；只保留有明确语义关联的边。\n"
        "4. 只返回 JSON，不要 Markdown。\n\n"
        'JSON 格式：{"relations": [{"source": "...", "target": "...", '
        '"type": "...", "confidence": 0.8, "explanation": "..."}]}'
    )

    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(f"Missing {api_key_env}")

    import dashscope
    dashscope.api_key = api_key
    response = dashscope.Generation.call(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_text},
        ],
        result_format="message",
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    content = response.output.choices[0].message.content
    return _parse_relations(content, id_set)


def _parse_relations(content: str, valid_ids: set[str]) -> list[ConceptRelation]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Model response did not contain JSON")
        payload = json.loads(text[start:end + 1])
    raw = payload.get("relations", [])
    relations: list[ConceptRelation] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        src = str(item.get("source", "")).strip()
        tgt = str(item.get("target", "")).strip()
        rtype = str(item.get("type", "")).strip()
        if src not in valid_ids or tgt not in valid_ids or src == tgt:
            continue
        if rtype not in VALID_RELATION_TYPES:
            continue
        key = (src, tgt)
        if key in seen:
            continue
        seen.add(key)
        relations.append(ConceptRelation(
            source_id=src,
            target_id=tgt,
            relation_type=rtype,
            confidence=max(0.0, min(1.0, float(item.get("confidence", 0.5)))),
            explanation=str(item.get("explanation", "")),
        ))
    return relations
