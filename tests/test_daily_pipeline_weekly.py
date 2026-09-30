from scripts import daily_pipeline


def test_agent_arena_skips_when_weekly_retrain_retains_web_port(monkeypatch, capsys):
    monkeypatch.setenv("WEEKLY_RETRAIN_WEB_PORT_RETAINED", "1")

    daily_pipeline.run_agent_arena()

    out = capsys.readouterr().out
    assert "Agent Arena skipped because weekly retrain retained" in out
