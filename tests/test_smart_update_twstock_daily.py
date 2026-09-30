import scripts.smart_update as smart_update


def test_exec_twstock_daily_skips_revenue(monkeypatch):
    calls = []

    def fake_run_job(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(smart_update, "run_job", fake_run_job)

    smart_update.exec_twstock_daily()

    args, kwargs = calls[0]
    assert args[0] == "twstock_daily"
    assert "--skip-revenue" in args[1]
    assert "--skip-snapshot-cache" in args[1]
    assert kwargs["timeout"] == "twstock_daily"
    assert kwargs["raise_on_fail"] is True


def test_exec_twstock_full_keeps_monthly_revenue(monkeypatch):
    calls = []

    def fake_run_job(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(smart_update, "run_job", fake_run_job)

    smart_update.exec_twstock()

    args, kwargs = calls[0]
    assert args[0] == "twstock"
    assert "--skip-revenue" not in args[1]
    assert kwargs["timeout"] == "twstock_full"
