# -*- coding: utf-8 -*-
"""AGENTS.md 要保持「薄」:只放短規則與指引,細節在 docs。超出預算或指到不存在的檔案就失敗(2026-10-18 瘦身)。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
CHAR_BUDGET = 14_000           # 2026-10-18 瘦身後約 11.4k;要加長先問:這條細節能不能放進 docs、本檔只留一行指引?
PATH_RE = re.compile(r"(?<![\w/.])((?:docs|skills|scripts|tests|config)/[\w./-]+\.(?:md|py|json|yaml|txt|sh|csv))")


def test_agents_md_stays_within_its_budget():
    size = len(AGENTS.read_text(encoding="utf-8"))
    assert size <= CHAR_BUDGET, f"AGENTS.md {size} 字 > {CHAR_BUDGET};事故經過請寫進 docs/AGENT_LESSONS.md,本檔只加一行規則+指引"


LIVE_DOCS = ("AGENTS.md", "docs/SYSTEM_reference.md", "docs/SPEC_prediction_guardrails.md")   # 事故紀錄是歷史,不檢查


def test_every_file_agents_md_and_live_reference_docs_point_to_exists():
    """2026-10-18 抓到:10/17 清理把 AGENTS.md 仍要求的 scripts/notify_codex_done.py 當零引用封存了。"""
    missing = sorted({f"{doc}: {p}" for doc in LIVE_DOCS
                      for p in PATH_RE.findall((ROOT / doc).read_text(encoding="utf-8")) if not (ROOT / p).exists()})
    assert not missing, f"指到不存在的檔案(清理腳本前也要掃文件):{missing}"


def test_red_lines_stay_on_top_and_moved_docs_exist():
    text = AGENTS.read_text(encoding="utf-8")
    assert text.index("## ⛔ 動手前紅線") < text.index("## Codex Role Charter")
    assert all(f"{n}. **" in text.split("## Codex Role Charter")[0] for n in range(1, 7))
    for doc, heading in (("docs/AGENT_LESSONS.md", "## 官方來源與資料層"),
                         ("docs/SYSTEM_reference.md", "## 系統架構"),
                         ("docs/SPEC_prediction_guardrails.md", "### 第二層：財務常識防呆層")):
        assert heading in (ROOT / doc).read_text(encoding="utf-8"), doc


def test_claude_md_still_imports_agents_md():
    assert (ROOT / "CLAUDE.md").read_text(encoding="utf-8").startswith("@AGENTS.md")
