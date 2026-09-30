from __future__ import annotations

from scripts.smart_update_auto import _extract_tunnel_url


def test_extract_tunnel_url_ignores_cloudflare_docs_hosts() -> None:
    text = "\n".join(
        [
            "Visit https://developers.trycloudflare.com for docs",
            "API https://api.trycloudflare.com",
            "Your quick Tunnel has been created!",
            "https://bloom-same-thickness-knowing.trycloudflare.com",
        ]
    )

    assert _extract_tunnel_url(text) == "https://bloom-same-thickness-knowing.trycloudflare.com"


def test_extract_tunnel_url_returns_none_when_only_banner_hosts_exist() -> None:
    text = "https://api.trycloudflare.com https://www.trycloudflare.com https://developers.trycloudflare.com"

    assert _extract_tunnel_url(text) is None
