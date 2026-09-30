# ChipK Admin Bridge

This runbook is for the case where CMoney ChipK/AppViewer is running elevated,
but the Codex automation shell is still medium integrity.  In that state,
Windows UIPI blocks mouse and keyboard messages from Codex to ChipK.

## When To Use

Use this bridge only for local GUI operation of the ChipK desktop app:

- ticker search
- tab or interval switching
- visible table scrolling
- screenshots for OCR or visual verification

Do not use it for credential capture, cookie export, or bypassing CMoney
authorization.  The bridge never reads CMoney login data.

## Start

Run this from Windows as administrator:

```powershell
Start-Process PowerShell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File "F:\stock\scripts\start_chipk_admin_bridge.ps1"'
```

Approve the UAC prompt and keep the opened PowerShell window alive.

The bridge writes a one-session token to:

```text
tmp/chipk_admin_bridge/token.txt
```

`tmp/` is git-ignored.  Do not copy the token into commits, docs, or logs.

## Verify

From the normal Codex shell:

```powershell
python scripts/chipk_admin_client.py health
```

Expected:

```json
{
  "ok": true,
  "admin": true
}
```

## Example Actions

Click:

```powershell
python scripts/chipk_admin_client.py click 255 80
```

Type:

```powershell
python scripts/chipk_admin_client.py write 2330
```

Hotkey:

```powershell
python scripts/chipk_admin_client.py hotkey ctrl a
```

Screenshot:

```powershell
python scripts/chipk_admin_client.py screenshot --name chipk_current.png
```

Screenshots are written under:

```text
tmp/chipk_admin_bridge/
```

## Security Boundaries

- Binds to `127.0.0.1` only.
- Requires `X-ChipK-Bridge-Token` for every request.
- Token is generated per start and stored only under `tmp/`.
- Request body is capped.
- Text input is capped to 128 characters.
- The bridge is still powerful while running because it sends real desktop
  input.  Close its PowerShell window when finished.
