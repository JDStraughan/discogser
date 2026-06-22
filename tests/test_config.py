import pytest

from discogser.config import DEFAULT_FOLDER, DEFAULT_MODEL, Config, ConfigError

_VARS = ["ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "DISCOGS_TOKEN",
         "DISCOGS_USERNAME", "DISCOGS_FOLDER", "USER_AGENT"]


def _clear(monkeypatch):
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)


def test_loads_dotenv_from_working_directory(tmp_path, monkeypatch):
    # A pip-installed discogser must read .env from where the user runs it,
    # not from inside site-packages.
    _clear(monkeypatch)
    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=sk-test\n"
        "DISCOGS_TOKEN=tok\n"
        "DISCOGS_USERNAME=me\n"
        "USER_AGENT=discogser/1.0 +mailto:me@example.com\n"
    )
    monkeypatch.chdir(tmp_path)
    cfg = Config.load()
    assert cfg.anthropic_api_key == "sk-test"
    assert cfg.discogs_token == "tok"
    assert cfg.discogs_username == "me"
    assert cfg.anthropic_model == DEFAULT_MODEL   # default applied when unset
    assert cfg.discogs_folder == DEFAULT_FOLDER


def test_real_environment_wins_over_dotenv(tmp_path, monkeypatch):
    _clear(monkeypatch)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-file\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    monkeypatch.setenv("DISCOGS_TOKEN", "tok")
    monkeypatch.setenv("DISCOGS_USERNAME", "me")
    monkeypatch.setenv("USER_AGENT", "ua/1.0")
    monkeypatch.chdir(tmp_path)
    assert Config.load().anthropic_api_key == "from-env"   # env not clobbered


def test_missing_required_raises(tmp_path, monkeypatch):
    _clear(monkeypatch)
    with pytest.raises(ConfigError):
        Config.load(env_file=tmp_path / "none.env")  # no file, nothing in env
