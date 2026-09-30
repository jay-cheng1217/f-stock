"""Semantic checks for isolated 2026-09-04 -> 2026-09-07 acceptance."""
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
EXPECTED = "2026-09-04"
TRADE = "2026-09-07"


def emit(name, payload):
    path = BASE / "logs" / f"acceptance_{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, default=str))


def assert_isolated():
    expected = Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    assert BASE == expected and "output" in BASE.parts
    from backend.config import DUCKDB_PATH
    assert Path(DUCKDB_PATH).resolve().is_relative_to(BASE)


def inputs():
    from scripts import smart_update_auto as pipeline
    from scripts.disposition_contract import load_disposition_gate
    from backend.db.engine import get_conn, close_conn
    from ml.features.registry import FEATURE_REGISTRY, get_available_features
    gaps = pipeline._check_data_freshness(expected_asof=date.fromisoformat(EXPECTED))
    gaps += pipeline._check_phase2_input_freshness(datetime(2026, 9, 6, 12))
    contract = load_disposition_gate(BASE, target_date=TRADE, required_as_of=EXPECTED)
    db = get_conn(read_only=True)
    tables = {name: db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
              for name in ("daily_k", "revenue", "financials", "tdcc", "indices", "stock_list")}
    dup = db.execute('SELECT count(*) FROM (SELECT Ticker, Date FROM daily_k GROUP BY Ticker, Date HAVING count(*)>1)').fetchone()[0]
    latest = str(db.execute('SELECT max(Date) FROM daily_k').fetchone()[0])[:10]
    available = get_available_features()
    meta = db.execute('SELECT * FROM ingest_meta').fetchdf().to_dict("records")
    close_conn()
    result = {"input_gaps": gaps, "disposition": contract, "tables": tables, "daily_duplicates": dup,
              "latest_daily": latest, "feature_groups": list(available), "missing_groups": sorted(set(FEATURE_REGISTRY) - set(available)),
              "ingest_meta": meta}
    emit("inputs", result)
    assert not gaps and contract["complete"] and all(tables.values())
    assert dup == 0 and latest == EXPECTED and set(available) == set(FEATURE_REGISTRY)


def outputs():
    import pandas as pd
    import pickle
    from scripts.smart_update_auto import _check_phase2_output_freshness
    paths = BASE / "ml" / "models"
    prediction = pd.read_csv(paths / f"predictions_{EXPECTED}.csv", dtype={"ticker": str})
    shadow = pd.read_csv(paths / f"dataA_predictions_{EXPECTED}.csv", dtype={"ticker": str})
    unified = pd.read_csv(paths / f"unified_signals_{EXPECTED}.csv", dtype={"ticker": str})
    plan = json.loads((BASE / "logs" / "entry_list_20260907.json").read_text(encoding="utf-8"))
    with (paths / "snapshot_cache.pkl").open("rb") as handle:
        raw = pickle.load(handle)
    raw = raw["df"] if isinstance(raw, dict) else raw
    dates = pd.to_datetime(raw["Date"]).dt.strftime("%Y-%m-%d")
    current = dict(zip(raw["ticker"].astype(str).str.zfill(4), dates))
    assert shadow["source_date"].eq(shadow["ticker"].str.zfill(4).map(current)).all()
    row_dates = sorted({row.get("source_date") for row in plan["rows"]}, key=str)
    model_dates = sorted({row.get("model_source_date") for row in plan["rows"]}, key=str)
    gaps = _check_phase2_output_freshness(datetime(2026, 9, 6, 12))
    html = (BASE / "ml" / "reports" / "entry_dashboard_latest.html").read_text(encoding="utf-8")
    result = {"prediction_rows": len(prediction), "dataA_rows": len(shadow), "unified_rows": len(unified),
              "raw_rows": len(raw), "raw_date_counts": dates.value_counts().to_dict(), "candidate_rows": len(plan["rows"]),
              "candidate_source_dates": row_dates, "candidate_model_dates": model_dates, "candidate_kinds": pd.Series([r["kind"] for r in plan["rows"]]).value_counts().to_dict(),
              "rere_tickers": [r["stock"].split()[0] for r in plan["rows"] if r.get("lane") == "rere"],
              "output_gaps": gaps, "html_bytes": len(html.encode("utf-8")), "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
              "html_hash_scope": "UTF-8 text after universal-newline decoding; artifact_manifest supplies exact file-byte hashes"}
    emit("outputs", result)
    assert len(prediction) and len(shadow) and not gaps
    assert row_dates == [EXPECTED] and model_dates == [EXPECTED]
    assert plan["trade_date"] == TRADE and plan["as_of_date"] == EXPECTED
    assert all(r["stock"].split()[0] in html for r in plan["rows"])


