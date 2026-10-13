"""日K終盤校驗告警改看「價格實質不符」:浮點尾數與成交量差異照修,但不該每晚觸發告警(2026-10-13)。"""
import logging
import subprocess
from datetime import date

import pandas as pd
import pytest

from scripts import smart_update_auto, verify_otc_bars


def test_float32_noise_is_not_material_but_real_gaps_and_missing_values_are():
    assert verify_otc_bars._is_material_price_change(89.0999984741211, 89.1) is False
    assert verify_otc_bars._is_material_price_change(89.1, 89.1) is False
    assert verify_otc_bars._is_material_price_change(89.1, 90.0) is True
    assert verify_otc_bars._is_material_price_change(float("nan"), 90.0) is True
    assert verify_otc_bars._is_material_price_change(0.0, 0.0) is False


def test_verifier_counts_material_price_rows_separately(tmp_path, monkeypatch):
    daily = tmp_path / "daily"
    daily.mkdir()
    monkeypatch.setattr(verify_otc_bars, "DAILY", daily)
    monkeypatch.setattr(verify_otc_bars, "LOG", tmp_path / "logs")
    monkeypatch.setattr(verify_otc_bars, "CACHE", tmp_path / "logs" / "cache")
    days = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]

    def bars(close, volume=1000.0):
        return [{"Date": d, "Open": close, "High": close, "Low": close, "Close": close, "Volume": volume} for d in days]

    pd.DataFrame(bars(89.0999984741211)).to_csv(daily / "1111.csv", index=False, encoding="utf-8-sig")   # 只有尾數
    pd.DataFrame(bars(50.0, volume=900.0)).to_csv(daily / "2222.csv", index=False, encoding="utf-8-sig")  # 只有成交量
    pd.DataFrame(bars(20.0)).to_csv(daily / "3333.csv", index=False, encoding="utf-8-sig")               # 價格真的錯
    official = {"1111": 89.1, "2222": 50.0, "3333": 21.0}

    def fetch(day, cache_dir=None):
        d = day.strftime("%Y-%m-%d")
        return pd.DataFrame([{"Ticker": tk, "Date": d, "Open": px, "High": px, "Low": px, "Close": px, "Volume": 1000.0}
                             for tk, px in official.items()])

    summary = verify_otc_bars.run(lookback_trading_days=5, today=date(2026, 9, 21), fetch=fetch)
    assert summary["files_patched"] == 3 and summary["rows_patched"] == 15
    assert summary["price_rows_material"] == 5                                   # 只有 3333 的五列
    repair = pd.read_csv(next((tmp_path / "logs").glob("official_bar_repair_*.csv")), dtype={"ticker": str})
    assert repair.groupby("ticker")["price_material"].sum().to_dict() == {"1111": 0, "2222": 0, "3333": 5}


@pytest.mark.parametrize("stdout, expect_alarm, expect_text", [
    ("verify_official_bars: dates=5 files_patched=615 rows_patched=615 rows_dropped=0 price_rows_material=0", False, ""),
    ("verify_official_bars: dates=5 files_patched=900 rows_patched=900 rows_dropped=0 price_rows_material=120", True, "價格實質不符 120 列"),
    ("verify_official_bars: dates=5 files_patched=958 rows_patched=958 rows_dropped=0", True, "官方對照修復 958 檔"),   # 舊格式退回檔數
])
def test_alarm_triggers_on_material_price_rows(monkeypatch, stdout, expect_alarm, expect_text):
    from scripts import notify_ntfy

    quiet = logging.getLogger("test_verify_alarm_material")
    quiet.handlers.clear()
    quiet.addHandler(logging.NullHandler())
    quiet.propagate = False
    monkeypatch.setattr(smart_update_auto, "LOGGER", quiet)      # 不可寫進正式排程 log
    sent = []
    monkeypatch.setattr(notify_ntfy, "send_ntfy", lambda **kw: sent.append(kw))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout.encode("utf-8"), b""))
    assert smart_update_auto.exec_verify_daily_bars() is True
    assert bool(sent) is expect_alarm
    if expect_alarm:
        assert expect_text in sent[0]["message"] and sent[0]["priority"] == "high"
