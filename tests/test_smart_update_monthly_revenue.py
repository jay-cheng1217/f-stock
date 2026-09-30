import scripts.smart_update as smart_update


def test_exec_monthly_revenue_uses_monthly_flag(monkeypatch):
    calls = []

    def fake_run_job(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(smart_update, "run_job", fake_run_job)

    smart_update.exec_monthly_revenue()

    args, kwargs = calls[0]
    assert args[0] == "monthly_revenue"
    # 走月營收 window 模式,不可帶 --skip-revenue
    assert "--monthly-revenue" in args[1]
    assert "--skip-revenue" not in args[1]
    # 營收抓失敗不可拖垮整條每日排程
    assert kwargs["raise_on_fail"] is False


def test_monthly_revenue_step_wired_into_daily_pipeline():
    import inspect
    import scripts.smart_update_auto as sua

    src = inspect.getsource(sua)
    # data_steps 必須含自動月營收步驟
    assert "exec_monthly_revenue" in src
    assert "11-15 publish window" in src
