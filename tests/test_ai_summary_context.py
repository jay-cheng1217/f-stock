import pandas as pd

from scripts import ai_summary


def _write_predictions(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")


def test_collect_context_uses_latest_production_prediction_file(tmp_path, monkeypatch):
    model_dir = tmp_path / "ml" / "models"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr("ml.config.MODEL_DIR", str(model_dir))
    monkeypatch.setattr(ai_summary, "BASE_DIR", str(tmp_path))

    _write_predictions(
        model_dir / "predictions_v4_rank15_2026-04-30.csv",
        [
            {
                "date": "2026-04-30",
                "ticker": "9999",
                "recommendation": "強力買進",
                "pred_return_20d": 0.99,
                "sector": "其他電子業",
            }
        ],
    )
    _write_predictions(
        model_dir / "predictions_2026-05-05.csv",
        [
            {
                "date": "2026-05-05",
                "ticker": "1111",
                "recommendation": "強力買進",
                "pred_return_20d": 0.01,
                "sector": "半導體業",
            },
            {
                "date": "2026-05-05",
                "ticker": "2222",
                "recommendation": "強力買進",
                "pred_return_20d": 0.20,
                "sector": "半導體業",
            },
            {
                "date": "2026-05-05",
                "ticker": "3333",
                "recommendation": "建議買進",
                "pred_return_20d": 0.15,
                "sector": "其他電子業",
            },
        ],
    )

    context = ai_summary._collect_context()

    assert "預測日期: 2026-05-05" in context
    assert "9999" not in context
    assert context.index("2222") < context.index("1111")


def test_collect_context_skips_stale_t1_prediction(tmp_path, monkeypatch):
    model_dir = tmp_path / "ml" / "models"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr("ml.config.MODEL_DIR", str(model_dir))
    monkeypatch.setattr(ai_summary, "BASE_DIR", str(tmp_path))

    _write_predictions(
        model_dir / "predictions_2026-05-05.csv",
        [
            {
                "date": "2026-05-05",
                "ticker": "2222",
                "recommendation": "強力買進",
                "pred_return_20d": 0.20,
                "sector": "半導體業",
            }
        ],
    )
    _write_predictions(
        model_dir / "predictions_t1_2026-04-30.csv",
        [
            {
                "date": "2026-04-30",
                "ticker": "9999",
                "selected_for_trade": True,
                "hit_prob_3pct": 0.88,
            }
        ],
    )

    context = ai_summary._collect_context()

    assert "最新檔日期 2026-04-30 與 20D 預測日期 2026-05-05 不一致" in context
    assert "9999 | 觸及3%機率" not in context


def test_collect_context_uses_injected_email_leaderboard(tmp_path, monkeypatch):
    model_dir = tmp_path / "ml" / "models"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr("ml.config.MODEL_DIR", str(model_dir))
    monkeypatch.setattr(ai_summary, "BASE_DIR", str(tmp_path))

    all_pred_df = pd.DataFrame(
        [
            {
                "date": "2026-05-05",
                "ticker": "2222",
                "recommendation": "強力買進",
                "pred_return_20d": 0.20,
                "sector": "半導體業",
            },
            {
                "date": "2026-05-05",
                "ticker": "1111",
                "recommendation": "強力買進",
                "pred_return_20d": 0.01,
                "sector": "半導體業",
            },
        ]
    )
    leaderboard_df = pd.DataFrame(
        [
            {
                "ticker": "8888",
                "recommendation": "建議買進",
                "pred_return_20d": 0.08,
                "sector": "其他電子業",
            }
        ]
    )

    context = ai_summary._collect_context(
        prediction_date="2026-05-05",
        all_pred_df=all_pred_df,
        leaderboard_df=leaderboard_df,
    )

    assert "8888 | 建議買進" in context
    assert "2222 | 強力買進" not in context


def test_collect_context_reads_current_index_files(tmp_path, monkeypatch):
    model_dir = tmp_path / "ml" / "models"
    model_dir.mkdir(parents=True)
    index_dir = tmp_path / "大盤指數"
    index_dir.mkdir()
    monkeypatch.setattr("ml.config.MODEL_DIR", str(model_dir))
    monkeypatch.setattr(ai_summary, "BASE_DIR", str(tmp_path))

    _write_predictions(
        model_dir / "predictions_2026-05-05.csv",
        [
            {
                "date": "2026-05-05",
                "ticker": "2222",
                "recommendation": "強力買進",
                "pred_return_20d": 0.20,
                "sector": "半導體業",
            }
        ],
    )
    pd.DataFrame(
        [{"Date": "2026-05-05", "Close": 40769.29}]
    ).to_csv(index_dir / "index_TWII.csv", index=False, encoding="utf-8")

    context = ai_summary._collect_context()

    assert "TWII: 40769.29 (2026-05-05)" in context


def test_model_viewpoint_is_deterministic_from_email_leaderboard(tmp_path, monkeypatch):
    model_dir = tmp_path / "ml" / "models"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr("ml.config.MODEL_DIR", str(model_dir))
    monkeypatch.setattr(ai_summary, "BASE_DIR", str(tmp_path))

    all_pred_df = pd.DataFrame(
        [
            {
                "date": "2026-05-05",
                "ticker": "2222",
                "recommendation": "強力買進",
                "pred_return_20d": 0.20,
                "sector": "半導體業",
            }
        ]
    )
    leaderboard_df = pd.DataFrame(
        [
            {
                "ticker": "8888",
                "recommendation": "建議買進",
                "pred_return_20d": 0.08,
                "sector": "其他電子業",
            }
        ]
    )

    html = ai_summary._build_model_viewpoint_html(
        prediction_date="2026-05-05",
        all_pred_df=all_pred_df,
        leaderboard_df=leaderboard_df,
    )

    assert "【模型觀點】" in html
    assert "<strong>8888</strong>（建議買進，預估20D 8.0%）" in html
    assert "2222" not in html
    assert "T+1" not in html
    assert "模型觀點不另行改排序或改名單" in html


def test_production_signal_section_uses_unified_signals_and_handles_disabled(tmp_path, monkeypatch):
    model_dir = tmp_path / "ml" / "models"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr("ml.config.MODEL_DIR", str(model_dir))
    monkeypatch.setattr(ai_summary, "BASE_DIR", str(tmp_path))

    pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-12",
                "ticker": "3529",
                "production_gate_status": "DISABLED",
                "production_gate_reason": "20d_only_research_backfill",
                "rank_20d": 1,
                "pred_return_20d": 0.20,
                "target_units": 2,
            }
        ]
    ).to_csv(model_dir / "unified_signals_2026-05-12.csv", index=False, encoding="utf-8-sig")

    html = ai_summary._build_production_signal_html("2026-05-12")

    assert "【今日 production 訊號】" in html
    assert "今日 production gate 未開，無進場訊號" in html
    assert "<strong>3529</strong>" not in html
    assert "【模型觀點】" not in html


