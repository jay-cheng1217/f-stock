"""Send the REQ-034-v3 daily my_holdings risk-cut report.

This batch script is read-only for `my_holdings.db`. It writes local artifacts
before any email attempt and only sends sanitized fields: ticker, signal bucket,
trigger rule names, and short status notes.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from scripts.req034_my_holdings_dry_run import DEFAULT_DB_PATH, build_dry_run  # noqa: E402
from scripts.send_daily_email import load_email_settings, send_email  # noqa: E402


LOG_DIR = BASE_DIR / "logs"
DEFAULT_TO_EMAIL = "jaycheng821217@gmail.com"
BACKFILL_LOG = LOG_DIR / "req034_real_coverage_backfill.log"
SYNTHETIC_NOTE = (
    "fallback 路徑為 synthetic-tested，首次真實觸發後將回填驗證；"
    "本信不包含成本、股數或未實現損益。"
)


def _today_str() -> str:
    return date.today().isoformat()


def _bucket_title(bucket: str) -> str:
    return {
        "red": "Red / 降部位或立即 review",
        "yellow": "Yellow / 觀察或減碼 review",
        "green": "Green / 保留",
    }.get(bucket, bucket)


def _reason_text(item: dict[str, Any]) -> str:
    reasons = item.get("reason_codes") or []
    return ", ".join(str(reason) for reason in reasons) if reasons else "-"


def _brief(item: dict[str, Any]) -> str:
    action = str(item.get("action_group") or "")
    status = str(item.get("review_status") or "")
    daily_k = str(item.get("daily_k_status") or "")
    return f"{action}; {status}; daily_k={daily_k}"


def render_text(summary: dict[str, Any], include_synthetic_note: bool = True) -> str:
    counts = summary["counts"]
    lines = [
        f"REQ-034-v3 my_holdings risk-cut report - {summary['as_of_date']}",
        "",
    ]
    if include_synthetic_note:
        lines.extend([SYNTHETIC_NOTE, ""])
    lines.extend(
        [
            f"Counts: red={counts['red']} yellow={counts['yellow']} green={counts['green']} total={counts['total']}",
            "",
        ]
    )
    for bucket in ("red", "yellow", "green"):
        items = [item for item in summary["holdings"] if item["bucket"] == bucket]
        lines.append(f"{_bucket_title(bucket)} (n={len(items)})")
        if not items:
            lines.append("- none")
        for item in items:
            lines.append(
                f"- {item['ticker']}: signal={item['signal']} rules={_reason_text(item)} brief={_brief(item)}"
            )
        lines.append("")
    lines.extend(
        [
            "Footer:",
            "- Read-only report; no DB mutation and no auto-order.",
            "- Private cost/share fields are omitted.",
            "- 14 guardrail thresholds unchanged.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _html_list(items: list[dict[str, Any]]) -> str:
    if not items:
        return "<p>none</p>"
    rows = []
    for item in items:
        rows.append(
            "<li>"
            f"<strong>{html.escape(str(item['ticker']))}</strong>: "
            f"signal={html.escape(str(item.get('signal') or ''))}; "
            f"rules={html.escape(_reason_text(item))}; "
            f"{html.escape(_brief(item))}"
            "</li>"
        )
    return "<ul>" + "".join(rows) + "</ul>"


def render_html(summary: dict[str, Any], include_synthetic_note: bool = True) -> str:
    counts = summary["counts"]
    sections = []
    for bucket, color in (("red", "#dc2626"), ("yellow", "#d97706"), ("green", "#16a34a")):
        items = [item for item in summary["holdings"] if item["bucket"] == bucket]
        sections.append(
            f"<section style='border-left:4px solid {color};padding:8px 12px;margin:12px 0;'>"
            f"<h3>{html.escape(_bucket_title(bucket))} (n={len(items)})</h3>"
            f"{_html_list(items)}</section>"
        )
    note = f"<p><strong>{html.escape(SYNTHETIC_NOTE)}</strong></p>" if include_synthetic_note else ""
    return f"""
