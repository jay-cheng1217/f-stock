from datetime import date
import json
from types import SimpleNamespace

import pandas as pd
import pytest

import twstock
from scripts import backfill_eps as eps
from scripts import backfill_balance_sheet as bs
from scripts import fundamentals_completeness_audit as audit
from scripts import quarterly_source_contract as contract


def eps_html(market, *, tickers=None, value=2.0):
    tickers = tickers or ([str(1100+i) for i in range(5)] if market == "TWSE" else [str(6100+i) for i in range(5)])
    prefix = "上市" if market == "TWSE" else "上櫃"
    return f'<h2>{prefix}公司第二季資料</h2><table class="hasBorder">' + "".join(
        f"<tr><td>{t}</td><td>Name {t}</td><td>ignored</td><td>{value}</td></tr>" for t in tickers) + "</table>"


def anchor_payload(frame, *, year=115):
    return [{"年度": str(year), "季別": "2", "公司代號": t,
             "基本每股盈餘（元）": str(v)} for t, v in zip(frame.Ticker, frame.EPS_Basic)]


def proof(market):
    html = eps_html(market)
    return {**contract.validate_html_period(html, "eps", 2026, 2, market),
            "kind": "eps", "html": html, "year_evidence": "fixture"}


def quarter_frame():
    parts = []
    for market in ("TWSE", "OTC"):
        frame = eps.parse_mops_eps_tables(eps_html(market))
        frame["Market"], frame["Year"], frame["Season"] = market, 2026, 2
        parts.append(frame)
    return pd.concat(parts, ignore_index=True)


@pytest.mark.parametrize("selector,prefix", [
    (eps.select_pending_quarters, "eps"),
    (bs.select_pending_quarters, "bs"),
    (twstock._select_pending_financial_quarters, "financial"),
])
def test_complete_latest_is_always_pending(selector, prefix, tmp_path):
    for quarter in (1, 2):
        pd.DataFrame({"Ticker": [str(1000+i) for i in range(100)]}).to_csv(
            tmp_path / f"{prefix}_2026Q{quarter}.csv", index=False)
    assert selector([(2026, 1), (2026, 2)], str(tmp_path)) == [(2026, 2)]


def test_writer_adds_reporter_retains_old_filing_and_preserves_true_nan(tmp_path):
    target = tmp_path / "quarter" / "eps_2026Q2.csv"
    target.parent.mkdir()
    before = quarter_frame()
    before.to_csv(target, index=False)
    fresh = before.loc[before.Ticker.ne("1100")].copy()
    fresh.loc[fresh.Ticker.eq("1101"), "EPS_Basic"] = float("nan")
    new = before.loc[before.Ticker.eq("1100")].assign(Ticker="1999", EPS_Basic=9.0)
    fresh = pd.concat([fresh, new], ignore_index=True)
    receipt = contract.write_verified_quarter(fresh, target, evidence={m: proof(m) for m in ("TWSE", "OTC")})
    actual = pd.read_csv(target, dtype={"Ticker": str}).set_index("Ticker")
    assert len(actual) == 11 and actual.loc["1999", "EPS_Basic"] == 9.0
    assert actual.loc["1100", "EPS_Basic"] == 2.0
    assert pd.isna(actual.loc["1101", "EPS_Basic"])
    assert receipt["retained_previously_filed_tickers"] == ["1100"]
    assert len(receipt["sources"]["TWSE"]["html_sha256"]) == 64


@pytest.mark.parametrize("defect", ["duplicate", "quarter", "schema", "single_market", "infinite"])
def test_invalid_fresh_data_preserves_existing_bytes(tmp_path, defect):
    frame = quarter_frame()
    target = tmp_path / "eps_2026Q2.csv"
    frame.to_csv(target, index=False)
    original = target.read_bytes()
    if defect == "duplicate":
        frame = pd.concat([frame, frame.iloc[:1]])
    elif defect == "quarter":
        frame["Season"] = 1
    elif defect == "schema":
        frame = frame.drop(columns="EPS_Basic")
    elif defect == "single_market":
        frame = frame.loc[frame.Market.eq("TWSE")]
    else:
        frame.loc[0, "EPS_Basic"] = float("inf")
    with pytest.raises(ValueError):
        contract.write_verified_quarter(frame, target, evidence={m: proof(m) for m in ("TWSE", "OTC")})
    assert target.read_bytes() == original


