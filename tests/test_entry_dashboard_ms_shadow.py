# -*- coding: utf-8 -*-
"""#45 MS 動能影子名單在儀表板只呈現:當日 12 檔 + 影子帳本對同期主 lane 的摘要,標明研究用。"""
import pandas as pd

from scripts import entry_dashboard as ed


def test_ms_shadow_loader_combines_today_list_and_ledger_summary(tmp_path, monkeypatch):
    ms = tmp_path / "ms.csv"
    pd.DataFrame([{"trade_date": "2026-10-20", "ticker": "2330", "lane": "main_ms", "status": "expired", "ret_pct": 3.0},
                  {"trade_date": "2026-10-21", "ticker": "2454", "lane": "main_ms", "status": "holding", "ret_pct": ""}]
                 ).to_csv(ms, index=False)
    main = tmp_path / "main.csv"
    pd.DataFrame([{"trade_date": "2026-10-19", "ticker": "1101", "lane": "main", "status": "expired", "ret_pct": 9.0},
                  {"trade_date": "2026-10-20", "ticker": "1102", "lane": "main", "status": "stopped", "ret_pct": -4.0}]
                 ).to_csv(main, index=False)
    monkeypatch.setattr(ed, "MS_SHADOW_LEDGER", ms)
    plan = {"shadow_ms_main": [{"stock": "2330 台積電", "mom_6_1": 35.4, "zone": "1-2", "stop": "0.9"}]}
    out = ed.load_ms_shadow(plan, main)
    assert out["today"] == [{"stock": "2330 台積電", "mom": 35.4, "zone": "1-2", "stop": "0.9"}]
    assert out["since"] == "2026-10-20" and out["closed"] == 1 and out["mean"] == 3.0 and out["holding"] == 1
    assert out["new_closed"] == 1 and out["new_mean"] == -4.0          # 同期主 lane 只從影子帳本第一天算起
    monkeypatch.setattr(ed, "MS_SHADOW_LEDGER", tmp_path / "missing.csv")
    assert ed.load_ms_shadow({}, main) is None


def test_ms_shadow_section_is_rendered_and_marked_research_only():
    assert "msShadowSec() +" in ed.HTML and "D.ms_shadow" in ed.HTML
    assert "研究用，不是交易名單" in ed.HTML
