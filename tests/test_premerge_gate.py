"""合併前測試關卡的判定邏輯。"""
from scripts import premerge_gate as gate


def test_parse_failures_reads_pytest_short_summary():
    out = "....\nFAILED tests/a.py::test_x - AssertionError\nERROR tests/b.py::test_y\n3 failed, 10 passed in 1.0s\n"
    assert gate.parse_failures(out) == {"tests/a.py::test_x", "tests/b.py::test_y"}


def test_unlisted_failure_blocks_and_env_list_only_applies_to_worktree():
    known, env = {"tests/a.py::known"}, {"tests/b.py::needs_data"}
    failed = {"tests/a.py::known", "tests/b.py::needs_data"}
    assert gate.evaluate(failed, known, env, "worktree")["unexpected"] == []
    assert gate.evaluate(failed, known, env, "live")["unexpected"] == ["tests/b.py::needs_data"]
    assert gate.evaluate(failed | {"tests/c.py::new"}, known, env, "worktree")["unexpected"] == ["tests/c.py::new"]


def test_passing_listed_tests_are_reported_as_stale():
    result = gate.evaluate(set(), {"tests/a.py::known"}, {"tests/b.py::needs_data"}, "worktree")
    assert result["stale_entries"] == ["tests/a.py::known", "tests/b.py::needs_data"] and result["unexpected"] == []


def test_list_files_ignore_comments_and_exist(tmp_path):
    path = tmp_path / "list.txt"
    path.write_text("# header\n\ntests/a.py::t1  # 2026-10-13 reason\ntests/b.py::t2\n", encoding="utf-8")
    assert gate.load_list(path) == {"tests/a.py::t1", "tests/b.py::t2"}
    assert gate.load_list(gate.KNOWN) and gate.load_list(gate.WORKTREE_ENV)


def test_cert_impact_matches_code_and_data_sources():
    patterns = ["ml/predict.py", "ml/features/*.py", "config/trading_status_events.json", "日K資料/*.csv"]
    changed = ["ml/predict.py", "ml/features/tdcc.py", "docs/STRATEGY_overview.md", "scripts/premerge_gate.py",
               "config/trading_status_events.json"]
    assert gate.cert_impact(changed, patterns) == ["config/trading_status_events.json", "ml/features/tdcc.py", "ml/predict.py"]


def test_real_certificate_sources_include_the_files_that_bit_us():
    patterns = gate.certificate_source_patterns()
    for must in ("ml/predict.py", "ml/dataset.py", "ml/universe.py", "scripts/generate_entry_candidates.py",
                 "config/trading_status_events.json"):
        assert gate.cert_impact([must], patterns) == [must], must
    assert gate.cert_impact(["docs/rere_strategy.md", "scripts/rere_public_follow_tracker.py",
                             "ml/data/rere_public_campaigns.csv"], patterns) == []
