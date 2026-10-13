#!/usr/bin/env bash
# 把分支工作目錄的成果併回 main 的固定流程(2026-10-13;取代每次臨時寫的延後合併腳本)。
#
# 用法:bash scripts/merge_branch.sh <分支> <分支工作目錄> [--not-before YYYYMMDDHHMM]
# 流程:(1) 等到指定時間 (2) preflight 無 STOP (3) 在分支工作目錄跑完整測試關卡
#       (4) 憑證影響檢查:改到憑證來源且現在是交易日 18:00 前 → 拒絕
#       (5) 可快轉、與主工作目錄未提交的追蹤檔不重疊 (6) 快轉合併 (7) 主工作目錄再跑完整測試關卡,不過就不推送
#       (8) 推送 (9) 移除分支工作目錄與分支
# 任何一步失敗都停在當下並印出原因,不會自動回復;紀錄寫在 logs/merge_<分支>_<日期>.log。
set -u
if [ -z "${MERGE_BRANCH_REEXEC:-}" ]; then      # 合併或移除工作目錄可能改寫/刪掉本檔,先複製到暫存再執行
  tmp="$(mktemp)"; cp "$0" "$tmp"; MERGE_BRANCH_REEXEC=1 exec bash "$tmp" "$@"
fi
BRANCH="${1:?需要分支名稱}"; WT="${2:?需要分支工作目錄}"; shift 2
NOT_BEFORE=""
while [ $# -gt 0 ]; do case "$1" in --not-before) NOT_BEFORE="$2"; shift 2;; *) echo "未知參數 $1"; exit 64;; esac; done
LIVE="$(git -C "$WT" worktree list --porcelain | head -1 | sed 's/^worktree //')"
cd "$LIVE" || exit 1
LOG="logs/merge_$(echo "$BRANCH" | tr '/' '_')_$(date +%Y%m%d).log"
exec > >(tee -a "$LOG") 2>&1
echo "=== merge $BRANCH start $(date '+%F %T') (live=$LIVE, worktree=$WT) ==="
if [ -n "$NOT_BEFORE" ]; then while [ "$(date +%Y%m%d%H%M)" -lt "$NOT_BEFORE" ]; do sleep 30; done; echo "--- $(date '+%F %T') 到達指定時間"; fi

PF=$(python -X utf8 scripts/preflight_check.py 2>&1); echo "$PF" | tail -8
if echo "$PF" | grep -q "\[STOP\]"; then echo "ABORT(2): preflight 有 STOP"; exit 2; fi

git -C "$WT" status --porcelain | grep -v '^??' | grep -q . && { echo "ABORT(3): 分支工作目錄有未提交的異動"; exit 3; }
( cd "$WT" && python -X utf8 scripts/premerge_gate.py --mode worktree ) || { echo "ABORT(4): 分支完整測試未過"; exit 4; }

( cd "$WT" && python -X utf8 scripts/premerge_gate.py --mode cert-impact --branch "$BRANCH" --base main ); IMPACT=$?
if [ "$IMPACT" -eq 3 ]; then
  TRADING=$(python -X utf8 -c "import sys,datetime;sys.path.insert(0,'scripts');from taiwan_trading_calendar import is_taiwan_trading_day as t;print(int(t(datetime.date.today())))")
  if [ "$TRADING" = "1" ] && [ "$(date +%H%M)" -lt 1800 ]; then echo "ABORT(5): 改到憑證來源,交易日 18:00 前不可合併"; exit 5; fi
  echo "注意:本次合併會使憑證失效,由下一次 Phase-2 重新認證"
elif [ "$IMPACT" -ne 0 ]; then echo "ABORT(5): 憑證影響檢查失敗"; exit 5; fi

git merge-base --is-ancestor main "$BRANCH" || { echo "ABORT(6): 無法快轉(main 已前進,請在分支工作目錄 rebase)"; exit 6; }
OVERLAP=$(comm -12 <(git diff --name-only main "$BRANCH" | sort) <(git status --porcelain | grep -v '^??' | cut -c4- | sort))
if [ -n "$OVERLAP" ]; then echo "ABORT(7): 主工作目錄未提交的檔案與分支重疊:"; echo "$OVERLAP"; exit 7; fi
git merge --ff-only "$BRANCH" || { echo "ABORT(8): 合併失敗"; exit 8; }
echo "merged: $(git log --oneline -1)"

python -X utf8 scripts/premerge_gate.py --mode live || { echo "STOP(9): 合併後主工作目錄完整測試未過,未推送,請先處理"; exit 9; }
git push -q origin main && echo "pushed" || { echo "STOP(10): 推送失敗"; exit 10; }
git worktree remove "$WT" && git branch -d "$BRANCH" && echo "分支工作目錄與分支已移除"
git worktree list
echo "=== merge $BRANCH end $(date '+%F %T') ==="
