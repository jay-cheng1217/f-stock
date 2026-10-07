---
name: stock-entry-decision-workflow
description: Evaluate Taiwan stock entry candidates with the required short-swing support workflow. Use when Codex discusses intraday entry, support/resistance zones, ChipK screenshots, friend-style short trades, entry filters, observation lists, or whether a stock can be bought now.
---

# Stock Entry Decision Workflow

## Overview

Use this skill to prevent mixing broad model watchlists with actual entry decisions. The required order is: short-swing support setup first, then local model overlay, then explicit entry zone, then ChipK veto when fresh manual evidence exists, then intraday trigger.

2026-07-03 update: ChipK **desktop automation is retired by default**. Do not treat stale desktop snapshots as a pipeline degradation, do not ask the user to reopen the desktop app, and do not read old desktop `chipk_model_diagnosis` / archive files as an automatic gate unless `CHIPK_DESKTOP_ENABLED=1` is explicitly set and the source is fresh. Mobile ChipK screenshots can still be used as a manual final veto.

## Required Decision Order

Never start from "model says good" or "price is in a broad zone" alone. Apply these gates in order.

1. **Short-swing support setup first**
   - Look for a fresh pullback or consolidation near support, not a stock that already exploded above the entry zone.
   - Require MA/MACD structure to be intact: support near MA5/MA20/MA60 or prior base, MACD not clearly broken, and price not closing below the support reference.
   - Require volume to be controlled. Volume expansion can confirm strength, but panic volume or obvious distribution is not a clean support entry.
   - Reject "falling through support" as a buy. A break below support is **not** a lower entry; it is an untriggered or failed setup unless the stock reclaims the support and holds.

2. **Overlay the local stock system**
   - Check local prediction, 20D expected return, risk score, beta, sector condition, news score, settlement/macro caution, special stock status, disposition/attention status, T+1/Dual gate, and model/pipeline health.
   - Treat 20D/model score as an overlay, not a direct short-swing buy signal.
   - If special status, disposition, missing critical data, or risk gate closes the path, label the stock blocked even when the chart looks attractive.

3. **Calculate explicit entry and invalidation zones**
   - Always provide a concrete entry zone, pressure/no-chase level, and invalidation or stop reference.
   - If a stock is above the zone, say "do not chase" and give the pullback zone to watch.
   - If a stock is below the zone, say it failed or needs to reclaim the lower bound before re-entering observation.
   - If no reliable zone can be computed, do not call it an entry candidate.

4. **Apply ChipK as a veto layer, not just a score boost**
   - Use ChipK only when fresh mobile screenshots/manual values are supplied, or when desktop automation is explicitly re-enabled and fresh.
   - Use ChipK main force, foreign/investment trust/dealer flow, large-holder signal, retail/large-holder changes, broker branch concentration, and 1D/5D/20D direction.
   - Veto or downgrade if main force or foreign investors are dumping, broker branches look like day-trade flipping, concentration is weakening, turnover is extreme, or the app flags distribution pressure.
   - Do not rescue a failed support setup because ChipK has one positive label. Price/support failure still fails the setup.

5. **Intraday trigger last**
   - Entry only exists when price enters the zone and holds support. For normal conditions, require at least 15-30 minutes of stable reclaim/hold after the first open-drive noise.
   - If price breaks below the support lower bound, classify as "failed/untriggered"; wait for reclaim before considering again.
   - If price jumps above the zone, classify as "direction right, no chase".
   - If the user asks during the open, prefer the 09:15-09:30, 10:15-10:45, and 13:00-13:15 checkpoints.

## Output Contract

When giving entry candidates, separate these states:

- **Observation list**: setup may become actionable if price returns to the zone.
- **Trigger candidate**: price is inside the zone and support has not failed.
- **Small-entry candidate**: inside zone, support is holding, and ChipK has no veto.
- **Failed/blocked**: support broke, price is too extended, ChipK vetoed, or special/risk gate blocks.

Use plain Chinese descriptions instead of variable names. Always show what is satisfied, what is missing, and why the state is not stronger.

Minimum table columns:

- 股票
- 狀態
- 進場觀察區間
- 壓力/不追價
- 失效價
- 已符合條件
- 缺少或否決條件
- 操作結論

## ChipK Screenshot Requirements

Ask for only the minimum missing screenshots needed to resolve uncertainty. Preferred order:

1. **健檢總覽**: short/long trading attribute, multi/bear factors, technical trend, large-holder signal, foreign/investment trust values.
2. **法人頁**: 1D and recent 6 trading days for foreign, investment trust, dealer, and total institutions.
3. **主力頁**: main-force net buy/sell, buyer-seller house difference, 5D concentration, 20D concentration.
4. **籌碼日報 or 分點頁**: top buy/sell brokers for 1D/5D/20D when day-trade flipping or concentrated branch activity is suspected.
5. **五檔/分時**: only for intraday confirmation, especially near the entry lower bound.

## News Overlay Usage (daily report: ml/reports/industry_news_latest.md)