<html><body style="font-family:'Noto Sans TC','Microsoft JhengHei',Arial,sans-serif;line-height:1.55;">
<h2>REQ-034-v3 my_holdings risk-cut report - {html.escape(summary['as_of_date'])}</h2>
{note}
<p>Counts: red={counts['red']} yellow={counts['yellow']} green={counts['green']} total={counts['total']}</p>
{''.join(sections)}
<hr>
<ul>
  <li>Read-only report; no DB mutation and no auto-order.</li>
  <li>Private cost/share fields are omitted.</li>
  <li>14 guardrail thresholds unchanged.</li>
</ul>
</body></html>
""".strip()


def write_report_artifacts(
    summary: dict[str, Any],
    log_dir: Path = LOG_DIR,
    include_synthetic_note: bool = True,
) -> dict[str, str]:
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = str(summary["as_of_date"]).replace("-", "")
    text_path = log_dir / f"my_holdings_risk_cut_{stamp}.log"
    json_path = log_dir / f"my_holdings_risk_cut_{stamp}.json"
    html_path = log_dir / f"my_holdings_risk_cut_{stamp}.html"
    text_body = render_text(summary, include_synthetic_note=include_synthetic_note)
    html_body = render_html(summary, include_synthetic_note=include_synthetic_note)
    text_path.write_text(text_body, encoding="utf-8")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(html_body, encoding="utf-8")
    return {"text": str(text_path), "json": str(json_path), "html": str(html_path)}


def append_real_coverage_backfill(summary: dict[str, Any], log_path: Path = BACKFILL_LOG) -> int:
    real_rows = []
    for item in summary["holdings"]:
        is_yellow = item["bucket"] == "yellow"
        is_etf = item["asset_type"] == "etf"
        is_fallback = item["asset_type"] == "stock" and not item["prediction_available"]
        if not (is_yellow or is_etf or is_fallback):
            continue
        cases = []
        if is_yellow:
            cases.append("real_yellow")
        if is_etf:
            cases.append("real_etf")
        if is_fallback:
            cases.append("real_prediction_fallback")
        real_rows.append(
            {
                "as_of_date": summary["as_of_date"],
                "ticker": item["ticker"],
                "cases": cases,
                "bucket": item["bucket"],
                "signal": item["signal"],
                "reason_codes": item.get("reason_codes") or [],
            }
        )
    if not real_rows:
        return 0
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        for row in real_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(real_rows)


def _send_report(summary: dict[str, Any], html_body: str, to_email: str) -> bool:
    settings = load_email_settings()
    if settings is None:
        print("[my-holdings-risk-cut] email skipped: SMTP settings missing")
        return False
    settings.to_emails = [to_email]
    subject = f"[My Holdings] Daily risk-cut report {summary['as_of_date']}"
    send_email(settings, subject, html_body)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send REQ-034-v3 my_holdings risk-cut email.")
    parser.add_argument("--as-of-date", default=_today_str())
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--to", default=DEFAULT_TO_EMAIL)
    parser.add_argument("--dry-run", action="store_true", help="Write artifacts but do not send email.")
    parser.add_argument(
        "--no-synthetic-note",
        action="store_true",
        help="Suppress the synthetic-tested fallback note after PM explicitly retires it.",
    )
    args = parser.parse_args(argv)

    summary = build_dry_run(args.as_of_date, Path(args.db_path))
    include_note = not args.no_synthetic_note
    paths = write_report_artifacts(summary, include_synthetic_note=include_note)
    backfill_count = append_real_coverage_backfill(summary)
    print(
        "[my-holdings-risk-cut] "
        f"counts={summary['counts']} artifacts={paths} real_backfill_rows={backfill_count}"
    )

    if args.dry_run:
        print("[my-holdings-risk-cut] dry-run: email not sent")
        return 0

    html_body = Path(paths["html"]).read_text(encoding="utf-8")
    sent = _send_report(summary, html_body, args.to)
    print(f"[my-holdings-risk-cut] email_sent={sent} to={args.to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

