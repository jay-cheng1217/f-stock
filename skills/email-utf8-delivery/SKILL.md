---
name: email-utf8-delivery
description: Prevent mojibake when sending ad-hoc Chinese email from Codex tasks in this repository.
---

# Email UTF-8 Delivery

Use this skill before sending any ad-hoc email that contains Chinese/Japanese/Korean text, web links, PM summaries, or user-facing HTML.

## Hard Rules

- Do not send CJK email bodies from an inline PowerShell here-string piped into `python -`.
- Do not build CJK email HTML only in shell command text.
- Write CJK email content in a UTF-8 `.py`, `.html`, or `.md` file and run that file with Python.
- Use `scripts.send_daily_email.send_email`; it sets plain/html parts to UTF-8 base64.
- For HTML email, include `<meta charset="utf-8">` by using `scripts.send_daily_email._wrap_email_html(...)` or an equivalent full HTML document with an explicit UTF-8 meta tag.
- Before sending, write a UTF-8 preview file under `logs/` and verify it contains the expected Chinese text, not `?` replacement characters or mojibake.

## Recommended Flow

1. Put the email builder in a repository Python script.
2. Build the HTML with `_wrap_email_html(title, subtitle, body_html, generated_at)`.
3. Write `logs/<purpose>_<YYYYMMDD_HHMMSS>.html` with `encoding="utf-8"`.
4. Read the preview back with `encoding="utf-8"` and assert key Chinese phrases are present.
5. Send using `load_email_settings()` and `send_email(settings, subject, html)`.

## Why

PowerShell pipeline encoding can corrupt CJK text before Python receives it. Once the body string is corrupted, SMTP charset headers cannot repair it. Keeping the content in UTF-8 files and using the repo email helper prevents the web-link and investment-summary emails from arriving as question marks or garbled text.
