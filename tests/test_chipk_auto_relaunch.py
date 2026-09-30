import argparse

import scripts.run_chipk_update as rcu


def _args(**over):
    base = dict(no_notify=True, notify_ok=False, archive_only=True,
               auto_relaunch=False, relaunch_wait=0, output_prefix=None, top_n=30)
    base.update(over)
    return argparse.Namespace(**base)


def test_auto_relaunch_recovers_stale_snapshot(monkeypatch):
    calls = {"archive": 0, "relaunch": 0, "notify": []}

    def fake_archive():
        calls["archive"] += 1
        # 第一次過期,重啟後第二次已刷新
        asof = "2020-01-01" if calls["archive"] == 1 else "2999-12-31"
        return {"archived": True, "asof_date": asof, "row_count": 100}

    def fake_relaunch(wait):
        calls["relaunch"] += 1
        return {"attempted": True, "launched": True, "app_path": "x", "waited_seconds": 0, "error": None}

    written = {}
    monkeypatch.setattr(rcu, "archive_latest_chipk_snapshot", fake_archive)
    monkeypatch.setattr(rcu, "_relaunch_chipk_app", fake_relaunch)
    monkeypatch.setattr(rcu, "_write_status", lambda p: written.update(p))
    monkeypatch.setattr(rcu, "_notify", lambda **k: calls["notify"].append(k["status"]))
    monkeypatch.setenv("CHIPK_DESKTOP_ENABLED", "1")

    rcu.run(_args(auto_relaunch=True))

    assert calls["relaunch"] == 1          # 有嘗試自動重啟
    assert calls["archive"] == 2           # 重啟後有重跑一次 archive
    assert written["status"] == "ok"       # 已自癒
    assert written["auto_heal"]["recovered"] is True


def test_no_relaunch_when_disabled(monkeypatch):
    calls = {"archive": 0, "relaunch": 0}

    def fake_archive():
        calls["archive"] += 1
        return {"archived": True, "asof_date": "2020-01-01", "row_count": 100}

    def fake_relaunch(wait):
        calls["relaunch"] += 1
        return {}

    monkeypatch.setattr(rcu, "archive_latest_chipk_snapshot", fake_archive)
    monkeypatch.setattr(rcu, "_relaunch_chipk_app", fake_relaunch)
    monkeypatch.setattr(rcu, "_write_status", lambda p: None)
    monkeypatch.setattr(rcu, "_notify", lambda **k: None)
    monkeypatch.setenv("CHIPK_DESKTOP_ENABLED", "1")

    rcu.run(_args(auto_relaunch=False))

    assert calls["relaunch"] == 0          # 未開啟則不重啟
    assert calls["archive"] == 1           # 只跑一次


def test_chipk_update_is_retired_by_default(monkeypatch):
    calls = {"archive": 0, "notify": 0}
    written = {}

    def fake_archive():
        calls["archive"] += 1
        return {"archived": True, "asof_date": "2999-12-31", "row_count": 100}

    monkeypatch.delenv("CHIPK_DESKTOP_ENABLED", raising=False)
    monkeypatch.delenv("STOCK_ENABLE_CHIPK_DESKTOP", raising=False)
    monkeypatch.setattr(rcu, "archive_latest_chipk_snapshot", fake_archive)
    monkeypatch.setattr(rcu, "_write_status", lambda p: written.update(p))
    monkeypatch.setattr(rcu, "_notify", lambda **k: calls.__setitem__("notify", calls["notify"] + 1))

    assert rcu.run(_args()) == 0
    assert written["status"] == "retired"
    assert written["user_action"] is None
    assert calls["archive"] == 0
    assert calls["notify"] == 0