def test_production_signal_section_lists_only_open_unified_rows(tmp_path, monkeypatch):
    model_dir = tmp_path / "ml" / "models"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr("ml.config.MODEL_DIR", str(model_dir))
    monkeypatch.setattr(ai_summary, "BASE_DIR", str(tmp_path))

    pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-12",
                "ticker": "1111",
                "production_gate_status": "OPEN",
                "rank_20d": 2,
                "pred_return_20d": 0.08,
                "target_units": 1,
                "target_weight_ratio": 0.9985,
            },
            {
                "prediction_date": "2026-05-12",
                "ticker": "2222",
                "production_gate_status": "DISABLED",
                "rank_20d": 1,
                "pred_return_20d": 0.20,
                "target_units": 2,
            },
        ]
    ).to_csv(model_dir / "unified_signals_2026-05-12.csv", index=False, encoding="utf-8-sig")

    html = ai_summary._build_production_signal_html("2026-05-12")

    assert "<strong>1111</strong>" in html
    assert "<strong>2222</strong>" not in html
    assert "predictions_" not in html
    assert "99.9%" in html  # Preserve the signal's number; clarify its denominator.
    assert "訊號配置上限" in html
    assert "待成交部位預留接近全部帳本資金，存在集中風險" in html
    assert "此數字不表示已成交或目前既有持倉比例" in html
    assert "<th>目標權重</th>" not in html


