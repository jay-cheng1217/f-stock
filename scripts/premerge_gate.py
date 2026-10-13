# -*- coding: utf-8 -*-
"""合併前/後的完整測試關卡(2026-10-13 建立)。

為什麼需要:過去每次合併只跑「相關」的幾支測試。2026-09-30~10-13 因此漏掉三件事——
策略函式指紋測試早已不符、10/7 改了名單流程步驟卻沒更新對應測試、10/12 新測試把假告警寫進正式排程 log。
完整測試只要約 40 秒,沒有理由只跑一部分。

用法:
  python -X utf8 scripts/premerge_gate.py --mode worktree    # 在分支工作目錄,合併前
  python -X utf8 scripts/premerge_gate.py --mode live        # 在主工作目錄,合併後(或任何收盤後時間)
  python -X utf8 scripts/premerge_gate.py --mode cert-impact --branch <分支>   # 這次合併會不會讓憑證失效
判定:
  允許失敗的只有登記在案的測試——
    tests/gate_known_failures.txt        兩種模式都允許:已知、待裁決的真失敗(每列要寫日期與原因)
    tests/gate_worktree_env_failures.txt 只有 worktree 模式允許:需要主工作目錄的日K/模型檔,分支工作目錄沒有
  名單外的任何失敗 → 結束碼 1(不可合併 / 不可推送)。
  名單上的測試如果通過了,會印出提醒(名單過期,應該刪掉該列),不擋。
  測試前後 `git status --porcelain` 的追蹤檔異動若不同 → 結束碼 1(有測試在改正式檔案)。
結果另寫 logs/premerge_gate_latest.json。

cert-impact:列出分支相對 base 改到的檔案中,屬於快照/預測/名單憑證來源者(直接向三個憑證模組要來源清單,不另外維護名單)。
  有命中 → 合併後當日名單 API 會回 503,直到下一次 Phase-2 重新認證;交易日 18:00 前不可合併。結束碼 3。
  2026-10-12 我以為 ml/predict.py 不在來源內而在收盤後合併,名單 API 斷了 45 分鐘——它在預測憑證的來源清單裡。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KNOWN = REPO / "tests" / "gate_known_failures.txt"
WORKTREE_ENV = REPO / "tests" / "gate_worktree_env_failures.txt"
FAILED_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)")


def load_list(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            out.add(entry)
    return out


def parse_failures(output: str) -> set[str]:
    return {m.group(1) for line in output.splitlines() if (m := FAILED_RE.match(line.strip()))}


def evaluate(failed: set[str], known: set[str], worktree_env: set[str], mode: str) -> dict:
    allowed = known | (worktree_env if mode == "worktree" else set())
    return {"unexpected": sorted(failed - allowed), "allowed_failed": sorted(failed & allowed),
            "stale_entries": sorted(allowed - failed)}


def certificate_source_patterns() -> list[str]:
    """三層憑證的來源樣式,轉成相對於 repo 根目錄的 posix 路徑(repo 以外的來源略過)。"""
    sys.path.insert(0, str(REPO))
    from ml import prediction_provenance, snapshot_lineage
    from scripts import entry_artifact_lineage

    patterns = set(snapshot_lineage.canonical_source_patterns()) | set(str(p) for p in entry_artifact_lineage._extra_patterns())
    for model_paths in ({}, {"dataA": {}}):
        patterns |= set(prediction_provenance._prediction_patterns(model_paths))
    root = str(REPO.resolve()).replace("\\", "/").lower()
    out = set()
    for pattern in patterns:
        norm = str(pattern).replace("\\", "/")
        if norm.lower().startswith(root + "/"):
            out.add(norm[len(root) + 1:])
    return sorted(out)


def cert_impact(changed: list[str], patterns: list[str]) -> list[str]:
    import fnmatch

    lowered = [p.lower() for p in patterns]
    return sorted(f for f in changed if any(fnmatch.fnmatch(f.replace("\\", "/").lower(), p) for p in lowered))


def tracked_changes() -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    return sorted(line for line in out.splitlines() if line and not line.startswith("??"))


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["worktree", "live", "cert-impact"], required=True)
    parser.add_argument("--branch", help="cert-impact:要合併的分支")
    parser.add_argument("--base", default="main", help="cert-impact:比較基準(預設 main)")
    args = parser.parse_args()

    if args.mode == "cert-impact":
        if not args.branch:
            parser.error("--mode cert-impact 需要 --branch")
        diff = subprocess.run(["git", "diff", "--name-only", f"{args.base}...{args.branch}"], cwd=REPO, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        if diff.returncode != 0:
            print(f"[gate:cert-impact] git diff 失敗:{diff.stderr.strip()[:200]}")
            return 2
        hits = cert_impact([line.strip() for line in diff.stdout.splitlines() if line.strip()], certificate_source_patterns())
        if hits:
            print("[gate:cert-impact] 會讓憑證失效的檔案:" + "、".join(hits))
            print("[gate:cert-impact] 合併後當日名單 API 會 503,直到 Phase-2 重新認證;交易日 18:00 前不可合併")
            return 3
        print("[gate:cert-impact] 沒有改到任何憑證來源,合併不影響名單 API")
        return 0

    before = tracked_changes()
    # 一定要指定 tests/:主工作目錄的 output/(舊程式副本)與 scripts/*_test.py(研究腳本)會被 pytest 當成測試蒐集而報錯,
    # 真正的測試反而沒跑(2026-10-13 第一次合併後關卡因此擋下)。分支工作目錄沒有 output/,所以合併前看不出來。
    proc = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "--tb=no", "-rfE"],
                          cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    after = tracked_changes()
    output = proc.stdout + "\n" + proc.stderr
    tail = next((line for line in reversed(output.splitlines()) if " passed" in line or " failed" in line or " error" in line), "")
    result = evaluate(parse_failures(output), load_list(KNOWN), load_list(WORKTREE_ENV), args.mode)
    side_effects = sorted(set(after) - set(before))
    collected = bool(re.search(r"\d+ passed", tail))
    ok = collected and not result["unexpected"] and not side_effects
    report = {"mode": args.mode, "at": datetime.now().isoformat(timespec="seconds"), "ok": ok, "pytest_summary": tail.strip(),
              "tracked_files_changed_by_tests": side_effects, **result}
    try:
        (REPO / "logs").mkdir(exist_ok=True)
        (REPO / "logs" / "premerge_gate_latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    print(f"[gate:{args.mode}] {tail.strip()}")
    if not collected:
        print("[gate] 測試沒有正常執行完(找不到通過數),視為失敗")
    for name in result["unexpected"]:
        print(f"[gate] 名單外的失敗:{name}")
    for name in side_effects:
        print(f"[gate] 測試改動了追蹤檔:{name}")
    for name in result["stale_entries"]:
        print(f"[gate] 提醒:{name} 已經通過,請從允許清單移除")
    print(f"[gate] 允許的失敗 {len(result['allowed_failed'])} 項;判定:{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
