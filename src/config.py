import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

PROVIDERS = ("google", "serpapi", "duckduckgo", "bing")


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
    search_providers: tuple[str, ...]
    google_api_key: str
    google_cse_id: str
    serpapi_key: str
    safe_search: bool
    block_russian: bool
    cache_time: int


def load_config() -> Config:
    raw = os.getenv("SEARCH_PROVIDERS", "duckduckgo,bing")
    providers = tuple(dict.fromkeys(p.strip().lower() for p in raw.split(",") if p.strip()))
    if not providers:
        raise RuntimeError("SEARCH_PROVIDERS не може бути порожнім")
    unknown = [p for p in providers if p not in PROVIDERS]
    if unknown:
        raise RuntimeError(
            f"Невідомі провайдери в SEARCH_PROVIDERS: {', '.join(unknown)}. "
            f"Доступні: {', '.join(PROVIDERS)}"
        )

    return Config(
        bot_token=_require("BOT_TOKEN"),
        search_providers=providers,
        google_api_key=_require("GOOGLE_API_KEY") if "google" in providers else "",
        google_cse_id=_require("GOOGLE_CSE_ID") if "google" in providers else "",
        serpapi_key=_require("SERPAPI_KEY") if "serpapi" in providers else "",
        safe_search=_bool("SAFE_SEARCH", True),
        block_russian=_bool("BLOCK_RUSSIAN", True),
        cache_time=int(os.getenv("CACHE_TIME", "300")),
    )
