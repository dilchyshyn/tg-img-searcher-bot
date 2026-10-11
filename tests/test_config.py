import pytest

from src.config import load_config

ENV_VARS = (
    "BOT_TOKEN", "SEARCH_PROVIDERS", "GOOGLE_API_KEY", "GOOGLE_CSE_ID", "SERPAPI_KEY",
    "SAFE_SEARCH", "BLOCK_RUSSIAN", "CACHE_TIME",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    # Локально load_dotenv() міг підтягнути змінні з .env
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BOT_TOKEN", "1:x")


def test_defaults():
    config = load_config()
    assert config.search_providers == ("duckduckgo", "bing")
    assert config.safe_search is True
    assert config.block_russian is True
    assert config.cache_time == 300


def test_providers_normalized_and_deduplicated(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDERS", " Bing, duckduckgo ,bing,")
    assert load_config().search_providers == ("bing", "duckduckgo")


def test_unknown_provider(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDERS", "bing,yandex")
    with pytest.raises(RuntimeError, match="yandex"):
        load_config()


def test_empty_providers(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDERS", " , ")
    with pytest.raises(RuntimeError, match="SEARCH_PROVIDERS"):
        load_config()


def test_bot_token_required(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN")
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        load_config()


@pytest.mark.parametrize("provider, missing", [
    ("google", "GOOGLE_API_KEY"),
    ("serpapi", "SERPAPI_KEY"),
])
def test_provider_keys_required_only_when_used(monkeypatch, provider, missing):
    monkeypatch.setenv("SEARCH_PROVIDERS", provider)
    with pytest.raises(RuntimeError, match=missing):
        load_config()


def test_google_keys(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDERS", "google")
    monkeypatch.setenv("GOOGLE_API_KEY", "key")
    monkeypatch.setenv("GOOGLE_CSE_ID", "cx")
    config = load_config()
    assert (config.google_api_key, config.google_cse_id) == ("key", "cx")


@pytest.mark.parametrize("value, expected", [
    ("true", True), ("1", True), ("ON", True), ("false", False), ("no", False),
])
def test_bool_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("SAFE_SEARCH", value)
    assert load_config().safe_search is expected
