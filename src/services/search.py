import asyncio
import logging
import time
from dataclasses import dataclass

import aiohttp
from ddgs import DDGS

from src.config import Config

log = logging.getLogger(__name__)

CACHE_TTL = 600
CACHE_MAX_SIZE = 1000

# Telegram в inline показує лише JPEG, тому відкидаємо очевидно інші формати
NON_JPEG_EXTENSIONS = (".png", ".gif", ".webp", ".svg", ".bmp", ".avif")


@dataclass(frozen=True)
class ImageResult:
    url: str
    thumbnail_url: str
    width: int | None
    height: int | None
    title: str


@dataclass(frozen=True)
class SearchPage:
    images: list[ImageResult]
    # Передається в Telegram як next_offset; порожній рядок — результатів більше немає
    next_offset: str


class SearchError(Exception):
    pass


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _looks_like_jpeg(url: str) -> bool:
    path = url.split("?", 1)[0].lower()
    return not path.endswith(NON_JPEG_EXTENSIONS)


class GoogleProvider:
    """Google Custom Search API: 10 картинок за запит, максимум 100 на пошук."""

    name = "google"
    batch_size = 10
    max_pages = 10
    URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, config: Config, session_factory) -> None:
        self._config = config
        self._session = session_factory

    async def fetch_page(self, query: str, page_no: int) -> list[ImageResult]:
        params = {
            "key": self._config.google_api_key,
            "cx": self._config.google_cse_id,
            "q": query,
            "searchType": "image",
            "num": 10,
            "start": page_no * 10 + 1,
            "safe": "active" if self._config.safe_search else "off",
            "fileType": "jpg",
        }
        async with self._session().get(self.URL, params=params) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                message = data.get("error", {}).get("message", resp.reason)
                raise SearchError(f"Google API {resp.status}: {message}")

        results = []
        for item in data.get("items", []):
            link = item.get("link")
            if not link:
                continue
            image = item.get("image", {})
            results.append(ImageResult(
                url=link,
                thumbnail_url=image.get("thumbnailLink") or link,
                width=_to_int(image.get("width")),
                height=_to_int(image.get("height")),
                title=item.get("title", ""),
            ))
        return results


class SerpApiProvider:
    """SerpAPI (видача Google Картинок): ~100 картинок за запит."""

    name = "serpapi"
    batch_size = 50
    max_pages = 5
    URL = "https://serpapi.com/search.json"

    def __init__(self, config: Config, session_factory) -> None:
        self._config = config
        self._session = session_factory

    async def fetch_page(self, query: str, page_no: int) -> list[ImageResult]:
        params = {
            "engine": "google_images",
            "q": query,
            "ijn": page_no,
            "safe": "active" if self._config.safe_search else "off",
            "api_key": self._config.serpapi_key,
        }
        async with self._session().get(self.URL, params=params) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise SearchError(f"SerpAPI {resp.status}: {data.get('error', resp.reason)}")

        results = []
        for item in data.get("images_results", []):
            link = item.get("original")
            if not link or not _looks_like_jpeg(link):
                continue
            results.append(ImageResult(
                url=link,
                thumbnail_url=item.get("thumbnail") or link,
                width=_to_int(item.get("original_width")),
                height=_to_int(item.get("original_height")),
                title=item.get("title", ""),
            ))
        return results


class DuckDuckGoProvider:
    """DuckDuckGo Images через бібліотеку ddgs: безкоштовно, без ключа, ~35 картинок за сторінку."""

    name = "duckduckgo"
    batch_size = 50
    max_pages = 10

    def __init__(self, config: Config) -> None:
        self._config = config

    async def fetch_page(self, query: str, page_no: int) -> list[ImageResult]:
        try:
            # ddgs синхронна, тому виконуємо її в окремому потоці
            items = await asyncio.to_thread(
                DDGS().images,
                query,
                region="wt-wt",
                safesearch="moderate" if self._config.safe_search else "off",
                max_results=100,
                page=page_no + 1,
            )
        except Exception as e:
            raise SearchError(f"DuckDuckGo: {e!r}") from e

        results = []
        for item in items:
            link = item.get("image")
            if not link or not _looks_like_jpeg(link):
                continue
            results.append(ImageResult(
                url=link,
                thumbnail_url=item.get("thumbnail") or link,
                width=_to_int(item.get("width")),
                height=_to_int(item.get("height")),
                title=item.get("title", ""),
            ))
        return results


class ImageSearch:
    def __init__(self, config: Config) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[tuple[str, str, int], tuple[float, list[ImageResult]]] = {}

        providers = {
            "google": lambda: GoogleProvider(config, self._get_session),
            "serpapi": lambda: SerpApiProvider(config, self._get_session),
            "duckduckgo": lambda: DuckDuckGoProvider(config),
        }
        # Основний провайдер першим, DuckDuckGo — запасним
        self._providers = [providers[config.search_provider]()]
        if config.duckduckgo_fallback and config.search_provider != "duckduckgo":
            self._providers.append(providers["duckduckgo"]())
        self._by_name = {p.name: p for p in self._providers}

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self._session

    async def search(self, query: str, offset: str = "") -> SearchPage:
        """offset — порожній для першої сторінки, далі те, що повернулось у next_offset."""
        query = query.strip()
        if not query:
            return SearchPage([], "")

        if offset:
            provider, page_no, skip = self._parse_offset(offset)
            return await self._search_with(provider, query, page_no, skip)

        # Перша сторінка: якщо основний провайдер упав або нічого не знайшов — пробуємо запасний
        last_error: SearchError | None = None
        for provider in self._providers:
            try:
                result = await self._search_with(provider, query, 0, 0)
            except SearchError as e:
                log.warning("%s failed for %r: %s", provider.name, query, e)
                last_error = e
                continue
            if result.images:
                return result
        if last_error:
            raise last_error
        return SearchPage([], "")

    def _parse_offset(self, offset: str):
        try:
            name, page_no, skip = offset.split(":")
            return self._by_name[name], int(page_no), int(skip)
        except (ValueError, KeyError):
            raise SearchError(f"Некоректний offset: {offset!r}") from None

    async def _search_with(self, provider, query: str, page_no: int, skip: int) -> SearchPage:
        page = await self._get_page(provider, query, page_no)
        images = page[skip:skip + provider.batch_size]

        if skip + provider.batch_size < len(page):
            next_offset = f"{provider.name}:{page_no}:{skip + provider.batch_size}"
        elif page and page_no + 1 < provider.max_pages:
            next_offset = f"{provider.name}:{page_no + 1}:0"
        else:
            next_offset = ""
        return SearchPage(images, next_offset)

    async def _get_page(self, provider, query: str, page_no: int) -> list[ImageResult]:
        key = (provider.name, query.lower(), page_no)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL:
            return cached[1]

        try:
            results = await provider.fetch_page(query, page_no)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise SearchError(f"Помилка запиту до {provider.name}: {e!r}") from e

        if len(self._cache) >= CACHE_MAX_SIZE:
            self._cache.clear()
        self._cache[key] = (time.monotonic(), results)
        return results
