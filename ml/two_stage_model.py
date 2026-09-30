"""Two-stage ranking inference helper.

The production code can import this module without depending on the AUDIT-002
research scripts. It intentionally does not load model files by itself; callers
own model loading and pass model-like objects that expose ``predict``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd


@dataclass(frozen=True)
class TwoStagePredictionConfig:
    """Configuration for two-stage inference."""

    candidate_size: int = 60
    top_n: int = 30
    stage1_score_col: str = "stage1_score"
    stage2_score_col: str = "stage2_score"
    final_rank_col: str = "two_stage_rank"


class TwoStageModel:
    """Stage 1 regression screen followed by Stage 2 ranker reordering."""

    def __init__(
        self,
        *,
        stage1_model: Any | None,
        stage2_model: Any | None,
        feature_cols: Iterable[str],
        config: TwoStagePredictionConfig | None = None,
    ) -> None:
        self.stage1_model = stage1_model
        self.stage2_model = stage2_model
        self.feature_cols = list(feature_cols)
        self.config = config or TwoStagePredictionConfig()

    def _missing_features(self, frame: pd.DataFrame) -> list[str]:
        return [col for col in self.feature_cols if col not in frame.columns]

    def _predict_with(self, model: Any, frame: pd.DataFrame) -> Any:
        if model is None:
            raise ValueError("Model is required when score column is not precomputed")
        return model.predict(frame[self.feature_cols].values)

    def predict(
        self,
        universe_df: pd.DataFrame,
        *,
        precomputed_stage1_score_col: str | None = None,
        precomputed_stage2_score_col: str | None = None,
    ) -> pd.DataFrame:
        """Return the final Top-N ranked DataFrame.

        Args:
            universe_df: Inference universe with all feature columns.
            precomputed_stage1_score_col: Optional existing column to use
                instead of calling ``stage1_model.predict``.
            precomputed_stage2_score_col: Optional existing column to use
                instead of calling ``stage2_model.predict`` on candidates.
        """
        if universe_df.empty:
            return universe_df.copy()
        missing = self._missing_features(universe_df)
        if missing:
            raise ValueError(f"Missing feature columns for two-stage inference: {missing[:8]}")

        cfg = self.config
        out = universe_df.copy()
        if precomputed_stage1_score_col:
            if precomputed_stage1_score_col not in out.columns:
                raise ValueError(f"{precomputed_stage1_score_col} missing from universe_df")
            out[cfg.stage1_score_col] = pd.to_numeric(
                out[precomputed_stage1_score_col], errors="coerce"
            )
        else:
            out[cfg.stage1_score_col] = self._predict_with(self.stage1_model, out)

        candidates = (
            out.dropna(subset=[cfg.stage1_score_col])
            .sort_values(cfg.stage1_score_col, ascending=False)
            .head(cfg.candidate_size)
            .copy()
        )
        if candidates.empty:
            candidates[cfg.stage2_score_col] = pd.Series(dtype="float64")
            candidates[cfg.final_rank_col] = pd.Series(dtype="int64")
            return candidates

        if precomputed_stage2_score_col:
            if precomputed_stage2_score_col not in candidates.columns:
                raise ValueError(f"{precomputed_stage2_score_col} missing from candidates")
            candidates[cfg.stage2_score_col] = pd.to_numeric(
                candidates[precomputed_stage2_score_col], errors="coerce"
            )
        else:
            candidates[cfg.stage2_score_col] = self._predict_with(self.stage2_model, candidates)

        ranked = (
            candidates.dropna(subset=[cfg.stage2_score_col])
            .sort_values(cfg.stage2_score_col, ascending=False)
            .head(cfg.top_n)
            .copy()
        )
        ranked[cfg.final_rank_col] = range(1, len(ranked) + 1)
        return ranked


def predict_two_stage(
    universe_df: pd.DataFrame,
    *,
    stage1_model: Any | None,
    stage2_model: Any | None,
    feature_cols: Iterable[str],
    candidate_size: int = 60,
    top_n: int = 30,
) -> pd.DataFrame:
    """Convenience wrapper around :class:`TwoStageModel`."""
    model = TwoStageModel(
        stage1_model=stage1_model,
        stage2_model=stage2_model,
        feature_cols=feature_cols,
        config=TwoStagePredictionConfig(candidate_size=candidate_size, top_n=top_n),
    )
    return model.predict(universe_df)
