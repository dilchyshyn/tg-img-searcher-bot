# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Що це

Telegram inline-бот для пошуку картинок (аналог @pic): користувач пише `@бот запит` у будь-якому чаті й отримує сітку картинок. Python 3.13, aiogram 3, long polling. Коментарі в коді й тексти бота — українською.

## Команди

```bash
# Локально (з кореня репозиторію — імпорти абсолютні від `src.`)
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m src.main

# Docker
docker compose up -d --build
docker compose logs -f
```

Автооновлення: `deploy/install-auto-update.sh` ставить systemd user-таймер, який кожні 2 хв запускає `deploy/auto-update.sh` (`git fetch` → fast-forward → `docker compose up -d --build`). Якщо в робочій копії є незакомічені зміни, оновлення пропускається. Логи: `journalctl --user -u tg-img-bot-update`. Отже, **push у `origin/main` = деплой у прод за ~2 хв**.

`aiohttp` не закріплений у `requirements.txt` — він приходить транзитивно з `aiogram`.

Тестів і лінтера в проєкті немає. Пошук можна перевірити без Telegram, викликавши `ImageSearch.search()` напряму (потрібен лише будь-який `BOT_TOKEN` у оточенні, бо `load_config()` його вимагає):

```bash
BOT_TOKEN=1:x SEARCH_PROVIDERS=bing .venv/bin/python -c "
import asyncio
from src.config import load_config
from src.services.search import ImageSearch
async def t():
    s = ImageSearch(load_config()); p = await s.search('cat'); print(len(p.images), p.next_offset); await s.close()
asyncio.run(t())"
```

Налаштування — у `.env` (у `.gitignore`, на GitHub не потрапляє; шаблону `.env.example` немає свідомо). Основне: `BOT_TOKEN`, `SEARCH_PROVIDERS`, `SAFE_SEARCH`, `CACHE_TIME`; ключі `GOOGLE_API_KEY`/`GOOGLE_CSE_ID`/`SERPAPI_KEY` обов'язкові лише якщо відповідний провайдер є в списку. Inline-режим має бути увімкнений у @BotFather (`/setinline`).

## Архітектура

- `src/main.py` — створює `Config` і `ImageSearch` та передає їх у `Dispatcher(config=..., search=...)`; aiogram підставляє їх у хендлери **за назвою аргументу** (`search: ImageSearch`, `config: Config`).
- `src/handlers/inline.py` — основна логіка. Перша сторінка запиту має дебаунс 0,7 с (Telegram шле запит на кожну літеру; хендлер виходить без відповіді, якщо від користувача прийшов новіший запит). Наступні сторінки — без дебаунсу. Дебаунс працює, бо aiogram обробляє апдейти паралельними задачами, а стан (`_latest_query`) і кеш живуть у пам'яті процесу — розраховано на один інстанс бота.
- `src/handlers/base.py` — лише приватні чати: `/start`, `/help` і кнопки, що перемикають в inline-режим.
- `src/services/search.py` — провайдери й `ImageSearch`.

### Провайдери пошуку

Кожен провайдер — клас з `name`, `batch_size`, `max_pages` і `async fetch_page(query, page_no) -> list[ImageResult]`. Реєструються в словнику `providers` у `ImageSearch.__init__`; допустимі назви також перелічені в `PROVIDERS` у `config.py` — новий провайдер треба додати в обидва місця.

- `duckduckgo` — бібліотека `ddgs` (синхронна, тому `asyncio.to_thread`), ~35 картинок на сторінку.
- `bing` — власний парсер `bing.com/images/async`: кожна картинка — `<a class="iusc" m="{JSON}">` (HTML-екранований JSON з `murl`/`turl`/`t`/`purl`; `purl` → `source_url`, на нього спирається фільтр), розмір — текст `W&#160;&#215;&#160;H` у тому ж блоці. Ламається, якщо Bing змінить розмітку.
- `google` (Custom Search API, 10 на запит, максимум 100) і `serpapi` — є в коді, але за замовчуванням вимкнені.

`SEARCH_PROVIDERS` — впорядкований список (за замовчуванням `duckduckgo,bing`). Перемикання на наступний відбувається **лише для першої сторінки**, якщо провайдер кинув `SearchError` або повернув порожньо.

### Фільтр російського контенту

`src/services/filters.py` → `is_russian(title, *urls)`: літери `ы э ё ъ`, російські домени/TLD і стоп-слова (початки слів, тож ловлять відмінки) у заголовку, адресі картинки й сторінки-джерела (`ImageResult.source_url`). Застосовується в `ImageSearch._get_page` до кешування, вмикається `BLOCK_RUSSIAN` (за замовчуванням увімкнено). Аналізу пікселів немає — лише метадані. На кириличні запити, що однаково пишуться російською («котики», «собака»), фільтр відсіює ~70–95% видачі, тому `_search_with` добирає до `MAX_EXTRA_PAGES` сторінок, поки не набереться `MIN_RESULTS`. Регіон пошуку (`ua-uk`, `mkt=uk-UA`) це майже не покращує — перевірено.

### Пагінація

`next_offset` для Telegram має формат `провайдер:номер_сторінки:зсув` (наприклад `bing:1:0`), тож наступні сторінки завжди беруться з того ж провайдера, що віддав першу. Якщо провайдера з offset уже немає в `SEARCH_PROVIDERS` (наприклад, після зміни `.env` і перезапуску), `_parse_offset` кидає `SearchError` і користувач бачить «Помилка пошуку». Розмір сторінки в провайдерів різний і непостійний, тому кінець видачі визначається порожньою сторінкою або `max_pages`. Сторінки кешуються в пам'яті на 10 хв за ключем `(провайдер, запит, сторінка)`; при 1000 записах кеш очищається повністю. У кеші лежать уже відфільтровані картинки разом із прапорцем `had_results` (чи була сторінка непорожньою **до** фільтра) — саме він, а не довжина списку, вирішує, чи видача закінчилась, тож сторінка, яку фільтр спустошив повністю, пагінацію не обриває.

### Обмеження

- Telegram в inline надійно показує лише JPEG: Google шукає з `fileType=jpg`, інші провайдери відкидають URL з розширеннями з `NON_JPEG_EXTENSIONS`. Відповідь — максимум 50 результатів.
- У сітці Telegram показує мініатюру, а при виборі сам завантажує оригінал (`photo_url`); ~5–25% оригіналів віддають 403, не JPEG (попри `.jpg` в адресі) чи понад 5 МБ — тоді картинка в сітці є, а надіслати її не можна. Тому `_search_with` для кожної взятої в порцію картинки одразу запускає `_is_sendable` (перші байти оригіналу, UA `TelegramBot`) і, не чекаючи, вантажить наступну сторінку; наприкінці `_keep_sendable` чекає всі перевірки один раз (`CHECK_TIMEOUT`) і відкидає непридатні та ті, що не встигли. Отже, кеш сторінок (`_cache`) містить картинки **до** перевірки, а її результати кешуються окремо за URL (`_sendable`). Рішення «добирати ще сторінку» враховує лише перевірки, що вже встигли провалитись. Не повертайте перевірку всередину `_get_page`: тоді затримка сумується посторінково (так і було: ~4 с замість ~2 на кириличних запитах).
- Прямий парсинг Google не працює (перевірено): без JavaScript Google віддає заглушку, headless Chromium одразу отримує капчу. Тому для Google — лише офіційне API або SerpAPI.
