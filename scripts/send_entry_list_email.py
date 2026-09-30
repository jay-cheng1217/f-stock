# -*- coding: utf-8 -*-
"""每日收斂進場名單寄信工具（通用版，UTF-8 安全，遵循 email-utf8-delivery skill）。

名單資料用 JSON 檔提供，與寄信邏輯分離；本檔只負責排版 + 寄送。

JSON 結構：
{
  "trade_date": "2026/07/01",
  "subtitle": "朋友短打 + 模型 + 支撐 + ChipK veto（活籌碼K覆核收斂）",
  "intro": "（可選）開頭說明，省略則用預設決策流文字",
  "rows": [
    {
      "priority": "1",
      "stock": "興勤 2428",
      "status": "可小倉觸發",            # 自由文字
      "kind": "go",                       # go | small | watch | veto → 決定 emoji/顏色
      "zone": "303.97–319.72（偏好回 305–310）",
      "stop": "294.86",
      "no_chase": ">319.72",
      "ret20d": "+1.24%",
      "reason": "外資連買4天、長期存股…"
    }
  ],
  "discipline": ["盤中觸發才算進場…", "…"]   # 可選，省略則用預設紀律
}

用法：
  python scripts/send_entry_list_email.py -i logs/entry_list_20260701.json
  python scripts/send_entry_list_email.py -i list.json --dry-run        # 只建 preview 不寄
  python scripts/send_entry_list_email.py -i list.json --to me@x.com    # 覆寫收件人
  python scripts/send_entry_list_email.py --sample logs/entry_list.json # 產範本後自行編輯
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from html import escape

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.send_daily_email import (  # noqa: E402
    load_email_settings,
    send_email,
    _wrap_email_html,
)

# kind → (emoji, 顏色)
KIND_STYLE = {
    "go": ("✅", "#1b7e3c"),
    "small": ("🟡", "#b8860b"),
    "watch": ("👀", "#555555"),
    "veto": ("❌", "#b00020"),
}

DEFAULT_INTRO = (
    "決策流：<b>朋友短打支撐優先 → 本地模型確認 → 明確進場區間 → 籌碼K veto</b>。"
    "本表已用實際籌碼K覆核收斂。"
)
DEFAULT_DISCIPLINE = [
    "盤中觸發才算進場：價格進區間後守 15–30 分；跌破下緣＝失效，不是更便宜。",
    "今日已上推的標的別開盤追，優先等回落到區間下半；跳空過上緣＝方向對不追。",
    "本名單為裁量短打進場層，非 Champion 自動建倉訊號；模型 20D 僅作中期支撐確認，小正值＝只能小倉。",
]

SAMPLE = {
    "trade_date": "2026/07/01",
    "subtitle": "朋友短打 + 模型 + 支撐 + ChipK veto（活籌碼K覆核收斂）",
    "rows": [
        {
            "priority": "1",
            "stock": "興勤 2428",
            "status": "可小倉觸發",
            "kind": "go",
            "zone": "303.97–319.72（偏好回 305–310）",
            "stop": "294.86",
            "no_chase": ">319.72",
            "ret20d": "+1.24%",
            "reason": "外資連買4天+657、長期存股、多方28/3，最乾淨；今日已漲5.7%，別追上緣",
        },
        {
            "priority": "—",
            "stock": "映泰 2399",
            "status": "否決（倒貨）",
            "kind": "veto",
            "zone": "—",
            "stop": "—",
            "no_chase": "—",
            "ret20d": "+1.65%",
            "reason": "外資連賣4天-6543張、多空交戰；活籌碼K打臉模型 supportive，移除",
        },
    ],
}


def _build_body_html(data: dict) -> str:
    intro = data.get("intro") or DEFAULT_INTRO
    head = f"<p style='margin:0 0 12px;color:#333'>{intro}</p>"

    th = "style='padding:8px 10px;border-bottom:2px solid #ddd;text-align:left;font-size:13px;color:#666'"
    headers = ["優先", "股票", "狀態", "明天進場觀察區", "停損", "不追價", "20D", "ChipK 收斂理由"]
    head_row = "".join(f"<th {th}>{escape(h)}</th>" for h in headers)

    td = "style='padding:8px 10px;border-bottom:1px solid #eee;font-size:13px;vertical-align:top'"
    body_rows = []
    for r in data.get("rows", []):
        emoji, color = KIND_STYLE.get(str(r.get("kind", "")).lower(), ("", "#333"))
        status = f"{emoji} {r.get('status', '')}".strip()
        cells = [
            r.get("priority", ""),
            f"<b>{escape(str(r.get('stock', '')))}</b>",
            f"<span style='color:{color};font-weight:600'>{escape(status)}</span>",
            escape(str(r.get("zone", ""))),
            escape(str(r.get("stop", ""))),
            escape(str(r.get("no_chase", ""))),
            escape(str(r.get("ret20d", ""))),
            escape(str(r.get("reason", ""))),
        ]
        # priority/股票/狀態 已含 HTML，不再 escape
        cells[0] = escape(str(r.get("priority", "")))
        body_rows.append("<tr>" + "".join(f"<td {td}>{c}</td>" for c in cells) + "</tr>")

    table = (
        "<table style='border-collapse:collapse;width:100%;max-width:880px'>"
        f"<thead><tr>{head_row}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
    )

    bullets = data.get("discipline") or DEFAULT_DISCIPLINE
    disc_items = "".join(f"<li>{escape(b)}</li>" for b in bullets)
    discipline = (
        "<h3 style='margin:18px 0 6px;color:#333'>執行紀律</h3>"
        "<ul style='margin:0;padding-left:20px;color:#444;font-size:13px;line-height:1.7'>"
        f"{disc_items}</ul>"
    )
    return head + table + discipline


def _validate(data: dict) -> None:
    if not data.get("trade_date"):
        raise ValueError("JSON 缺 trade_date")
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("JSON 的 rows 須為非空陣列")
    for i, r in enumerate(rows):
        for key in ("stock", "status"):
            if not r.get(key):
                raise ValueError(f"rows[{i}] 缺必要欄位 {key}")


def main() -> int:
    ap = argparse.ArgumentParser(description="寄出每日收斂進場名單（UTF-8 安全）")
    ap.add_argument("-i", "--input", help="名單 JSON 路徑")
    ap.add_argument("-d", "--date", help="覆寫 trade_date（如 2026/07/01）")
    ap.add_argument("--to", help="覆寫收件人（逗號分隔），預設用 .env.email 的 SMTP_TO")
    ap.add_argument("--dry-run", action="store_true", help="只建 preview 不寄出")
    ap.add_argument("--sample", metavar="PATH", help="寫出範本 JSON 後結束")
    args = ap.parse_args()

    if args.sample:
        with open(args.sample, "w", encoding="utf-8") as f:
            json.dump(SAMPLE, f, ensure_ascii=False, indent=2)
        print("範本已寫出:", args.sample)
        return 0

    if not args.input:
        ap.error("需要 -i/--input（或用 --sample 產範本）")

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    if args.date:
        data["trade_date"] = args.date
    _validate(data)

    trade_date = data["trade_date"]
    subtitle = data.get("subtitle") or "每日收斂進場名單"
    body_html = _build_body_html(data)
    html = _wrap_email_html(
        title=f"進場名單明細 {trade_date}",
        subtitle=subtitle,
        body_html=body_html,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # UTF-8 preview + 驗證中文未亂碼（用實際名單內容做斷言，通用）
    os.makedirs("logs", exist_ok=True)
    preview = os.path.join("logs", f"entry_list_email_{datetime.now():%Y%m%d_%H%M%S}.html")
    with open(preview, "w", encoding="utf-8") as f:
        f.write(html)
    with open(preview, encoding="utf-8") as f:
        content = f.read()
    must_have = ["進場觀察區", "執行紀律", str(data["rows"][0].get("stock", ""))]
    for phrase in must_have:
        assert phrase and phrase in content, f"preview 缺片語：{phrase}"
    print("preview OK:", preview)

    if args.dry_run:
        print("dry-run：未寄出。")
        return 0

    settings = load_email_settings()
    if settings is None:
        print("ERROR: load_email_settings() = None，缺 SMTP 設定")
        return 1
    if args.to:
        settings.to_emails = [x.strip() for x in args.to.split(",") if x.strip()]

    subject = f"進場名單明細 {trade_date}"
    send_email(settings, subject, html)
    print("已寄出 →", ", ".join(settings.to_emails))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
