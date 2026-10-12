"""rere 候選影子排序研究帳本:集保 4 週變化的可得日、分組、冪等記錄與超額計算。"""
import json

import numpy as np
import pandas as pd

import scripts.rere_shadow_rank_tracker as t


def _weekly(retail, whale, last="2026-10-02"):
    weeks = pd.date_range(end=last, periods=len(retail), freq="W-FRI")
    return pd.DataFrame({"week": weeks, "retail": retail, "whale": whale})


def test_tdcc_change_respects_publication_lag_and_staleness():
    w = _weekly([30, 29, 28, 27, 26, 20], [50, 51, 52, 53, 54, 60])
    assert t.tdcc_change(w, "2026-10-05") == (-9.0, 9.0)            # 10/02 週資料 +3 天 = 10/05 可用
    assert t.tdcc_change(w, "2026-10-04") == (-4.0, 4.0)            # 還不知道 10/02 那週,用到 9/25
    assert all(np.isnan(v) for v in t.tdcc_change(w, "2026-11-20"))  # 最近一週超過 21 天
    assert all(np.isnan(v) for v in t.tdcc_change(w.head(4), "2026-10-05"))
    assert all(np.isnan(v) for v in t.tdcc_change(None, "2026-10-05"))


def _plan(n=9, listed=("1000", "1001")):
    pool = [{"ticker": f"{1000 + i}", "stock": f"{1000 + i} 股{i}", "ptype": "shakeout_v2", "close": 100.0 + i,
             "wash": 0.1, "sec_tier": "neutral", "source_date": "2026-10-12"} for i in range(n)]
    rows = [{"stock": f"{tk} x", "status": "rere·蹲點型v2·小倉(60日,配停損)"} for tk in listed]
    rows += [{"stock": "1008 x", "status": "⚠️官方警示·剔除(rere)"}, {"stock": "2330 台積電", "status": "可小倉觸發"}]
    return {"trade_date": "2026-10-13", "as_of_date": "2026-10-12", "rows": rows, "shadow_rere_pool": pool}


def _tdcc(n=9):
    # 代號越大散戶減越多、大戶增越多
    return {f"{1000 + i}": _weekly([30, 30, 30, 30, 30, 30 - i], [50, 50, 50, 50, 50, 50 + i], last="2026-10-09") for i in range(n)}


def test_groups_rank_by_retail_decrease_and_whale_increase():
    picks = t.picks_from_plan(_plan(), _tdcc())
    by = {g: f["ticker"].tolist() for g, f in picks.groupby("group")}
    assert by["retail_top3"] == ["1007", "1006", "1005"] and by["whale_top3"] == ["1007", "1006", "1005"]
    assert sorted(by["listed"]) == ["1000", "1001"]
    assert len(by["pool"]) == 8 and "1008" not in by["pool"]        # 被官方公告剔除的 1008 不算候選
    few = t.picks_from_plan(_plan(n=5, listed=("1000",)), _tdcc(5))
    assert set(few["group"]) == {"pool", "listed"}                  # 不足 8 檔不排名
    assert t.picks_from_plan({"trade_date": "2026-10-13", "as_of_date": "2026-10-12", "rows": []}, {}).empty


def test_record_is_idempotent_per_trade_date(tmp_path):
    plan_path = tmp_path / "entry_list_20261013.json"
    plan_path.write_text(json.dumps(_plan(), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "picks.csv"
    first = t.record([plan_path], _tdcc(), out)
    second = t.record([plan_path], _tdcc(), out)
    assert len(first) == len(second) == 8 + 2 + 3 + 3
    assert len(pd.read_csv(out)) == len(first)


def test_book_excess_is_group_mean_minus_pool_mean_and_stays_observing():
    picks = t.picks_from_plan(_plan(), _tdcc())
    days = pd.bdate_range("2026-10-12", periods=4).strftime("%Y-%m-%d")
    bars = {f"{1000 + i}": pd.DataFrame({"adj": [100, 100, 100 + i, 100 + 2 * i]}, index=days) for i in range(9)}
    book = t.book_from_picks(picks, bars)
    daily, summary = t.summarize(book)
    top = daily[daily["group"] == "retail_top3"].iloc[0]
    assert round(top["ret"], 2) == 12.0 and round(top["pool_ret"], 2) == 7.0 and round(top["excess"], 2) == 5.0
    assert "觀察中" in t.verdict(daily)
    text = t.render(daily, summary, picks, "2026-10-15")
    assert "現行名單與排序不受影響" in text and "1007" in text


def test_generator_payload_exports_the_candidate_pool(monkeypatch):
    from scripts import generate_entry_candidates as gen
    monkeypatch.setattr(gen, "_name_map", lambda: {"6278": "台表科"})
    monkeypatch.setattr(gen, "_chipk_source_label", lambda result: "manual")
    result = {"as_of_close": None, "as_of_date": "2026-10-12", "rows": [], "shadow_legacy_main": [],
              "shadow_rere_pool": [{"ticker": "6278", "ptype": "shallow_v2", "close": 272.5, "wash": 0.09,
                                    "sec_tier": "strong", "source_date": "2026-10-12"}]}
    payload = gen.to_entry_json(result, "2026-10-13")
    assert payload["shadow_rere_pool"][0]["stock"] == "6278 台表科" and payload["rows"] == []
