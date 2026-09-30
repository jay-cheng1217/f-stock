"""Historical filename dispatch stays separate from operational ledger rules."""
import json

from scripts import entry_filter_tracker as tracker


def test_backfill_only_dispatches_exact_dated_plans(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    plans = ["entry_list_20260703.json", "entry_list_20260907.json"]
    for name in reversed(plans):
        (logs / name).write_text(json.dumps({"rows": [{"ticker": "1111"}]}), encoding="utf-8")
    for name in ["entry_list_20260907.json.manifest.json", "entry_list_20260907.manifest.json",
                 "entry_list_latest.json", "entry_list_2026097.json", "entry_list_202609070.json",
                 "entry_list_20260907_backup.json", "entry_list_２０２６０９０７.json"]:
        (logs / name).write_text(json.dumps({"rows": 18}), encoding="utf-8")
    calls = []
    def record(path):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload["rows"], list)
        calls.append(path.name)
    monkeypatch.setattr(tracker, "BASE_DIR", tmp_path)
    monkeypatch.setattr(tracker, "record", record)
    monkeypatch.setattr(tracker, "check", lambda: calls.append("check"))
    tracker.backfill()
    assert calls == plans + ["check"]


def test_backfill_with_only_certificate_still_checks_without_recording(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "entry_list_20260907.json.manifest.json").write_text('{"rows":18}', encoding="utf-8")
    calls = []
    monkeypatch.setattr(tracker, "BASE_DIR", tmp_path)
    monkeypatch.setattr(tracker, "record", lambda path: calls.append("unexpected record"))
    monkeypatch.setattr(tracker, "check", lambda: calls.append("check"))
    tracker.backfill()
    assert calls == ["check"]
