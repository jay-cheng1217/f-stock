import sys
import types
from datetime import date

import twstock


def test_step1_unreadable_history_is_not_overwritten(tmp_path, monkeypatch):
    history = tmp_path / "2330.csv"
    history.write_bytes(b"\xff")
    original = history.read_bytes()

    def unexpected_download(*args, **kwargs):
        raise AssertionError("download must not run when existing history is unreadable")

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=unexpected_download))
    monkeypatch.setattr(twstock, "DAILY_K_DIR", str(tmp_path))
    monkeypatch.setattr(twstock, "TODAY", date(2026, 7, 17))
    monkeypatch.setattr(twstock, "update_existing_etf_daily_k", lambda: None)
    monkeypatch.setattr(twstock, "load_retired_tickers", lambda: set())
    # Step 1 discovers listed companies from the live exchanges first; keep this
    # test offline and independent of the roster report-date contract.
    from scripts import listed_company_discovery
    monkeypatch.setattr(listed_company_discovery, "discover_and_seed_listed_companies",
                        lambda **kwargs: {"company_count": 0, "seeded": [], "pending": []})

    twstock.step1_update_daily_k()

    assert history.read_bytes() == original

