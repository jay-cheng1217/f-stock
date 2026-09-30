from scripts import smart_update


def test_admin_api_headers_include_loaded_admin_token(monkeypatch):
    monkeypatch.setattr(smart_update, "_load_admin_token", lambda: "secret-token")

    assert smart_update._admin_api_headers() == {
        "Content-Type": "application/json",
        "X-Admin-Token": "secret-token",
    }


def test_load_admin_token_reads_dotenv(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("OTHER=value\nADMIN_TOKEN='from-file'\n", encoding="utf-8")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(smart_update, "BASE_DIR", str(tmp_path))

    assert smart_update._load_admin_token() == "from-file"
