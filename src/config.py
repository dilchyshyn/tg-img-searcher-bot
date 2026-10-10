import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

PROVIDERS = ("google", "serpapi", "duckduckgo")


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Змінна оточення {name} не задана (заповніть .env)")
    return value


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    bot_token: str
    search_provider: str
    google_api_key: str
    google_cse_id: str
    serpapi_key: str
    duckduckgo_fallback: bool
    safe_search: bool
    cache_time: int


def load_config() -> Config:
    provider = os.getenv("SEARCH_PROVIDER", "google").strip().lower()
    if provider not in PROVIDERS:
        raise RuntimeError(f"SEARCH_PROVIDER має бути одним із: {', '.join(PROVIDERS)}")

    return Config(
        bot_token=_require("BOT_TOKEN"),
        search_provider=provider,
        google_api_key=_require("GOOGLE_API_KEY") if provider == "google" else "",
        google_cse_id=_require("GOOGLE_CSE_ID") if provider == "google" else "",
        serpapi_key=_require("SERPAPI_KEY") if provider == "serpapi" else "",
        duckduckgo_fallback=_bool("DUCKDUCKGO_FALLBACK", True),
        safe_search=_bool("SAFE_SEARCH", True),
        cache_time=int(os.getenv("CACHE_TIME", "300")),
    )
