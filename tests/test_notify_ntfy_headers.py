"""ntfy 標頭編碼:中文標題不可再讓告警送不出去(2026-09-29~10-12 日K終盤校驗告警事故)。"""
import base64
import io

from scripts import notify_ntfy


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _send(monkeypatch, **kwargs):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["headers"] = dict(request.header_items())
        captured["data"] = request.data
        for value in captured["headers"].values():
            value.encode("latin-1")                      # urllib 實際送出前做的事;這裡先驗證不會丟例外
        return _Response(b'{"id": "x"}')

    monkeypatch.setattr(notify_ntfy.urllib.request, "urlopen", fake_urlopen)
    settings = notify_ntfy.NtfySettings(url="https://example.invalid/topic")
    notify_ntfy.send_ntfy(settings=settings, **kwargs)
    return captured


def test_chinese_title_is_rfc2047_encoded_and_round_trips(monkeypatch):
    got = _send(monkeypatch, title="日K終盤校驗告警", message="官方對照修復 272 檔", priority="high")
    title = got["headers"]["Title"]
    assert title.startswith("=?UTF-8?B?") and title.endswith("?=")
    assert base64.b64decode(title[10:-2]).decode("utf-8") == "日K終盤校驗告警"
    assert got["data"].decode("utf-8") == "官方對照修復 272 檔"


def test_ascii_title_and_tags_are_sent_unchanged(monkeypatch):
    got = _send(monkeypatch, title="Phase-1 OK", message="done", tags="white_check_mark")
    assert got["headers"]["Title"] == "Phase-1 OK" and got["headers"]["Tags"] == "white_check_mark"
    assert notify_ntfy._header_value("警告,tag") != "警告,tag"