News NEVER adds candidates — it only vetoes / downgrades / delays names already on the
canonical list. Check order: (1) MOPS ⚠️ alert (董監轉讓/私募/增資) on a list stock →
drop for the day; 📅法說 within 3 days → wait until after the call. (2) Per-stock noise
level: NO news = good (quiet washout is the rere setup); a wall of bullish headlines /
target-price upgrades = heat warning, right tail may be realised → downsize or skip.
(3) Industry section: if the stock's theme is pulling back today, tighten the intraday
trigger. (4) TW-US section: big negative US semis = day-level caution like the gap hint.
Opinion pieces (目標價/外資喊買) are noise; only substance (漲價/接單/財報) counts.

## Common Mistakes To Avoid

- Do not treat an observation list as a buy list.
- Do not call "below support" a better low-entry price.
- Do not chase stocks already above the upper zone because the direction was right.
- Do not mix 20D swing score with same-day short-entry permission.
- Do not allow a single positive ChipK label to override support failure or active distribution.
- Do not recommend special-status/disposition/5-minute matching stocks unless the user explicitly accepts that risk and the gate allows it.

## Canonical candidate generator (USE THIS — do not improvise)

**To build the entry candidate list, run `scripts/generate_entry_candidates.py`** — it encodes the 5-step order deterministically (型態 hard-gate first → model confirm → ChipK veto-only, ChipK cannot rescue a failed 型態) so Claude and Codex produce the **same** list instead of each improvising from different feeds.

```
python scripts/generate_entry_candidates.py --date <YYYY-MM-DD> -o logs/entry_list_<YYYYMMDD>.json
```

Do NOT start from `chipk_model_diagnosis` and back into a list (that puts the veto layer first and lets strong ChipK rescue weak 型態 — a recurring error). The script's 型態 gate runs first; ChipK only vetoes/downgrades. Thresholds live at the top of the script (change → backtest first, per CLAUDE.md).

The generator outputs TWO lanes:
- **Main lane** (溫和動能): 貼MA20 pattern gate → ChipK veto → **型態乾淨度排序取 12**(2026-10-07 PM 核可,BT-main-lane-ranking 4/4 PASS)。模型 pred20 只是顯示欄位,**不過濾、不排序**;OM<0 防呆仍擋。舊規則(模型不反對→clean+pred20 排序)改為 plan 的 `shadow_legacy_main` 影子列,`entry_filter_tracker.py --legacy` 記入 `ml/reports/entry_filter_ledger_legacy_main.csv` 並行 ≥60 日。Kinds: go/small.
- **rere lane**: 蹲點型＝high10/close−1 ≥10%、MA20±5%、當日/20日均量<1.2、外資前5日淨賣且今日買、收在MA60上；發動型＝同深洗盤與外資轉買、量比≥1.5、站回MA20（昨在下方或今漲>2%）且投信同買；淺洗盤型＝8%≤洗盤<10% 其餘同蹲點型。**v2 兩型(2026-10-07 PM 核可,BT-rere-no-foreign-turn 12/12 PASS)**:蹲點型v2 / 淺洗盤型v2 = 同帶、**不要求外資拐點**;現行三型不成立時才掛 v2 標籤(ptype `shakeout_v2`/`shallow_v2`),另計上限 6 檔、帳本 subtype 分開累積,現行 forward cohort 零變動。全部 20日均量≥500張,族群強度再洗盤深度排序。**模型負分與本業虧損不可否決 rere**；固定小倉、60日、失效參考 MA60×0.97。保留賣壓力/砍失敗/基本倉/波段裁量；系統掃描不等於KOL本人薦股。
- Rankings are **snapshots, not permanent truths** (regimes rotate). `scripts/strategy_scoreboard.py` re-scores all validated strategies monthly (auto, day 1-3, in the nightly pipeline) — full-period vs recent-6-month edge; big negative decay ⇒ downgrade the strategy. A recent window far ABOVE full-period = bull-regime inflation, do not extrapolate.
- Tactical (MA60 support bounce) zones were backtested NEGATIVE at 5-10d holds (2026-07-02) — treat tactical zone fields as reference levels only, not entry justification.

## Data Sources (the generator reads these; for manual cross-check only)

The discretionary short-swing entry layer is NOT the Champion auto-gate. Read:

- **Model overlay (PM 2026-07-02 directive)**: exact-date `ml/models/dataA_predictions_<YYYY-MM-DD>.csv` is preferred; exact-date `predictions_<YYYY-MM-DD>.csv` is the fallback. Required close = session before trade_date. Every ticker carries actual source_date; an old file without row-level date evidence is unknown, never repaired from a newer cache. Main lane requires known operating margin from the same inference frame; unknown data stays observation. rere does not require positive model score. Champion book/pins remain separate.
- **ChipK veto**: default is **manual mobile screenshots only**. Desktop `ml/reports/chipk_model_diagnosis_<YYYYMMDD>.csv` is retired/legacy and must not be used automatically unless `CHIPK_DESKTOP_ENABLED=1` and the file is fresh. Fresh manual ChipK evidence can veto or downgrade, but stale desktop files cannot rescue, rank, block, or degrade a candidate.
- **Support pattern / zones**: shortwave/tactical fields in unified output, and `ml/reports/momentum_continuation_backtest_*_latest_setups.csv` (has MA5/20/60, RSI, entry_low/limit/high).
- **Do NOT** treat an empty `ml/models/unified_signals_<date>.csv` as "no candidates exist". That file is the strict Champion **auto-build gate** and is routinely empty in large-cap/cash-waiting regimes. The discretionary layer still has candidates via predictions + support pattern; ChipK only enters as a fresh manual veto unless desktop automation is explicitly re-enabled.

