from scripts import daily_pipeline


def test_agent_arena_skips_when_weekly_retrain_retains_web_port(monkeypatch, capsys):
    monkeypatch.setenv("WEEKLY_RETRAIN_WEB_PORT_RETAINED", "1")

    daily_pipeline.run_agent_arena()

    out = capsys.readouterr().out
    assert "Agent Arena skipped because weekly retrain retained" in out


def test_agent_arena_releases_web_db_before_running(monkeypatch):
    from scripts import agent_arena, smart_update

    monkeypatch.delenv("WEEKLY_RETRAIN_WEB_PORT_RETAINED", raising=False)
    calls: list[str] = []
    monkeypatch.setattr(smart_update, "_api_post", lambda path, timeout=10: calls.append(path) or True)
    monkeypatch.setattr(agent_arena, "run_daily_competition", lambda: calls.append("arena"))

    daily_pipeline.run_agent_arena()

    assert calls == ["/api/db/release", "arena"]


def test_agent_arena_runs_when_web_is_down(monkeypatch):
    from scripts import agent_arena, smart_update

    monkeypatch.delenv("WEEKLY_RETRAIN_WEB_PORT_RETAINED", raising=False)
    ran: list[bool] = []
    monkeypatch.setattr(smart_update, "_api_post", lambda path, timeout=10: False)
    monkeypatch.setattr(agent_arena, "run_daily_competition", lambda: ran.append(True))

    daily_pipeline.run_agent_arena()

    assert ran == [True]