def email():
    # Dummy settings exercise the real composer without copying credentials.
    # The installed guard independently blocks SMTP even if dry_run regresses.
    for key in ("SMTP_FROM", "SMTP_TO", "SMTP_USER"):
        os.environ[key] = "acceptance@example.invalid"
    os.environ["SMTP_PASSWORD"] = "acceptance-dummy-no-credential"
    from scripts.send_daily_email import send_latest_email, load_email_settings, _build_email_message
    target = BASE / "logs" / "acceptance_email.html"
    result = send_latest_email(as_of_date=EXPECTED, dry_run=True, preview_path=str(target))
    assert result["status"] == "preview"
    html = target.read_text(encoding="utf-8")
    assert '<meta charset="utf-8"' in html.lower()
    assert "每日投資總結" in html and EXPECTED in html and "\ufffd" not in html
    message = _build_email_message(load_email_settings(), result["subjects"][0], html)
    parts = [part for part in message.walk() if part.get_content_maintype() == "text"]
    assert len(parts) == 2
    assert all(part.get_content_charset() == "utf-8" and part["Content-Transfer-Encoding"] == "base64" for part in parts)
    emit("email", {"status": "PASS", "delivery": "NOT_RUN", "preview": str(target),
                   "html_bytes": len(html.encode("utf-8")), "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                   "subject": result["subjects"][0], "mime": "plain/html UTF-8 base64", "credentials": "dummy .invalid addresses only",
                   "hash_scope": "UTF-8 text after universal-newline decoding; artifact_manifest supplies exact file-byte hashes"})


def health():
    os.environ["ADMIN_TOKEN"] = "isolated-acceptance-dummy-token"
    from fastapi.testclient import TestClient
    from backend.db.engine import get_conn, close_conn
    import app as application
    # Establish the real shared RW connection, as production startup does.
    # Do not start the unrelated asynchronous prediction-cache warmer.
    db = get_conn()
    assert db.execute("select count(*) from daily_k").fetchone()[0] > 0
    client = TestClient(application.app)
    try:
        denied = client.get("/api/pipeline/status")
        assert denied.status_code == 401
        stocks = client.get("/api/stocks/2330/daily?days=3")
        assert stocks.status_code == 200 and EXPECTED in stocks.text
        response = client.get("/api/pipeline/status", headers={"X-Admin-Token": os.environ["ADMIN_TOKEN"]})
        assert response.status_code == 200
        payload = response.json()
        daily = payload["daily_health"]
        assert daily["ok"] and not daily["stale"] and "error" not in daily["duckdb"]
        assert db.execute("select count(*) from daily_k").fetchone()[0] > 0
        emit("health", {"status": "PASS", "scope": "Actual ASGI middleware/routes and shared RW DuckDB; no lifespan or live-service reload",
                        "auth_denial_http": denied.status_code, "stock_http": stocks.status_code,
                        "health_http": response.status_code, "response": payload})
    finally:
        client.close()
        close_conn()


if __name__ == "__main__":
    assert_isolated()
    {"inputs": inputs, "outputs": outputs, "email": email, "health": health}[sys.argv[1]]()