## Repo Tools

- `scripts/intraday_quote.py <tickers...>` — on-demand TWSE MIS live quote (auto TSE/OTC). `--plan <entry_list.json>` compares current price vs zone/stop/no-chase and prints a verdict (未到區間 / 進區間·可觸發 / 跌破失效 / 噴過上緣·不追). No background polling, no disk writes. MIS is delayed ~5-20s.
- `scripts/send_entry_list_email.py -i <entry_list.json>` — email the converged list (UTF-8 safe; follows email-utf8-delivery). `--sample <path>` writes the JSON template; `--dry-run` previews without sending. JSON schema: `{trade_date, subtitle?, intro?, rows:[{priority, stock, status, kind(go|small|watch|veto), zone, stop, no_chase, ret20d, reason}], discipline?}`. Put clean numeric `zone` low–high so the intraday tool parses it correctly.

## Known Data Gaps (do NOT re-investigate)

- **ChipK desktop automation is retired** — do not ask for desktop refresh/reopen, and do not use stale desktop archives as an automated gate.
- **分點 / 短沖 (broker-branch) auto-fetch is BLOCKED** — the CMoney ChipK app API returns header-only, no rows; params never reverse-engineered. Full diagnosis in `docs/RESEARCH_chipk_broker_top_fetch.md`. For 分點/短沖 vetoes (e.g. "Top buyers are all 短沖主力 → caution"), **ask the user for a mobile ChipK screenshot**.
- **No live per-stock feed** except `intraday_quote.py`. Daily K (`日K資料/`) is end-of-day only; today's intraday prices/chips are not in the stored data.
- **Live ChipK screenshots override the model chipk proxy** for the final veto — the proxy can miss fresh distribution (e.g. 映泰 foreign selling 4 days was only visible in the screenshot, not the diagnosis).

## Exit & Position Management (rere layer — discretionary only)

The entry steps above are only half the playbook. rere's real edge is on the **exit/position** side. This is the **discretionary short-swing layer**, NOT the Champion production exit (which is `asymmetric_v2`: 8% HWM trailing + MA5_BREAK — do not touch that). Backtest reality (2020-2026): this is a **low win rate (~43-50%), right-tail** style — most trades are small losers, a few big winners carry it. So manage accordingly:

- **Cut on SETUP FAILURE, not on daily red.** Exit when price breaks the invalidation/support (the "跌破支撐=失敗" line) or the intraday washout does not reclaim. Do NOT cut on day-1/day-2 red — edge only shows at 20-60 day holds; cutting on 1-day noise converts a 20-day-edge into a 1-day-loss.
- **Take profit into resistance (賣在壓力).** Trim/sell as price reaches the prior high / no-chase upper level. Right-tail winners are realised by selling strength, not holding forever.
- **Keep a base position (留基本倉) on confirmed leaders** — names with sustained 主力/投信 accumulation and intact trend. Don't fully exit a winning theme leader; sell the trade portion at resistance, keep the base.
- **Swing round-trip (波段來回).** After selling at resistance, re-enter the same name on a clean pullback to support (re-run the 5-step entry). Same stock, repeated波段.
- **Regime discipline.** In weak/choppy tape (e.g. low recent hit-rate) do fewer trades, wait for pullback + chip confirmation; the style loses in bear/flat years.

## Theme-First Overlay (rere layer)

rere selects **top-down (theme first), then bottom-up (support setup within the theme)** — not a flat market-wide scan.

- When a **sector/theme is leading** (rere's 2026 call: 封裝封測; H2 watch: 成熟製程 / Power IC / 功率元件 / 電力·BBU), **prioritise support setups WITHIN that theme** over unrelated names.
- Confirm the theme with **fundamentals** (月營收 YoY accelerating, Q 毛利率 holding through the cycle) — quality names that keep margins through a downturn (e.g. 聯電/世界 27-30%) over commodity names that swung to losses (e.g. 力積電 10%, foreign dumping).
- Keep base positions in the theme's **quality leaders**; avoid the extended/低毛利/外資倒貨 weak links even if the theme is hot.

## 主力成本 (blocked — manual only)

rere enters near/above the **main-force cost basis** (from ChipK 籌碼日報). That data (分點/主力成本) is BLOCKED for auto-fetch (see Known Data Gaps). Until unblocked, ask the user for the ChipK 主力/分點 screenshot and read the 主力 cost + concentration manually.
