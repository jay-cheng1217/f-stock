from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml


TAXONOMY_PATH = os.path.join(os.path.dirname(__file__), "taxonomy.yaml")


@dataclass(frozen=True)
class NarrativeLabel:
    label: str
    matched_keywords: tuple[str, ...]
    half_life_days: int
    polarity: str = "mixed"

    def matched_keywords_json(self) -> str:
        return json.dumps(list(self.matched_keywords), ensure_ascii=False)


@lru_cache(maxsize=1)
def load_taxonomy(path: str = TAXONOMY_PATH) -> dict[str, dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}
    labels = payload.get("labels") or {}
    if not isinstance(labels, dict):
        raise ValueError("taxonomy.yaml must contain a labels mapping")
    return labels


def label_text(title: str, snippet: str = "", taxonomy: dict[str, dict[str, Any]] | None = None) -> list[NarrativeLabel]:
    """Apply the approved keyword taxonomy to metadata only."""
    labels = taxonomy or load_taxonomy()
    raw_text = f"{title or ''} {snippet or ''}".strip()
    text_lower = raw_text.lower()
    matched: list[NarrativeLabel] = []

    for label, config in labels.items():
        keywords = [str(k) for k in (config.get("keywords") or []) if str(k).strip()]
        hits: list[str] = []
        for keyword in keywords:
            if keyword.lower() in text_lower:
                hits.append(keyword)
        if hits:
            matched.append(
                NarrativeLabel(
                    label=str(label),
                    matched_keywords=tuple(dict.fromkeys(hits)),
                    half_life_days=int(config.get("half_life_days") or 30),
                    polarity=str(config.get("polarity") or "mixed"),
                )
            )

    return matched


def taxonomy_half_life_lookup(taxonomy: dict[str, dict[str, Any]] | None = None) -> dict[str, int]:
    labels = taxonomy or load_taxonomy()
    return {
        str(label): int(config.get("half_life_days") or 30)
        for label, config in labels.items()
    }
