"""Send a UTF-8 safe web dashboard link email.

This script exists to avoid CJK mojibake from ad-hoc PowerShell pipelines.
Keep Chinese email copy in this UTF-8 source file and use the shared email
helper, which sends plain/html MIME parts as UTF-8 base64.
"""

from __future__ import annotations

import argparse
import html
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from scripts.send_daily_email import _wrap_email_html, load_email_settings, send_email


DEFAULT_LOCAL_URL = "http://127.0.0.1:8001"
# 固定網址 Artifact(每日雲端排程重發同一網址)——主要介面。
DASHBOARD_ARTIFACT_URL = "https://claude.ai/code/artifact/8705d1fe-77ce-411e-a162-895c7c8fb22b"
KOL_ARTIFACT_URL = "https://claude.ai/code/artifact/5acea1ce-9a67-44fe-a55e-4d8fc8b285f6"
REQUIRED_PHRASES = [
    "進場策略介面",
    "進場儀表板",
    "KOL Watch",
    "認明上方兩個固定網址",
]


def build_web_link_html(remote_url: str, local_url: str, generated_at: str) -> str:
    safe_remote = html.escape(remote_url)
    safe_local = html.escape(local_url)
    safe_dash = html.escape(DASHBOARD_ARTIFACT_URL)
    safe_kol = html.escape(KOL_ARTIFACT_URL)
    body = f"""
    <div class="section">
      <h2>進場策略介面已更新</h2>
      <p><b>主要介面:進場儀表板(固定網址)</b>——雙策略名單、大盤資訊、個股日K/MACD/四大面向、
         模型/主力/ChipK/短波 tag、進場帳本,每日自動更新、網址不變。</p>
      <p style="margin:18px 0;">
        <a href="{safe_dash}" style="display:inline-block;background:#16a34a;color:#ffffff;padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:800;">
          開啟進場儀表板(固定網址)
        </a>
      </p>
      <p><b>美股 KOL Watch(固定網址)</b>——Serenity 觀點追蹤、四時間視圖、個股觀點歷史。</p>
      <p style="margin:18px 0;">
        <a href="{safe_kol}" style="display:inline-block;background:#7c3aed;color:#ffffff;padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:800;">
          開啟 KOL Watch(固定網址)
        </a>
      </p>
      <p style="color:#64748b;font-size:13px;margin-top:20px;">
        ⚠️ 認明上方兩個固定網址(綠/紫)。以下遠端 Web 為每日換網址的即時工作台,
        讀本機資料庫,若本機更新中可能顯示舊值;日常看盤請用上方固定網址,勿以此為準。
      </p>
      <table role="presentation" style="width:100%;border-collapse:collapse;">
        <tr>
          <td style="padding:8px 0;color:#64748b;width:110px;">進場儀表板(固定)</td>
          <td style="padding:8px 0;"><a href="{safe_dash}">{safe_dash}</a></td>
        </tr>
        <tr>
          <td style="padding:8px 0;color:#64748b;">KOL Watch(固定)</td>
          <td style="padding:8px 0;"><a href="{safe_kol}">{safe_kol}</a></td>
        </tr>
        <tr>
          <td style="padding:8px 0;color:#94a3b8;font-size:12px;">遠端Web(即時,次要)</td>
          <td style="padding:8px 0;font-size:12px;"><a href="{safe_remote}" style="color:#94a3b8;">{safe_remote}</a></td>
        </tr>
      </table>
    </div>
    """
    return _wrap_email_html(
        title="Web 進場策略介面連結",
        subtitle="UTF-8 safe resend / 進場過濾器式版面",
        body_html=body,
        generated_at=generated_at,
    )


def write_preview(html_body: str, preview_path: Path) -> None:
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(html_body, encoding="utf-8")
    preview_text = preview_path.read_text(encoding="utf-8")
    missing = [phrase for phrase in REQUIRED_PHRASES if phrase not in preview_text]
    if missing:
        raise RuntimeError(f"UTF-8 preview verification failed, missing phrases: {missing}")
    if "\ufffd" in preview_text:
        raise RuntimeError("UTF-8 preview verification failed: replacement character found")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a UTF-8 safe web dashboard link email.")
    parser.add_argument("--remote-url", required=True)
    parser.add_argument("--local-url", default=DEFAULT_LOCAL_URL)
    parser.add_argument("--preview-path", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_body = build_web_link_html(args.remote_url, args.local_url, generated_at)
    preview_path = (
        Path(args.preview_path)
        if args.preview_path
        else BASE_DIR / "logs" / f"web_link_email_preview_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    )
    write_preview(html_body, preview_path)

    if args.dry_run:
        print(f"[web-link-email] dry-run preview={preview_path}")
        return

    settings = load_email_settings()
    if settings is None:
        raise RuntimeError("SMTP settings missing; cannot send web link email")
    subject = "[Stock ML] Web 進場策略介面連結已更新"
    send_email(settings, subject, html_body)
    print(f"[web-link-email] sent to={','.join(settings.to_emails)} preview={preview_path}")


if __name__ == "__main__":
    main()