def test_complete_official_response_repairs_bad_old_schema_and_values(tmp_path):
    fresh = quarter_frame()
    old = fresh.drop(columns="Name").copy()
    old.loc[0, "EPS_Basic"] = float("inf")
    target = tmp_path / "eps_2026Q2.csv"
    old.to_csv(target, index=False)
    contract.write_verified_quarter(fresh, target, evidence={m: proof(m) for m in ("TWSE", "OTC")})
    actual = pd.read_csv(target)
    assert "Name" in actual and actual.EPS_Basic.eq(2).all()


def test_unverifiable_omitted_old_filing_blocks_without_deleting(tmp_path):
    fresh = quarter_frame()
    old = fresh.drop(columns="Name")
    target = tmp_path / "eps_2026Q2.csv"
    old.to_csv(target, index=False)
    original = target.read_bytes()
    with pytest.raises(ValueError, match="retained omitted"):
        contract.write_verified_quarter(fresh.iloc[1:], target, evidence={m: proof(m) for m in ("TWSE", "OTC")})
    assert target.read_bytes() == original


def test_bs_accounting_identity_is_checked_only_when_known():
    frame = quarter_frame().drop(columns="EPS_Basic")
    for column in contract.METRICS["bs"]:
        frame[column] = 1.0
    frame.Total_Assets = 10
    frame.Total_Liabilities = 4
    frame.Total_Equity = 6
    contract.validate_quarter_frame(frame, "bs", 2026, 2)
    frame.loc[0, "Total_Equity"] = float("nan")
    contract.validate_quarter_frame(frame, "bs", 2026, 2)
    frame.loc[1, "Total_Equity"] = 0
    with pytest.raises(ValueError, match="accounting identity"):
        contract.validate_quarter_frame(frame, "bs", 2026, 2)


def test_period_proof_uses_heading_not_data_values():
    proof = contract.validate_html_period('<h2>上市公司115年度第二季</h2>', "financial", 2026, 2, "TWSE")
    assert proof["response_year_explicit"] is True
    for html in ('<h2>上市公司114年度第二季</h2><td>115</td>', '<h2>上櫃公司115年度第二季</h2>', '<h2>上市公司115年度第一季</h2>'):
        with pytest.raises(ValueError, match="heading"):
            contract.validate_html_period(html, "financial", 2026, 2, "TWSE")
    proof = contract.validate_html_period(eps_html("TWSE"), "eps", 2026, 2, "TWSE")
    assert proof["response_year_explicit"] is False and proof["request_year"] == 2026


def test_latest_anchor_rejects_wrong_year_and_values():
    frame = eps.parse_mops_eps_tables(eps_html("TWSE"))
    assert len(contract.validate_latest_anchor(anchor_payload(frame), frame, "eps", 2026, 2, "TWSE")["sample_tickers"]) == 5
    with pytest.raises(ValueError, match="year/quarter"):
        contract.validate_latest_anchor(anchor_payload(frame, year=114), frame, "eps", 2026, 2, "TWSE")
    changed = anchor_payload(frame)
    changed[0]["基本每股盈餘（元）"] = "9.9"
    with pytest.raises(ValueError, match="value mismatch"):
        contract.validate_latest_anchor(changed, frame, "eps", 2026, 2, "TWSE")


def _configure_eps_main(monkeypatch, tmp_path):
    monkeypatch.setattr(eps, "EPS_DIR", str(tmp_path))
    monkeypatch.setattr(eps, "get_latest_available_quarter", lambda: (2026, 2))
    monkeypatch.setattr(eps, "select_pending_quarters", lambda quarters: [(2026, 2)])
    monkeypatch.setattr(eps, "_previous_reference_rows", lambda *a: 0)
    monkeypatch.setattr(eps.time, "sleep", lambda *a: None)
    monkeypatch.setattr("sys.argv", ["backfill_eps", "--start-year", "2026", "--start-quarter", "2"])


def test_eps_cli_failed_source_returns_nonzero_and_keeps_existing(monkeypatch, tmp_path):
    _configure_eps_main(monkeypatch, tmp_path)
    path = tmp_path / "eps_2026Q2.csv"
    quarter_frame().to_csv(path, index=False)
    before = path.read_bytes()
    monkeypatch.setattr(eps, "fetch_mops_eps", lambda *a: None)
    assert eps.main() == 1
    assert path.read_bytes() == before


