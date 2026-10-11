import asyncio
import html
import json
import logging
import re
import time
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

import aiohttp
from ddgs import DDGS

from src.config import Config
from src.services.filters import is_russian

log = logging.getLogger(__name__)

CACHE_TTL = 600
CACHE_MAX_SIZE = 1000

# Якщо після фільтра на сторінці мало картинок — добираємо наступні сторінки
MIN_RESULTS = 20
MAX_EXTRA_PAGES = 2

# Telegram в inline показує лише JPEG, тому відкидаємо очевидно інші формати
NON_JPEG_EXTENSIONS = (".png", ".gif", ".webp", ".svg", ".bmp", ".avif")

# У сітці Telegram показує мініатюру, а при виборі сам завантажує оригінал.
# Якщо сайт віддає 403, не JPEG чи файл понад 5 МБ — картинку не вдасться
# надіслати, тому перевіряємо оригінали заздалегідь
CHECK_TIMEOUT = 2.5
MAX_PHOTO_SIZE = 5 * 1024 * 1024
CHECK_HEADERS = {"User-Agent": "TelegramBot (like TwitterBot)"}
SENDABLE_CACHE_MAX_SIZE = 20000

# Сайти, які віддають картинки нам, але не серверам Telegram (sendPhoto за URL
# повертає "failed to get HTTP URL content"), тож наша перевірка їх не відсіює.
# Разом із піддоменами, напр. images.meme-arsenal.com
TELEGRAM_BLOCKED_HOSTS = ("meme-arsenal.com",)


@dataclass(frozen=True)
class ImageResult:
    url: str
    thumbnail_url: str
    width: int | None
    height: int | None
    title: str
    # Сторінка, на якій знайдено картинку
    source_url: str = ""


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


def _telegram_can_fetch(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return not any(host == h or host.endswith("." + h) for h in TELEGRAM_BLOCKED_HOSTS)


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
                source_url=image.get("contextLink", ""),
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
                source_url=item.get("link", ""),
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
                source_url=item.get("url", ""),
            ))
        return results


class BingProvider:
    """Власний парсер Bing Картинок: ~35 картинок за сторінку, без ключа і сторонніх бібліотек."""

    name = "bing"
    batch_size = 50
    max_pages = 10
    URL = "https://www.bing.com/images/async"
    PAGE_SIZE = 35
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "uk,en;q=0.8",
    }
    # Кожна картинка — <a class="iusc" ... m="{JSON}">, розмір лежить поруч у "1049 × 1280"
    _META_RE = re.compile(r'\sm="([^"]+)"')
    _SIZE_RE = re.compile(r"(\d+)&#160;&#215;&#160;(\d+)")

    def __init__(self, config: Config, session_factory) -> None:
        self._config = config
        self._session = session_factory

    async def fetch_page(self, query: str, page_no: int) -> list[ImageResult]:
        params = {
            "q": query,
            "first": page_no * self.PAGE_SIZE,
            "count": self.PAGE_SIZE,
            "adlt": "moderate" if self._config.safe_search else "off",
        }
        async with self._session().get(self.URL, params=params, headers=self.HEADERS) as resp:
            if resp.status != 200:
                raise SearchError(f"Bing {resp.status}: {resp.reason}")
            text = await resp.text()

        results = []
        for block in text.split('class="iusc"')[1:]:
            meta_match = self._META_RE.search(block)
            if not meta_match:
                continue
            try:
                meta = json.loads(html.unescape(meta_match.group(1)))
            except ValueError:
                continue
            link = meta.get("murl")
            if not link or not _looks_like_jpeg(link):
                continue
            size = self._SIZE_RE.search(block)
            results.append(ImageResult(
                url=link,
                thumbnail_url=meta.get("turl") or link,
                width=int(size.group(1)) if size else None,
                height=int(size.group(2)) if size else None,
                title=meta.get("t", ""),
                source_url=meta.get("purl", ""),
            ))
        return results


