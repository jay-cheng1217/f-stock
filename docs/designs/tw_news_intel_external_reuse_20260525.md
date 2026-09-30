# TW News Intelligence External Reuse Plan

As of: 2026-05-25

## SA Decision

`ZhuLinsen/daily_stock_analysis` 的可用價值不是整套搬，而是把「情報層」抽成台股版：

- P0 已落地：多 provider search fallback、新聞時效過濾、relevance scoring、多維度分類。
- P0 已落地：LLM/Copilot 可呼叫 `get_stock_news_intel`，只做 daily AI summary / 問答情報，不進 production gate。
- P1 已落地第一版：dashboard-shaped report schema，接到現有 war room 新 tab「新聞情報」。
- P1/P2/P3 暫不合併：notification fanout、import parser、image extractor、alert CRUD、React UI、SSE、GitHub Actions 先列待評估，避免擴 blast radius。

## Production Scope

Implemented files:

- `backend/services/tw_news_intel_service.py`
- `backend/routers/news.py`
- `backend/routers/chat.py`
- `scripts/tw_news_intel.py`
- `frontend/index.html`
- `tests/test_tw_news_intel_service.py`

Read-only behavior:

- Does not modify models.
- Does not modify schedulers.
- Does not modify order gates.
- Does not mutate `my_holdings.db`.
- Does not use protected model artifacts as cleanup targets.

## How To Use

Web UI:

1. Open the existing dashboard.
2. Click `新聞情報`.
3. Choose `今日候選`, `我的持股`, or `單檔股票`.
4. Keep `Web 補強` checked if one of these env vars is configured:
   - `TAVILY_API_KEY` / `TAVILY_API_KEYS`
   - `BRAVE_API_KEY` / `BRAVE_API_KEYS`
   - `SERPAPI_API_KEY` / `SERPAPI_API_KEYS`
   - `SEARXNG_BASE_URLS`
5. Press `更新情報`.

API:

- `GET /api/news/intel?scope=top&limit=6&days=7&include_web=true`
- `GET /api/news/intel?scope=holdings&limit=12&days=7&include_web=true`
- `GET /api/news/intel/2330?days=7&limit=8&include_web=true`

CLI:

```powershell
python scripts/tw_news_intel.py --scope top --limit 6 --days 7 --write-report
python scripts/tw_news_intel.py --scope holdings --limit 12 --days 7 --write-report
python scripts/tw_news_intel.py --ticker 2330 --name 台積電 --write-report
```

Artifacts are written to `ml/reports/tw_news_intel_<timestamp>.json` and `.md` only when `--write-report` is used.

## Data Semantics

Dimensions:

- `latest_news`: 最新新聞
- `announcements`: 重大訊息 / 公告 / 法說
- `risk_check`: 處置股、裁罰、虧損、停工、違約等風險事件
- `earnings_revenue`: 營收、獲利、EPS、毛利、展望
- `industry_supply_chain`: 產業供應鏈、訂單、客戶、競爭
- `institutional_flow`: 外資 / 投信 / 籌碼

Risk levels:

- `red`: direct stock match + negative/risk keyword, or MOPS risk item
- `yellow`: sector/macro risk, unknown-date direct risk, or risk-check item without firm date
- `green`: no detected risk keyword

The labels are advisory intelligence. They are not entry/exit automation signals.

## Deferred Reuse Backlog

P1 candidates:

- notification sender abstraction for email / ntfy / Telegram / Slack fanout
- holding import parser for CSV / Excel / pasted text
- screenshot stock extractor for watchlist and holdings import
- alert dry-run architecture, only after REQ-034 stabilizes

P2 candidates:

- manual-analysis task queue and SSE progress for web-triggered analysis
- trading calendar abstraction with XTAI + Taiwan futures settlement overlays
- React UX concepts as reference, not direct merge

P3 candidates:

- network smoke tests and CI ideas
- no GitHub Actions migration for weekly retrain because local DB, model artifacts, and credentials are machine-bound
