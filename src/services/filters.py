import re
from urllib.parse import unquote, urlsplit

# Літери, яких немає в українській (є також у білоруській/болгарській)
RUSSIAN_LETTERS_RE = re.compile(r"[ыэёъ]")

RUSSIAN_TLDS = (".ru", ".su", ".xn--p1ai", ".рф")
RUSSIAN_DOMAINS = (
    "vk.com", "vk.me", "userapi.com", "vkuser.net", "ok.ru", "ya.ru", "dzen.ru",
    "mail.ru", "rambler.ru", "livejournal.com", "pikabu.ru", "tass.com", "rt.com",
    "sputniknews.com", "kinopoisk.ru",
)
# Частина назви хоста: yandex.net, yandex.com, yastatic.net тощо
RUSSIAN_HOST_PARTS = ("yandex", "yastatic")

# Початки слів: збігаються з усіма відмінками («москв» -> Москва, Москві, Москвою)
STOP_WORDS = (
    # Кирилиця
    "путин", "путін", "кремл", "москв", "москов", "петербург", "питер", "росси", "росі",
    "рашист", "триколор", "георгиевск", "георгіївськ", "кадыров", "кадиров", "шойгу",
    "лавров", "медведев", "пригожин", "вагнер", "своих не бросаем",
    # Латиниця й транслітерація
    "putin", "kremlin", "moscow", "moskva", "petersburg", "russia", "rossiya", "rossii",
    "kadyrov", "shoigu", "lavrov", "medvedev", "prigozhin", "wagner group",
)
STOP_WORDS_RE = re.compile(
    r"(?<![a-zа-яіїєґ])(" + "|".join(re.escape(w) for w in STOP_WORDS) + ")"
)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_russian_host(host: str) -> bool:
    if not host:
        return False
    if host.endswith(RUSSIAN_TLDS):
        return True
    if any(host == d or host.endswith("." + d) for d in RUSSIAN_DOMAINS):
        return True
    return any(part in host for part in RUSSIAN_HOST_PARTS)


def is_russian(title: str, *urls: str) -> bool:
    """True, якщо за заголовком чи адресами картинка схожа на російський контент."""
    if any(_is_russian_host(_host(url)) for url in urls if url):
        return True

    text = " ".join([title, *(unquote(url) for url in urls if url)]).lower()
    return bool(RUSSIAN_LETTERS_RE.search(text) or STOP_WORDS_RE.search(text))