def test_production_signal_section_hides_blocked_open_gate_rows(tmp_path, monkeypatch):
    model_dir = tmp_path / "ml" / "models"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr("ml.config.MODEL_DIR", str(model_dir))
    monkeypatch.setattr(ai_summary, "BASE_DIR", str(tmp_path))

    pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-12",
                "ticker": "1111",
                "production_gate_status": "OPEN",
                "signal_type": "20D_only",
                "tradability_status": "OPEN",
                "rank_20d": 1,
                "pred_return_20d": 0.10,
                "target_units": 2,
            },
            {
                "prediction_date": "2026-05-12",
                "ticker": "2222",
                "production_gate_status": "OPEN",
                "signal_type": "NONE",
                "tradability_status": "BLOCKED",
                "tradability_reason": "MARKET_REGIME_CLOSED",
                "rank_20d": 2,
                "pred_return_20d": 0.20,
                "target_units": 0,
            },
        ]
    ).to_csv(model_dir / "unified_signals_2026-05-12.csv", index=False, encoding="utf-8-sig")

    html = ai_summary._build_production_signal_html("2026-05-12")

    assert "<strong>1111</strong>" in html
    assert "<strong>2222</strong>" not in html


def test_market_summary_describes_sox_crash_rule_based(tmp_path, monkeypatch):
    index_dir = tmp_path / "大盤指數"
    index_dir.mkdir(parents=True)
    monkeypatch.setattr(ai_summary, "BASE_DIR", str(tmp_path))

    rows = [
        {"Date": "2026-05-11", "Close": 100.0},
        {"Date": "2026-05-12", "Close": 96.989},
    ]
    for _, filename, _ in ai_summary.MARKET_INDEX_FILES:
        pd.DataFrame(rows).to_csv(index_dir / filename, index=False, encoding="utf-8")

    html = ai_summary._build_market_summary_html("2026-05-12")

    assert "SOX" in html
    assert "顯著重挫" in html
    assert "穩健" not in html


def test_insert_model_viewpoint_replaces_generated_model_section():
    generated = "【今日盤勢】<p>ok</p>\n【模型觀點】<p><strong>9999</strong></p>\n【風險提示】<p>risk</p>"
    fixed = "<h3>【模型觀點】</h3><p><strong>8888</strong></p>"

    result = ai_summary._insert_model_viewpoint_section(generated, fixed)

    assert "9999" not in result
    assert "<strong>8888</strong>" in result
    assert result.index("【今日盤勢】") < result.index("【模型觀點】") < result.index("【風險提示】")


def test_daily_summary_context_excludes_t1_when_disabled(tmp_path, monkeypatch):
    model_dir = tmp_path / "ml" / "models"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr("ml.config.MODEL_DIR", str(model_dir))
    monkeypatch.setattr(ai_summary, "BASE_DIR", str(tmp_path))

    _write_predictions(
        model_dir / "predictions_2026-05-05.csv",
        [
            {
                "date": "2026-05-05",
                "ticker": "2222",
                "recommendation": "強力買進",
                "pred_return_20d": 0.20,
                "sector": "半導體業",
            }
        ],
    )
    _write_predictions(
        model_dir / "predictions_t1_2026-05-05.csv",
        [
            {
                "date": "2026-05-05",
                "ticker": "9999",
                "selected_for_trade": True,
                "hit_prob_3pct": 0.88,
            }
        ],
    )

    context = ai_summary._collect_context(include_model_selection=False, include_t1=False)

    assert "T+1" not in context
    assert "9999" not in context


def test_drop_blocked_daily_summary_terms_removes_t1_sentences():
    generated = (
        "【今日盤勢】<p>台股走勢穩定。T+1 隔日沖仍需觀察 selected_for_trade。"
        "20D 波段仍以正式選股表為準。</p>"
    )

    result = ai_summary._drop_blocked_daily_summary_terms(generated)

    assert "T+1" not in result
    assert "隔日沖" not in result
    assert "selected_for_trade" not in result
    assert "20D 波段仍以正式選股表為準" in result
