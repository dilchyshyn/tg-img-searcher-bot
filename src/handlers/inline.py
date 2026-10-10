import asyncio
import hashlib
import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineQuery, InlineQueryResultPhoto, InlineQueryResultsButton

from src.config import Config
from src.services.search import ImageSearch, SearchError

log = logging.getLogger(__name__)
router = Router(name="inline")

# Telegram надсилає inline-запит на кожну введену літеру. Чекаємо паузу
# в наборі, щоб не витрачати квоту API на "к", "ко", "кот"...
DEBOUNCE_SECONDS = 0.7
_latest_query: dict[int, str] = {}


@router.inline_query()
async def inline_search(query: InlineQuery, search: ImageSearch, config: Config) -> None:
    text = query.query.strip()
    if not text:
        await query.answer(
            [],
            cache_time=1,
            button=InlineQueryResultsButton(text="Введіть запит для пошуку картинок", start_parameter="help"),
        )
        return

    # Дебаунс лише для першої сторінки: підвантаження наступних не чекає
    if not query.offset:
        user_id = query.from_user.id
        _latest_query[user_id] = query.id
        await asyncio.sleep(DEBOUNCE_SECONDS)
        if _latest_query.get(user_id) != query.id:
            return
        _latest_query.pop(user_id, None)

    try:
        page = await search.search(text, query.offset)
    except SearchError as e:
        log.warning("Search failed for %r: %s", text, e)
        await query.answer(
            [],
            cache_time=5,
            button=InlineQueryResultsButton(text="Помилка пошуку, спробуйте пізніше", start_parameter="error"),
        )
        return

    results = []
    seen = set()
    for image in page.images:
        if image.url in seen:
            continue
        seen.add(image.url)
        results.append(InlineQueryResultPhoto(
            id=hashlib.md5(image.url.encode()).hexdigest(),
            photo_url=image.url,
            thumbnail_url=image.thumbnail_url,
            photo_width=image.width,
            photo_height=image.height,
            title=image.title or None,
        ))

    try:
        await query.answer(results, cache_time=config.cache_time, next_offset=page.next_offset)
    except TelegramBadRequest as e:
        # Наприклад, застарілий запит (користувач уже набрав інший текст)
        log.warning("Failed to answer inline query %r: %s", text, e)
