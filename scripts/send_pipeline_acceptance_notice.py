"""Dated UTF-8 acceptance notice to the existing configured recipient only."""
from datetime import datetime
import argparse
import hashlib
import json
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from scripts.send_daily_email import _wrap_email_html, load_email_settings, send_email


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    receipt_path = BASE / "logs/pipeline_acceptance_notice_20260906_receipt.json"
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text(encoding="utf-8"))
        if previous.get("status") == "sent":
            print(json.dumps({"status": "already_sent"}))
            return
    subject = "[Stock ML] 流程驗收完成｜資金集中風險與Web載入待處理"
    body = """
<div class="section">
<h2>現在已完成實際流程驗收</h2>
<p>使用9/4真實收盤資料，已走過官方校驗、新建DuckDB、完整特徵與1,080檔既有模型推論，
產生9/7的18檔觀察名單，並驗證帳本重跑去重、卡片與郵件預覽。rere六筆仍待進場，沒有提前成交。</p>
<p>修正健康API連線、排程失敗回傳與呈現問題；模型風險警示不再誤稱全部正式入口關閉，
動能研究不再混稱正式放行，退役ChipK不再宣稱已更新。本輪沒有重訓、換模型或更動rere條件。</p>
<h2>尚不能稱全系統健康完善</h2>
<ul>
<li>資金配置缺乏通用單股／產業資金上限：新空帳本可能對單一2603預留99.85%；
既有Champion紙上帳本則為2357名目100%，並非券商實際持倉或即時市值占比。正式配置規則本輪未改。</li>
<li>現行Web停止程序遭Windows拒絕，舊服務仍在線。修正版API已在隔離環境驗證，
需管理員權限重啟既有Web後核對健康API；不需重訓或重啟隧道。</li>
<li>下一交易日的新資料發布、盘中觸發、未來成交與策略長期獲利，不能由週末重播證明。</li>
</ul>
<p>完整驗收與後續排序：F:/stock/docs/REPORT_pipeline_acceptance_20260906.md。</p>
<p><a href="https://claude.ai/code/artifact/8705d1fe-77ce-411e-a162-895c7c8fb22b">進場儀表板固定網址</a>
仍待Claude Artifact同步；本次未重發Artifact，也未另建網址。同步後請按Ctrl+F5。</p>
</div>
"""
    html = _wrap_email_html(subject, "整合流程有實證，資金與部署限制仍保留", body, datetime.now().isoformat(timespec="seconds"))
    preview = BASE / "logs/pipeline_acceptance_notice_20260906.html"
    preview.write_text(html, encoding="utf-8")
    checked = preview.read_text(encoding="utf-8")
    for phrase in ("完整特徵與1,080檔", "正式配置規則本輪未改", "需管理員權限", "仍待Claude Artifact同步", "Ctrl+F5"):
        assert phrase in checked
    assert "\ufffd" not in checked and 'charset="utf-8"' in checked.lower()
    result = {"time": datetime.now().isoformat(), "status": "preview_verified", "preview": str(preview),
              "sha256": hashlib.sha256(preview.read_bytes()).hexdigest()}
    if args.send:
        settings = load_email_settings()
        if settings is None:
            result.update(status="skipped", reason="missing_smtp_settings")
        else:
            try:
                send_email(settings, subject, checked)
                result.update(status="sent", recipient_count=len(settings.to_emails))
            except Exception as exc:
                result.update(status="failed", error_type=type(exc).__name__)
    receipt_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
