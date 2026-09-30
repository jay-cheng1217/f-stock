"""CMoney ChipK app API probe helpers.

This module reads the desktop app WebView session only in memory and never
prints or stores cookies, JWTs, passwords, or account identifiers.  Persisted
outputs should contain only non-secret market/broker data returned by CMoney.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests
import urllib3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from urllib3.exceptions import InsecureRequestWarning

CHIPK_API_URL = "https://datasv.cmoney.tw/ChipKAdaptor/api/ChipK"
CHIPK_APP_ID = "1812"
CHIPK_BROKER_TOP_FUN_ID = 2
CHIPK_BROKER_DEFAULT_PERIODS = ["1", "5", "10"]
CHIPK_BROKER_TOP_COLUMNS = [
    "排名",
    "分點代號",
    "分點名稱",
    "買張數",
    "賣張數",
    "買金額",
    "賣金額",
    "區間損益",
    "買股數",
    "賣股數",
    "買金額(元)",
    "賣金額(元)",
]
SESSION_COOKIE_NAMES = {
    "cm_at",
    "cm_idt",
    "cm_rt",
    "idp.deviceId",
    "idp.session",
    "__lt__cid",
}


@dataclass(frozen=True)
class ChipKCookieStore:
    local_state_path: Path
    cookies_path: Path


@dataclass(frozen=True)
class ChipKSession:
    cookies: dict[str, str]
    user_guid: str | None
    source: ChipKCookieStore

    def available_sources(self) -> dict[str, bool]:
        return {
            "device": bool(self.cookies.get("idp.deviceId")),
            "ltcid": bool(self.cookies.get("__lt__cid")),
            "user_guid": bool(self.user_guid),
            "cm_at": bool(self.cookies.get("cm_at")),
            "cm_idt": bool(self.cookies.get("cm_idt")),
            "idp_session": bool(self.cookies.get("idp.session")),
            "cm_rt": bool(self.cookies.get("cm_rt")),
        }


@dataclass(frozen=True)
class ChipKDecodedPayload:
    header: list[str]
    rows: list[dict[str, str]]
    raw_line_count: int
    file_names: list[str]


@dataclass(frozen=True)
class ChipKProbeResult:
    fun_id: int
    fun_params: str
    http_status: int | None
    api_state: Any
    query_number_present: bool
    header: list[str]
    row_count: int
    status: str
    message: str
    tls_verify: bool
    auth_label: str
    guid_label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "fun_id": self.fun_id,
            "fun_params": self.fun_params,
            "http_status": self.http_status,
            "api_state": self.api_state,
            "query_number_present": self.query_number_present,
            "header": self.header,
            "row_count": self.row_count,
            "status": self.status,
            "message": self.message,
            "tls_verify": self.tls_verify,
            "auth_label": self.auth_label,
            "guid_label": self.guid_label,
        }


def default_appviewer_webview_root() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return Path()
    return Path(appdata) / "AppViewer" / "EBWebView"


def find_chipk_cookie_store(root: Path | None = None) -> ChipKCookieStore | None:
    """Find the CMoney AppViewer WebView cookie store."""

    webview_root = root or default_appviewer_webview_root()
    local_state = webview_root / "Local State"
    cookies = webview_root / "Default" / "Network" / "Cookies"
    if local_state.exists() and cookies.exists():
        return ChipKCookieStore(local_state_path=local_state, cookies_path=cookies)
    return None


def _dpapi_unprotect(data: bytes) -> bytes:
    """Decrypt a Windows DPAPI blob for the current user."""

    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _load_master_key(local_state_path: Path) -> bytes:
    raw = json.loads(local_state_path.read_text(encoding="utf-8"))
    encrypted_key = base64.b64decode(raw["os_crypt"]["encrypted_key"])
    if encrypted_key.startswith(b"DPAPI"):
        encrypted_key = encrypted_key[5:]
    return _dpapi_unprotect(encrypted_key)


def _decrypt_chromium_value(encrypted_value: bytes, master_key: bytes) -> str:
    if not encrypted_value:
        return ""
    if encrypted_value.startswith((b"v10", b"v11")):
        nonce = encrypted_value[3:15]
        ciphertext = encrypted_value[15:]
        return AESGCM(master_key).decrypt(nonce, ciphertext, None).decode("utf-8", errors="replace")
    return _dpapi_unprotect(encrypted_value).decode("utf-8", errors="replace")


def _copy_cookie_db(cookies_path: Path) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="chipk_cookies_"))
    tmp_path = tmp_dir / "Cookies"
    shutil.copy2(cookies_path, tmp_path)
    return tmp_path


def _jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}


def load_chipk_session(store: ChipKCookieStore | None = None) -> ChipKSession:
    """Load CMoney WebView session fields without exposing secret values."""

    cookie_store = store or find_chipk_cookie_store()
    if cookie_store is None:
        raise FileNotFoundError("CMoney AppViewer WebView cookie store not found")
    master_key = _load_master_key(cookie_store.local_state_path)
    copied = _copy_cookie_db(cookie_store.cookies_path)
    try:
        conn = sqlite3.connect(f"file:{copied}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                """
                SELECT host_key, name, value, encrypted_value
                FROM cookies
                WHERE host_key LIKE '%cmoney.tw%'
                """
            ).fetchall()
        finally:
            conn.close()
    finally:
        shutil.rmtree(copied.parent, ignore_errors=True)

    cookies: dict[str, str] = {}
    for _host, name, value, encrypted_value in rows:
        if name not in SESSION_COOKIE_NAMES:
            continue
        plain = value or _decrypt_chromium_value(bytes(encrypted_value), master_key)
        if plain:
            cookies[name] = plain

    user_guid = None
    for token_name in ("cm_at", "cm_idt"):
        claims = _jwt_payload(cookies.get(token_name, ""))
        candidate = claims.get("user_guid") or claims.get("UserGuid") or claims.get("sub")
        if candidate:
            user_guid = str(candidate)
            break
    return ChipKSession(cookies=cookies, user_guid=user_guid, source=cookie_store)


def resolve_session_value(session: ChipKSession, source: str) -> str | None:
    mapping = {
        "device": session.cookies.get("idp.deviceId"),
        "ltcid": session.cookies.get("__lt__cid"),
        "user_guid": session.user_guid,
        "cm_at": session.cookies.get("cm_at"),
        "cm_idt": session.cookies.get("cm_idt"),
        "idp_session": session.cookies.get("idp.session"),
        "cm_rt": session.cookies.get("cm_rt"),
    }
    return mapping.get(source)


def iter_default_auth_pairs(session: ChipKSession) -> Iterable[tuple[str, str, str, str]]:
    """Yield non-printable auth values with printable labels.

    The labels are safe for reports; the values must never be logged.
    """

    candidates = [
        ("device", "device"),
        ("user_guid", "device"),
        ("device", "user_guid"),
        ("ltcid", "device"),
        ("device", "ltcid"),
        ("user_guid", "cm_at"),
        ("user_guid", "cm_idt"),
        ("device", "idp_session"),
    ]
    seen: set[tuple[str, str]] = set()
    for guid_label, auth_label in candidates:
        if (guid_label, auth_label) in seen:
            continue
        seen.add((guid_label, auth_label))
        guid = resolve_session_value(session, guid_label)
        auth = resolve_session_value(session, auth_label)
        if guid and auth:
            yield guid_label, auth_label, guid, auth


def chipk_broker_top_params(ticker: str, yyyymmdd: str, period: str | int = 5) -> str:
    return f"{str(ticker).zfill(4)},{yyyymmdd},{period},1"


def _decode_zip_csv_payload(data_b64: str, query_number: Any) -> ChipKDecodedPayload:
    data = base64.b64decode(str(data_b64))
    password = f"Get{query_number}CMoney".encode("utf-8")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        texts: list[str] = []
        for name in names:
            with zf.open(name, "r", pwd=password) as handle:
                texts.append(handle.read().decode("cp950", errors="replace"))
    text = "\n".join(texts)
    reader = csv.reader(io.StringIO(text), delimiter="^")
    parsed = [[cell.strip() for cell in row] for row in reader if row]
    if not parsed:
        return ChipKDecodedPayload(header=[], rows=[], raw_line_count=0, file_names=names)
    header = parsed[0]
    rows = []
    for row in parsed[1:]:
        if not any(row):
            continue
        padded = row + [""] * max(0, len(header) - len(row))
        rows.append(dict(zip(header, padded[: len(header)])))
    return ChipKDecodedPayload(header=header, rows=rows, raw_line_count=len(parsed), file_names=names)


def request_chipk_api(
    *,
    fun_id: int,
    fun_params: str,
    guid: str,
    auth_token: str,
    guid_label: str,
    auth_label: str,
    timeout: float = 20.0,
    tls_verify: bool = True,
) -> tuple[ChipKProbeResult, ChipKDecodedPayload | None]:
    payload = {
        "FunId": str(fun_id),
        "FunParams": fun_params,
        "Guid": guid,
        "AuthToken": auth_token,
        "AppId": CHIPK_APP_ID,
    }
    try:
        if not tls_verify:
            urllib3.disable_warnings(InsecureRequestWarning)
        response = requests.post(CHIPK_API_URL, json=payload, timeout=timeout, verify=tls_verify)
    except requests.RequestException as exc:
        return (
            ChipKProbeResult(
                fun_id=fun_id,
                fun_params=fun_params,
                http_status=None,
                api_state=None,
                query_number_present=False,
                header=[],
                row_count=0,
                status="request_error",
                message=f"{type(exc).__name__}: {exc}",
                tls_verify=tls_verify,
                auth_label=auth_label,
                guid_label=guid_label,
            ),
            None,
        )
    try:
        body = response.json()
    except ValueError as exc:
        return (
            ChipKProbeResult(
                fun_id=fun_id,
                fun_params=fun_params,
                http_status=response.status_code,
                api_state=None,
                query_number_present=False,
                header=[],
                row_count=0,
                status="bad_json",
                message=f"{type(exc).__name__}: {exc}",
                tls_verify=tls_verify,
                auth_label=auth_label,
                guid_label=guid_label,
            ),
            None,
        )

    state = body.get("state")
    query_number = body.get("queryNumber")
    data = body.get("data")
    if response.status_code != 200 or state != 1 or not data or query_number is None:
        return (
            ChipKProbeResult(
                fun_id=fun_id,
                fun_params=fun_params,
                http_status=response.status_code,
                api_state=state,
                query_number_present=query_number is not None,
                header=[],
                row_count=0,
                status="api_no_data",
                message=str(body.get("message") or body.get("msg") or ""),
                tls_verify=tls_verify,
                auth_label=auth_label,
                guid_label=guid_label,
            ),
            None,
        )

    try:
        decoded = _decode_zip_csv_payload(data, query_number)
    except Exception as exc:
        return (
            ChipKProbeResult(
                fun_id=fun_id,
                fun_params=fun_params,
                http_status=response.status_code,
                api_state=state,
                query_number_present=True,
                header=[],
                row_count=0,
                status="decode_error",
                message=f"{type(exc).__name__}: {exc}",
                tls_verify=tls_verify,
                auth_label=auth_label,
                guid_label=guid_label,
            ),
            None,
        )
    status = "ok" if decoded.rows else "empty_rows"
    message = "decoded rows" if decoded.rows else "decoded header but no data rows"
    return (
        ChipKProbeResult(
            fun_id=fun_id,
            fun_params=fun_params,
            http_status=response.status_code,
            api_state=state,
            query_number_present=True,
            header=decoded.header,
            row_count=len(decoded.rows),
            status=status,
            message=message,
            tls_verify=tls_verify,
            auth_label=auth_label,
            guid_label=guid_label,
        ),
        decoded,
    )


def normalize_broker_top_rows(rows: list[dict[str, str]], ticker: str, asof_date: str, period: str | int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        out: dict[str, Any] = {
            "ticker": str(ticker).zfill(4),
            "asof_date": asof_date,
            "period": str(period),
            "rank": row.get("排名"),
            "broker_code": row.get("分點代號"),
            "broker_name": row.get("分點名稱"),
            "buy_lots": row.get("買張數"),
            "sell_lots": row.get("賣張數"),
            "buy_amount": row.get("買金額"),
            "sell_amount": row.get("賣金額"),
            "interval_pnl": row.get("區間損益"),
            "buy_shares": row.get("買股數"),
            "sell_shares": row.get("賣股數"),
            "buy_amount_twd": row.get("買金額(元)"),
            "sell_amount_twd": row.get("賣金額(元)"),
        }
        normalized.append(out)
    return normalized
