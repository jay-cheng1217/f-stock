import pandas as pd

from scripts import send_daily_email
from scripts.send_daily_email import EmailSettings, _build_email_message


def test_entry_candidate_section_renders_utf8_table(tmp_path, monkeypatch) -> None:
    latest = tmp_path / "momentum_continuation_backtest_20260625_latest_setups.csv"
    pd.DataFrame(
        [
            {
                "Date": "2026-06-24",
                "ticker": "6138",
                "name": "茂達",
                "sector": "半導體業",
                "Close": 362.0,
                "momentum_score": 0.6815,
                "momentum_entry_low": 358.5,
                "momentum_entry_limit": 362.0,
                "momentum_entry_high": 367.43,
                "volume_ratio_20d": 1.24,
                "RSI_14": 59.6,
                "MACDh_12_26_9": 3.38,
            }
        ]
    ).to_csv(latest, index=False, encoding="utf-8-sig")
    monkeypatch.setattr(
        send_daily_email,
        "MOMENTUM_LATEST_SETUP_GLOB",
        str(tmp_path / "momentum_continuation_backtest_*_latest_setups.csv"),
    )
    monkeypatch.setattr(
        send_daily_email,
        "UNIFIED_SIGNALS_LATEST_JSON_PATH",
        str(tmp_path / "missing_unified.json"),
    )
    special_status = tmp_path / "special_stock_status_latest.json"
    special_status.write_text(
        (
            '{"status":"OK","effective_date":"'
            + send_daily_email.datetime.now().strftime("%Y-%m-%d")
            + '","gate_action":"ALLOW_T1_AND_DUAL","missing_sources":[]}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(send_daily_email, "SPECIAL_STATUS_REPORT_PATH", str(special_status))
    monkeypatch.setattr(send_daily_email, "DISPOSITION_ACTIVE_PATH", str(tmp_path / "missing_disposition.csv"))
    pred_df = pd.DataFrame(
        [
            {
                "ticker": "6138",
                "sector": "半導體業",
                "recommendation": "建議買進",
                "pred_return_20d": 0.0145,
                "shortwave_action": "WAIT_NEW_STRATEGY",
                "shortwave_strategy_tags": "macd_hist_positive;macd_improving;ml_buy_signal",
                "shortwave_missing_strategy_tags": "missing_friend_pullback_support;missing_volume_quiet",
            }
        ]
    )

    section = send_daily_email._build_entry_candidate_email_section("2026-06-24", pred_df)

    assert "進場區間候選表" in section
    assert "6138" in section
    assert "茂達" in section
    assert "研究條件 / 等待新策略確認" in section
    assert "正式 / 等待新策略確認" not in section
    assert "不代表 production 放行" in section
    assert "日K資料日：2026-06-24（盤後）" in section
    assert "不能證明盤中守穩或已成交" in section
    assert "358.50 ~ 367.43" in section
    assert "缺回落支撐確認" in section


def test_entry_candidate_section_excludes_sell_or_exit_bias(tmp_path, monkeypatch) -> None:
    latest = tmp_path / "momentum_continuation_backtest_20260625_latest_setups.csv"
    pd.DataFrame(
        [
            {
                "Date": "2026-06-24",
                "ticker": "8071",
                "name": "能率網通",
                "sector": "電子零組件業",
                "Close": 28.0,
                "momentum_score": 0.64,
                "momentum_entry_low": 27.58,
                "momentum_entry_limit": 28.0,
                "momentum_entry_high": 28.42,
                "volume_ratio_20d": 0.77,
                "RSI_14": 64.5,
                "MACDh_12_26_9": 0.28,
            }
        ]
    ).to_csv(latest, index=False, encoding="utf-8-sig")
    monkeypatch.setattr(
        send_daily_email,
        "MOMENTUM_LATEST_SETUP_GLOB",
        str(tmp_path / "momentum_continuation_backtest_*_latest_setups.csv"),
    )
    monkeypatch.setattr(
        send_daily_email,
        "UNIFIED_SIGNALS_LATEST_JSON_PATH",
        str(tmp_path / "missing_unified.json"),
    )
    special_status = tmp_path / "special_stock_status_latest.json"
    special_status.write_text(
        (
            '{"status":"OK","effective_date":"'
            + send_daily_email.datetime.now().strftime("%Y-%m-%d")
            + '","gate_action":"ALLOW_T1_AND_DUAL","missing_sources":[]}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(send_daily_email, "SPECIAL_STATUS_REPORT_PATH", str(special_status))
    monkeypatch.setattr(send_daily_email, "DISPOSITION_ACTIVE_PATH", str(tmp_path / "missing_disposition.csv"))
    pred_df = pd.DataFrame(
        [
            {
                "ticker": "8071",
                "recommendation": "建議賣出",
                "pred_return_20d": -0.027,
                "shortwave_action": "EXIT_BIAS",
            }
        ]
    )

    section = send_daily_email._build_entry_candidate_email_section("2026-06-24", pred_df)

    assert "8071" not in section
    assert "目前沒有動能研究或支撐區觀察候選" in section


def test_entry_candidate_records_exclude_active_disposition_without_prediction_flag(
    tmp_path,
    monkeypatch,
) -> None:
    latest = tmp_path / "momentum_continuation_backtest_20260625_latest_setups.csv"
    pd.DataFrame(
        [
            {
                "Date": "2026-06-24",
                "ticker": "3675",
                "name": "Dewei",
                "sector": "Semiconductor",
                "Close": 413.0,
                "momentum_score": 0.6435,
                "momentum_entry_low": 398.55,
                "momentum_entry_limit": 413.0,
                "momentum_entry_high": 419.19,
                "volume_ratio_20d": 2.78,
                "RSI_14": 64.5,
                "MACDh_12_26_9": 5.98,
            }
        ]
    ).to_csv(latest, index=False, encoding="utf-8-sig")
    monkeypatch.setattr(
        send_daily_email,
        "MOMENTUM_LATEST_SETUP_GLOB",
        str(tmp_path / "momentum_continuation_backtest_*_latest_setups.csv"),
    )
    special_status = tmp_path / "special_stock_status_latest.json"
    special_status.write_text(
        (
            '{"status":"OK","effective_date":"'
            + send_daily_email.datetime.now().strftime("%Y-%m-%d")
            + '","gate_action":"ALLOW_T1_AND_DUAL","missing_sources":[]}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(send_daily_email, "SPECIAL_STATUS_REPORT_PATH", str(special_status))
    active = tmp_path / "disposition_active.csv"
    today = send_daily_email.datetime.now().strftime("%Y-%m-%d")
    active.write_text(f"stock_id,period_start,period_end\n3675,{today},{today}\n", encoding="utf-8")
    monkeypatch.setattr(send_daily_email, "DISPOSITION_ACTIVE_PATH", str(active))
    pred_df = pd.DataFrame(
        [
            {
                "ticker": "3675",
                "recommendation": "buy",
                "pred_return_20d": 0.032,
                "shortwave_action": "WAIT_NEW_STRATEGY",
            }
        ]
    )

    records, _, stats = send_daily_email._build_entry_candidate_records("2026-06-24", pred_df)

    assert records == []
    assert stats["disposition_blocked"] == 1
    assert stats["excluded"] == 1


def test_email_message_forces_utf8_mime_parts() -> None:
    settings = EmailSettings(
        host="smtp.example.com",
        port=465,
        user="bot@example.com",
        password="secret",
        from_email="bot@example.com",
        to_emails=["user@example.com"],
        use_ssl=True,
        from_name="台股分析平台",
    )

    message = _build_email_message(
        settings,
        "[Stock ML] 遠端看盤連結使用說明",
        "<html><body><h1>操作指引</h1><p>請輸入 IP 後繼續。</p></body></html>",
    )

    raw = message.as_bytes()
    raw_lower = raw.lower()

    assert b"charset=\"utf-8\"" in raw_lower
    assert b"content-transfer-encoding: base64" in raw_lower
    assert "=?utf-8?" in raw.decode("ascii", errors="ignore").lower()

    text_parts = [part for part in message.walk() if part.get_content_maintype() == "text"]
    assert {part.get_content_subtype() for part in text_parts} == {"plain", "html"}
    assert all(part.get_content_charset() == "utf-8" for part in text_parts)
    assert "請使用支援 HTML" in text_parts[0].get_content()
    assert "操作指引" in text_parts[1].get_content()