class ImageSearch:
    def __init__(self, config: Config) -> None:
        self._block_russian = config.block_russian
        self._session: aiohttp.ClientSession | None = None
        # Значення: (час, відфільтровані картинки, чи була сторінка непорожньою до фільтра)
        self._cache: dict[tuple[str, str, int], tuple[float, list[ImageResult], bool]] = {}
        # Результати перевірки оригіналів: url -> (час, кінцева адреса або None, якщо не надішлеться)
        self._sendable: dict[str, tuple[float, str | None]] = {}

        providers = {
            "google": lambda: GoogleProvider(config, self._get_session),
            "serpapi": lambda: SerpApiProvider(config, self._get_session),
            "duckduckgo": lambda: DuckDuckGoProvider(config),
            "bing": lambda: BingProvider(config, self._get_session),
        }
        # Перший у списку — основний, решта — запасні по черзі
        self._providers = [providers[name]() for name in config.search_providers]
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

        # Перша сторінка: якщо провайдер упав або нічого не знайшов — пробуємо наступний
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
        images: list[ImageResult] = []
        # Оригінали перевіряються, поки завантажуються наступні сторінки, а чекаємо
        # їх один раз у кінці — інакше затримка перевірки сумується посторінково
        checks: list[asyncio.Task] = []
        extra_pages = 0
        try:
            while True:
                page, had_results = await self._get_page(provider, query, page_no)
                taken = page[skip:skip + provider.batch_size - len(images)]
                images.extend(taken)
                checks.extend(asyncio.ensure_future(self._is_sendable(image.url)) for image in taken)
                skip += len(taken)

                # Порція заповнена, а на сторінці ще лишились картинки
                if skip < len(page):
                    next_offset = f"{provider.name}:{page_no}:{skip}"
                    break

                # Сторінка закінчилась: далі або кінець видачі, або наступна сторінка
                if not had_results or page_no + 1 >= provider.max_pages:
                    next_offset = ""
                    break
                page_no, skip = page_no + 1, 0
                # Перевірки ще тривають, тож не рахуємо лише ті, що вже провалились
                failed = sum(task.done() and not task.result() for task in checks)
                if len(images) - failed >= MIN_RESULTS or extra_pages >= MAX_EXTRA_PAGES:
                    next_offset = f"{provider.name}:{page_no}:0"
                    break
                extra_pages += 1
        except BaseException:
            for task in checks:
                task.cancel()
            raise

        sendable = await self._keep_sendable(images, checks)
        if len(sendable) < len(images):
            log.info("%s: недоступних оригіналів %d з %d для %r", provider.name,
                     len(images) - len(sendable), len(images), query)
        return SearchPage(sendable, next_offset)

    async def _get_page(self, provider, query: str, page_no: int) -> tuple[list[ImageResult], bool]:
        key = (provider.name, query.lower(), page_no)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL:
            return cached[1], cached[2]

        try:
            results = await provider.fetch_page(query, page_no)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise SearchError(f"Помилка запиту до {provider.name}: {e!r}") from e

        had_results = bool(results)
        if self._block_russian:
            kept = [r for r in results if not is_russian(r.title, r.url, r.source_url)]
            if len(kept) < len(results):
                log.info("%s: відфільтровано %d з %d для %r", provider.name,
                         len(results) - len(kept), len(results), query)
            results = kept

        results = [r for r in results if _telegram_can_fetch(r.url)]

        if len(self._cache) >= CACHE_MAX_SIZE:
            self._cache.clear()
        self._cache[key] = (time.monotonic(), results, had_results)
        return results, had_results

    async def _keep_sendable(self, images: list[ImageResult], tasks: list[asyncio.Task]) -> list[ImageResult]:
        """Лишає картинки, оригінал яких Telegram зможе завантажити (tasks — уже запущені
        перевірки _is_sendable). Ті, що не встигли за CHECK_TIMEOUT, теж відкидаються."""
        if not tasks:
            return []
        done, pending = await asyncio.wait(tasks, timeout=CHECK_TIMEOUT)
        for task in pending:
            task.cancel()
        kept = []
        for image, task in zip(images, tasks):
            final_url = task.result() if task in done else None
            if final_url and _telegram_can_fetch(final_url):
                kept.append(image if final_url == image.url else replace(image, url=final_url))
        return kept

    async def _is_sendable(self, url: str) -> str | None:
        """Адреса, з якої Telegram зможе завантажити оригінал, або None."""
        cached = self._sendable.get(url)
        if cached and time.monotonic() - cached[0] < CACHE_TTL:
            return cached[1]
        final_url = await self._check_original(url)
        if len(self._sendable) >= SENDABLE_CACHE_MAX_SIZE:
            self._sendable.clear()
        self._sendable[url] = (time.monotonic(), final_url)
        return final_url

    async def _check_original(self, url: str) -> str | None:
        try:
            async with self._get_session().get(url, headers=CHECK_HEADERS) as resp:
                if resp.status != 200:
                    return None
                if resp.content_length and resp.content_length > MAX_PHOTO_SIZE:
                    return None
                # Сигнатура JPEG; читаємо лише початок файлу
                if await resp.content.readexactly(3) != b"\xff\xd8\xff":
                    return None
                # Telegram може не йти за перенаправленнями (напр. www.meme-arsenal.com
                # віддає 301 на images.meme-arsenal.com), тож віддаємо йому кінцеву адресу
                return str(resp.url)
        except Exception:
            # Будь-яка помилка (мережа, некоректна адреса, обірване з'єднання) — не надішлеться
            return None
