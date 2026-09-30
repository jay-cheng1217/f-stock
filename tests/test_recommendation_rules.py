"""recommendation rules 防呆規則回歸測試.

對應 CLAUDE.md 第二層 / 第三層硬擋規則，每條鎖一條測試。修過濾器前必看這檔。

執行：
    pytest tests/test_recommendation_rules.py -v
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml.predict import _apply_recommendation_rules_core
from ml import predict as predict_module


# ============================================================================
# Fixture 工具：建一個「乾淨無瑕疵」的 pred + snapshot，後續用 .copy() 改幾欄
# 觀察單一規則。預設值都安全可買進。
# ============================================================================
def _base_pred_row(**overrides):
    row = dict(
        ticker="2330",
        signal="UP",
        up_prob=0.70,
        flat_prob=0.20,
        down_prob=0.10,           # prob_edge = 0.60 → 強力買進門檻 OK
        pred_return_20d=0.10,     # > V2 strong (0.03) 也 > override (0.08)
        close=600.0,
    )
    row.update(overrides)
    return row


def _base_snapshot_row(**overrides):
    row = dict(
        atr_pct=0.02,                       # ATR cap = max(0.10, 0.20) = 0.20
        price_vs_ma20=0.05,                 # 5% (< 30% overheat threshold)
        VOL_MA_5=2_000_000,                 # 200 萬股 = 2000 張
        Volume=2_000_000,
        return_20d=0.0,                     # 沒暴跌
        operating_margin_latest=0.15,       # 本業有賺 15%
        chip_diverge_bear=0.0,
        chip_diverge_bull=0.0,
        # turnaround 訊號相關欄位都缺 → score=0 < 2 → 不算修復
    )
    row.update(overrides)
    return row


def _make_dfs(pred_overrides=None, snap_overrides=None):
    pred = pd.DataFrame([_base_pred_row(**(pred_overrides or {}))])
    snap = pd.DataFrame([_base_snapshot_row(**(snap_overrides or {}))])
    return pred, snap


@pytest.fixture(autouse=True)
def _isolate_disposition(monkeypatch):
    """避免測試讀到磁碟上的 disposition_active.csv。預設清單為空。"""
    monkeypatch.setattr(
        predict_module, "_load_disposition_set", lambda: set()
    )


# ============================================================================
# 基線：乾淨買進條件 → 強力買進
# ============================================================================
class TestStrongBuyPromotion:
    def test_clean_dual_track_promotes_to_strong_buy(self):
        pred, snap = _make_dfs()
        out = _apply_recommendation_rules_core(pred, snap)
        assert out.loc[0, "recommendation"] == "強力買進"

    def test_only_v1_ok_v2_bull_not_strong_yields_buy_only(self):
        # V1 only 軟通過 (prob_edge=0.04 < STRONG=0.05)；V2 看多 +5%
        # 結果應該是「建議買進」+「勝率優勢未達強力買進門檻」標籤
        pred, snap = _make_dfs(pred_overrides=dict(
            up_prob=0.40, flat_prob=0.36, down_prob=0.24,  # prob_edge=0.16
            pred_return_20d=0.05,
        ))
        # 把 prob_edge 卡到剛好 strong 門檻以下
        pred.loc[0, "up_prob"] = 0.40
        pred.loc[0, "flat_prob"] = 0.38
        pred.loc[0, "down_prob"] = 0.22  # edge = 0.18 → strong
        out = _apply_recommendation_rules_core(pred, snap)
        # 0.18 邊際雖達 strong，但 V2 5% 達 strong V2 門檻 (0.03)，仍會被升級
        # 所以這個 case 預期是強力買進
        assert out.loc[0, "recommendation"] == "強力買進"


# ============================================================================
# CLAUDE.md 規則 #7：流動性不足硬擋
# ============================================================================
class TestLiquidityBlock:
    def test_low_volume_demotes_strong_buy_to_watchlist(self):
        # 5 日均量 < 500k
        pred, snap = _make_dfs(snap_overrides=dict(VOL_MA_5=400_000, Volume=400_000))
        out = _apply_recommendation_rules_core(pred, snap)
        assert out.loc[0, "recommendation"] == "觀望（流動性不足）"

    def test_low_today_volume_alone_triggers_block(self):
        # 均量 OK 但當日量低 → 仍應擋
        pred, snap = _make_dfs(snap_overrides=dict(VOL_MA_5=2_000_000, Volume=300_000))
        out = _apply_recommendation_rules_core(pred, snap)
        assert out.loc[0, "recommendation"] == "觀望（流動性不足）"


# ============================================================================
# CLAUDE.md 規則 #8：異常暴跌硬擋
# ============================================================================
class TestCrashBlock:
    def test_return_20d_below_minus_40_demotes(self):
        pred, snap = _make_dfs(snap_overrides=dict(return_20d=-0.45))
        out = _apply_recommendation_rules_core(pred, snap)
        assert out.loc[0, "recommendation"] == "觀望（異常暴跌）"

    def test_return_20d_minus_39_does_not_demote(self):
        # 邊界：-39% 仍可以買
        pred, snap = _make_dfs(snap_overrides=dict(return_20d=-0.39))
        out = _apply_recommendation_rules_core(pred, snap)
        assert out.loc[0, "recommendation"] == "強力買進"


# ============================================================================
# CLAUDE.md 規則 #9：極端預測值硬擋
# ============================================================================
class TestExtremePredictionBlock:
    def test_pred_return_above_30pct_demotes(self):
        # ATR cap 會先把 pred_return 切到 ATR*10 = 0.20，所以這個只在 ATR 大時才會觸發
        # 設一個高 ATR 讓 ATR cap 不擋它
        pred, snap = _make_dfs(
            pred_overrides=dict(pred_return_20d=0.40),
            snap_overrides=dict(atr_pct=0.10),   # cap = 1.0，pred 0.40 不被 ATR cap 改動
        )
        out = _apply_recommendation_rules_core(pred, snap)
        assert out.loc[0, "recommendation"] == "觀望（預測值異常）"


# ============================================================================
# CLAUDE.md 規則 #10：偏熱乖離硬擋（OVERHEAT_THRESHOLD = 0.30）
# 此處同時驗 drift bug 已修：app.py 與 predict.py 都用 0.30
# ============================================================================
class TestOverheatBlock:
    def test_above_plus_30pct_ma20_demotes(self):
        pred, snap = _make_dfs(snap_overrides=dict(price_vs_ma20=0.35))
        out = _apply_recommendation_rules_core(pred, snap)
        # price_ok = False → 不會升級到強力買進；下游不擋（不在 buy_labels 降級規則裡）
        # 但注意這條只擋強力買進；建議買進不擋（保留為 dual_track buy）
        # 所以結果應該是「建議買進」，且 risk_tags 含偏熱
        assert out.loc[0, "recommendation"] == "建議買進"
        assert "偏熱" in out.loc[0, "risk_tags"]

    def test_at_18pct_no_longer_blocks(self):
        # drift bug 修正前 app.py 用 0.18，現在用 0.30 → 18% 應仍可強力買進
        pred, snap = _make_dfs(snap_overrides=dict(price_vs_ma20=0.18))
        out = _apply_recommendation_rules_core(pred, snap)
        assert out.loc[0, "recommendation"] == "強力買進"
        assert "偏熱" not in out.loc[0, "risk_tags"]


# ============================================================================
# CLAUDE.md 規則 #11：本業虧損硬擋（轉機股例外）
# ============================================================================
class TestOperatingLossBlock:
    def test_op_loss_no_turnaround_demotes(self):
        pred, snap = _make_dfs(snap_overrides=dict(operating_margin_latest=-0.05))
        out = _apply_recommendation_rules_core(pred, snap)
        assert out.loc[0, "recommendation"] == "觀望（基本面警示）"

    def test_op_loss_with_turnaround_keeps_buy(self):
        # 同時提供修復訊號讓 turnaround_score >= 2
        pred, snap = _make_dfs(snap_overrides=dict(
            operating_margin_latest=-0.05,
            eps_qoq=0.10,                  # +1
            revenue_yoy_latest=0.20,       # +1
            margin_trend=0.05,             # +1 (≥ 0.03)
        ))
        out = _apply_recommendation_rules_core(pred, snap)
        # turnaround 成立 → 規則 #11：不可強力買進，但可保留建議買進
        assert out.loc[0, "recommendation"] in ("建議買進", "強力買進")
        # 確認硬擋沒生效 (推薦不應為「觀望（基本面警示）」)
        assert "基本面警示" not in str(out.loc[0, "recommendation"])


# ============================================================================
# CLAUDE.md 規則 #13：籌碼頂部背離硬擋（block 門檻 2.0）
# ============================================================================
class TestChipBearBlock:
    def test_chip_diverge_bear_block_threshold_demotes(self):
        pred, snap = _make_dfs(snap_overrides=dict(chip_diverge_bear=2.5))
        out = _apply_recommendation_rules_core(pred, snap)
        assert out.loc[0, "recommendation"] == "觀望（籌碼頂部背離）"

    def test_chip_diverge_bear_warn_only_does_not_demote(self):
        # 1.0 ≤ x < 2.0 只警示不擋
        pred, snap = _make_dfs(snap_overrides=dict(chip_diverge_bear=1.5))
        out = _apply_recommendation_rules_core(pred, snap)
        assert out.loc[0, "recommendation"] == "強力買進"
        assert "籌碼頂部背離" in out.loc[0, "risk_tags"]


# ============================================================================
# 處置股硬擋 + 高強度放行例外
# ============================================================================
class TestDispositionBlock:
    def test_disposition_blocks_recommendation(self, monkeypatch):
        monkeypatch.setattr(predict_module, "_load_disposition_set", lambda: {"2330"})
        pred, snap = _make_dfs()
        out = _apply_recommendation_rules_core(pred, snap)
        # 高 V2 (0.10 ≥ override 0.08) + 流動性 ≥ 1M + 無 op_loss/crash → override
        assert out.loc[0, "disposition_override"] == 1
        # override 通過後 recommendation 不會變 觀望（處置股）
        assert "處置股" not in str(out.loc[0, "recommendation"])

    def test_disposition_low_v2_blocks(self, monkeypatch):
        monkeypatch.setattr(predict_module, "_load_disposition_set", lambda: {"2330"})
        pred, snap = _make_dfs(pred_overrides=dict(pred_return_20d=0.05))  # V2 < override
        out = _apply_recommendation_rules_core(pred, snap)
        assert out.loc[0, "recommendation"] == "觀望（處置股）"


# ============================================================================
# 雙軌否決 / 強力賣出
# ============================================================================
class TestDualTrackVeto:
    def test_strong_sell_when_both_v1_v2_bear_strong(self):
        pred, snap = _make_dfs(pred_overrides=dict(
            signal="DOWN",
            up_prob=0.10, flat_prob=0.20, down_prob=0.70,  # edge = -0.60
            pred_return_20d=-0.08,
        ))
        out = _apply_recommendation_rules_core(pred, snap)
        assert out.loc[0, "recommendation"] == "強力賣出"

    def test_v1_v2_disagree_v2_strong_override(self):
        # V1 偏空但 V2 強勢看多 → V2 override，仍 buy
        pred, snap = _make_dfs(pred_overrides=dict(
            signal="DOWN",
            up_prob=0.20, flat_prob=0.30, down_prob=0.50,  # edge = -0.30
            pred_return_20d=0.10,                          # ≥ override 0.08
        ))
        out = _apply_recommendation_rules_core(pred, snap)
        assert out.loc[0, "recommendation"] == "建議買進"
        assert "V2強勢看多" in out.loc[0, "risk_tags"]