def test_eps_cli_real_parse_verify_merge_write_flow(monkeypatch, tmp_path):
    _configure_eps_main(monkeypatch, tmp_path)
    monkeypatch.setattr(eps, "fetch_mops_eps", lambda y, q, t: eps_html("TWSE" if t == "sii" else "OTC"))
    def get(url, timeout):
        market = "TWSE" if "openapi.twse" in url else "OTC"
        payload = anchor_payload(eps.parse_mops_eps_tables(eps_html(market)))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload, text=json.dumps(payload))
    monkeypatch.setattr(eps.SESSION, "get", get)
    assert eps.main() == 0
    assert len(pd.read_csv(tmp_path / "eps_2026Q2.csv")) == 10


def _healthy_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(audit, "_check_quarterly", lambda *a: None)
    monkeypatch.setattr(audit, "_quarter_content_gap", lambda *a: None)
    monkeypatch.setattr(audit, "_csv_rows", lambda *a: 100)
    monkeypatch.setattr(audit, "_check_monthly_revenue", lambda *a: None)
    monkeypatch.setattr(audit, "_check_tdcc_freshness", lambda *a: None)
    monkeypatch.setattr(audit, "_check_semantic_invariants", lambda *a: [])


def test_no_heal_is_network_and_subprocess_free(monkeypatch, tmp_path):
    _healthy_audit(monkeypatch, tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("read-only audit attempted subprocess/network")
    monkeypatch.setattr(audit.subprocess, "run", forbidden)
    assert audit.run_audit(heal=False, today=date(2026, 9, 6)) == []


def test_healthy_coverage_heal_refreshes_three_and_propagates_failure(monkeypatch, tmp_path):
    _healthy_audit(monkeypatch, tmp_path)
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=1 if "backfill_eps.py" in str(cmd) else 0,
                               stdout=b"producer output", stderr=b"source detail")
    monkeypatch.setattr(audit.subprocess, "run", run)
    monkeypatch.setattr(audit.time, "sleep", lambda *_: None)
    gaps = audit.run_audit(heal=True, today=date(2026, 9, 6))
    # EPS is retried HEAL_RETRY_ATTEMPTS times before it counts as a gap.
    assert len(calls) == 2 + audit.HEAL_RETRY_ATTEMPTS
    assert len(gaps) == 1 and "exit 1" in gaps[0]
    report = json.loads((tmp_path / "logs/fundamentals_audit_20260906.json").read_text(encoding="utf-8"))
    assert report["status"] == "GAPS" and len(report["refresh_attempts"]) == 3
    failed = next(r for r in report["refresh_attempts"] if r["returncode"])
    assert failed["attempts"] == audit.HEAL_RETRY_ATTEMPTS
    from pathlib import Path
    diagnostic = json.loads(Path(failed["diagnostic_path"]).read_text(encoding="utf-8"))
    assert diagnostic["stderr_tail"] == "source detail"


def test_transient_source_failure_recovers_on_retry_without_gap(monkeypatch, tmp_path):
    """TPEx returned 520 once on 2026-09-08 minutes after the pipeline's own EPS
    step succeeded; one bad response must not raise a DATA GAPS alert."""
    _healthy_audit(monkeypatch, tmp_path)
    eps_calls = []
    def run(cmd, **kwargs):
        if "backfill_eps.py" in str(cmd):
            eps_calls.append(cmd)
            return SimpleNamespace(returncode=1 if len(eps_calls) == 1 else 0, stdout=b"", stderr=b"520")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    monkeypatch.setattr(audit.subprocess, "run", run)
    monkeypatch.setattr(audit.time, "sleep", lambda *_: None)
    gaps = audit.run_audit(heal=True, today=date(2026, 9, 6))
    assert gaps == [] and len(eps_calls) == 2
    report = json.loads((tmp_path / "logs/fundamentals_audit_20260906.json").read_text(encoding="utf-8"))
    assert report["status"] == "OK"
    assert next(r for r in report["refresh_attempts"] if r["label"] == "季EPS")["attempts"] == 2
