# Finnhub News Sentiment Overlay

As of: 2026-06-07

## Scope

Finnhub `news-sentiment` is treated as an external ADR/global sentiment overlay.
If the token does not have Premium `news-sentiment` access, the fetcher falls
back to free `company-news` headline/summary scoring for the mapped ADR symbol.
It does not replace local MOPS announcements or Taiwan news intelligence.

Supported daily artifacts:

- `ml/data/external_sentiment/finnhub_news_sentiment_YYYYMMDD.json`
- `ml/data/external_sentiment/finnhub_news_sentiment_latest.json`
- `ml/reports/finnhub_news_sentiment_YYYYMMDD.md`
- `ml/reports/finnhub_news_sentiment_latest.md`

## Configuration

Set one of these locally, never in git:

- `FINNHUB_TOKEN`
- `FINNHUB_API_KEY`

Ticker mappings live in:

- `config/finnhub_symbol_map.csv`

Only high-confidence ADR mappings should be enabled. Non-ADR Taiwan stocks must
stay unmapped until a reliable Finnhub symbol basis is verified.

## Daily Update

Nightly news update calls:

```powershell
python scripts/fetch_finnhub_news_sentiment.py
```

If no token exists, the step writes a `skipped_no_token` snapshot and exits zero.
This must not break `TW_Stock_Nightly`.

## Backtest And Monitoring

Run:

```powershell
python scripts/backtest_finnhub_news_overlay.py
```

Promotion requires:

- at least 20 historical Finnhub snapshot days
- at least 50 joined no-lookahead rows
- top-minus-bottom 20D excess return > 0.5pp
- Spearman correlation > 0

By user decision on 2026-06-07, the overlay directly enters
`score_stock_news_sentiment` whenever the latest Finnhub record is `status=ok`.
The blend is local MOPS/Taiwan news score 80% plus Finnhub overlay 20%.
For free tokens this usually means the direct overlay comes from Finnhub
`company-news`, not Premium `news-sentiment`.

Set `FINNHUB_NEWS_SENTIMENT_DIRECT_SCORING=0` to disable direct blending and
fall back to the historical backtest promotion gate. Set
`FINNHUB_NEWS_SENTIMENT_FORCE_SCORING=1` only for explicit manual override.

## Production Boundaries

- Do not use today's Finnhub sentiment to backfill historical trades.
- Do not map Taiwan tickers without ADR or verified Finnhub symbol basis.
- Do not change entry hard gates from this overlay without a separate backtest.
- If Finnhub is missing, unauthorized, non-premium, or unmapped, use the local
  news score unchanged.
- Keep local MOPS announcements as the primary company-event source.
