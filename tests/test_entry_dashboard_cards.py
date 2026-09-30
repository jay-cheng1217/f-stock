from scripts.entry_dashboard import card_context, state_of, _verdict, _source_freshness, evidence_verdict, guard_context, close_context


def row(**overrides):
    return {"tk": "2330", "stock": "2330 台積電", "kind": "go", "lane": "main",
            "source_date": "2026-09-04", "zone": "100-105", "stop": "95", **overrides}


def plan(**overrides):
    return {"trade_date": "2026/09/07", "data_status": "OK",
            "model_source_date": "2026-09-04", **overrides}


def test_daily_go_is_observation_and_never_proves_intraday_trigger():
    result = card_context(row(), plan())
    assert result["label"] == "觀察 · 待盤中確認"
    assert "15–30" in result["action"]
    assert result["warnings"] == []


def test_veto_does_not_silently_become_small_position():
    value = row(kind="veto")
    assert state_of(value) == "veto"
    assert card_context(value, plan())["tone"] == "skip"


def test_unknown_legacy_and_mismatched_dates_cannot_show_ready_badge():
    assert card_context(row(source_date=None), {})["tone"] == "warn"
    mismatch = card_context(row(), plan(model_source_date="2026-09-03"))
    assert mismatch["label"] == "暫緩 · 待資料確認"
    assert "型態與模型資料日期不同" in mismatch["warnings"]


def test_rere_does_not_use_model_date_or_score_as_a_veto():
    result = card_context(row(lane="rere", kind="small", ret20d="-20%", ptype="ignition"),
                          plan(model_source_date="2026-09-03"))
    assert result["tone"] == "check"
    assert result["horizon"] == 60
    assert result["size"] == "只考慮最小試單"
    assert "放量點火" in result["basis"][0]


def test_bad_price_reference_and_future_source_are_not_actionable():
    assert card_context(row(zone="105-100"), plan())["tone"] == "warn"
    assert card_context(row(stop=None), plan())["tone"] == "warn"
    assert card_context(row(source_date="2026-09-07"), plan())["tone"] == "warn"
    future = _source_freshness("2026/09/07", [("model", "模型", "2026-09-07", "daily")])
    assert future[0]["status"] == "future"


def test_rere_high_conviction_never_displays_an_upgrade_from_minimum_size():
    value = row(strategy="rere", state="small", closev=100, _bull=True,
                _m={"ic": 100, "inet": 3000, "wr": 60})
    verdict, text = _verdict(value, {"t5": 3000, "t20": 3000, "operating_margin_latest": .2},
                             {"2330": (50, 3, 10, -2)})
    assert verdict == "check"
    assert "最小試單" in text
    assert all(word not in text for word in ("標準倉", "7成", "首選", "可升"))


def test_chip_veto_is_retained_and_ordinary_data_cannot_confirm_live_entry():
    value = row(strategy="rere", state="small")
    assert _verdict(value, {}, {"2330": (50, -2, 10, 2)})[0] == "skip"
    verdict, text = _verdict(row(strategy="main", state="go"), {}, {})
    assert verdict == "ok"
    assert "不代表分點或盤中已確認" in text


def test_stale_weekly_evidence_cannot_veto_current_rere_but_fresh_can():
    value = row(strategy="rere", state="small")
    weekly = {"2330": (50, -2, 10, 2)}
    sources = _source_freshness("2026/09/07", [
        ("snapshot", "四面向", "2026-09-04", "daily"),
        ("production", "正式標籤", "2026-09-04", "daily"),
        ("whale", "大戶雷達", "2026-07-03", "weekly"),
    ])
    verdict, message = evidence_verdict(value, {}, weekly, sources)
    assert verdict == "check"
    assert "大戶雷達非當期" in message
    sources[-1]["status"] = "fresh"
    assert evidence_verdict(value, {}, weekly, sources)[0] == "skip"


def test_missing_supplemental_sources_cannot_be_interpreted_as_no_risk():
    verdict, message = evidence_verdict(row(strategy="main", state="go"), {}, {}, [])
    assert verdict == "check"
    assert "需人工補充" in message


def test_guard_translation_preserves_exact_cause_and_separates_rere():
    raw = "MARKET_GUARDRAIL_CIRCUIT:Top50攔截率80.0%>60%; Top50雙軌分歧72.0%>40%"
    result = guard_context(raw)
    assert result["detail"] == raw
    assert "攔截比例偏高" in result["headline"]
    assert "分歧偏高" in result["headline"]
    assert "名單變動" not in result["headline"]
    assert "Champion" in result["scope"] and "rere" in result["scope"]
    assert "模型風險檢查警示" in result["headline"]
    assert "關閉" not in result["headline"]  # Prediction diagnostics do not close every unified gate.
    assert "當日正式訊號名單（unified）為準" in result["scope"]


def test_historical_close_position_is_dated_and_never_implies_live_entry():
    for value, expected, phrase in ((99, "below", "站回下緣"), (102, "inside", "僅上次收盤"), (106, "above", "不追價")):
        result = close_context(value, "2026-09-04", "100-105", "2026-09-04")
        assert result["date"] == "2026-09-04"
        assert result["position"] == expected
        assert phrase in result["message"]
    assert close_context(102, None, "100-105", "2026-09-04")["price"] is None
    assert close_context(102, "2026-09-07", "100-105", "2026-09-04")["price"] is None


def test_price_position_does_not_reclassify_generator_kind_or_card_state():
    for value in (90, 102, 110):
        result = card_context(row(last_close=value, last_close_date="2026-09-04"), plan())
        assert result["label"] == "觀察 · 待盤中確認"
        assert result["tone"] == "check"
