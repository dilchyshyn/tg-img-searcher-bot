import asyncio

import pytest

from src.services import search as search_module
from src.services.search import ImageResult, ImageSearch, SearchError


def images(prefix: str, count: int, title: str = "cat") -> list[ImageResult]:
    return [
        ImageResult(f"https://img.example/{prefix}{i}.jpg", f"https://thumb.example/{prefix}{i}", 100, 100, title)
        for i in range(count)
    ]


class FakeProvider:
    def __init__(self, name: str, pages: list[list[ImageResult]], batch_size: int = 50,
                 max_pages: int = 10, error: Exception | None = None) -> None:
        self.name = name
        self.pages = pages
        self.batch_size = batch_size
        self.max_pages = max_pages
        self.error = error
        self.calls: list[int] = []

    async def fetch_page(self, query: str, page_no: int) -> list[ImageResult]:
        self.calls.append(page_no)
        # Як справжня мережа: віддаємо керування, щоб запущені перевірки встигли виконатись
        await asyncio.sleep(0.01)
        if self.error:
            raise self.error
        return self.pages[page_no] if page_no < len(self.pages) else []


@pytest.fixture
def make_search(make_config):
    def make(*providers: FakeProvider, block_russian: bool = True, bad: tuple[str, ...] = ()):
        s = ImageSearch(make_config(block_russian=block_russian))
        s._providers = list(providers)
        s._by_name = {p.name: p for p in providers}
        s.checked = []

        async def check_original(url: str) -> str | None:
            s.checked.append(url)
            return None if any(marker in url for marker in bad) else url

        s._check_original = check_original
        return s
    return make


def run(coro):
    return asyncio.run(coro)


def urls(page) -> list[str]:
    return [image.url for image in page.images]


def test_batch_smaller_than_page(make_search):
    provider = FakeProvider("fake", [images("a", 5)], batch_size=3)
    s = make_search(provider)

    first = run(s.search("cat"))
    assert urls(first) == urls_of(images("a", 5)[:3])
    assert first.next_offset == "fake:0:3"

    second = run(s.search("cat", first.next_offset))
    assert urls(second) == urls_of(images("a", 5)[3:])
    # Сторінка 1 порожня — видача закінчилась
    assert second.next_offset == ""
    assert provider.calls == [0, 1]   # сторінка 0 узята з кешу


def urls_of(results: list[ImageResult]) -> list[str]:
    return [r.url for r in results]


def test_only_taken_images_are_checked(make_search):
    s = make_search(FakeProvider("fake", [images("a", 10)], batch_size=4))
    run(s.search("cat"))
    assert sorted(s.checked) == sorted(urls_of(images("a", 10)[:4]))


def test_extra_pages_until_min_results(make_search):
    provider = FakeProvider("fake", [images(f"p{n}-", 5) for n in range(10)])
    page = run(make_search(provider).search("cat"))

    # 5 картинок < MIN_RESULTS, тож добираємо MAX_EXTRA_PAGES сторінок
    assert provider.calls == [0, 1, 2]
    assert len(page.images) == 15
    assert page.next_offset == "fake:3:0"


def test_no_extra_pages_when_enough(make_search):
    provider = FakeProvider("fake", [images(f"p{n}-", 25) for n in range(3)])
    page = run(make_search(provider).search("cat"))
    assert provider.calls == [0]
    assert page.next_offset == "fake:1:0"


def test_last_page_by_max_pages(make_search):
    provider = FakeProvider("fake", [images("a", 30), images("b", 30)], max_pages=1)
    page = run(make_search(provider).search("cat"))
    assert len(page.images) == 30
    assert page.next_offset == ""


def test_page_emptied_by_filter_does_not_end_results(make_search):
    provider = FakeProvider("fake", [
        images("ru", 10, title="Кремль"),
        images("ok", 25),
    ])
    page = run(make_search(provider).search("cat"))

    assert urls(page) == urls_of(images("ok", 25))
    assert page.next_offset == "fake:2:0"


def test_filter_can_be_disabled(make_search):
    provider = FakeProvider("fake", [images("ru", 25, title="Кремль")])
    page = run(make_search(provider, block_russian=False).search("cat"))
    assert len(page.images) == 25


def test_unsendable_originals_dropped_and_offset_kept(make_search):
    page0 = images("ok", 3) + images("bad", 2) + images("ok-tail", 3)
    s = make_search(FakeProvider("fake", [page0], batch_size=5), bad=("bad",))

    page = run(s.search("cat"))
    assert urls(page) == urls_of(images("ok", 3))
    # Зсув рахується по сторінці до перевірки, інакше наступна порція зсунулась би
    assert page.next_offset == "fake:0:5"


def test_failed_checks_trigger_extra_page(make_search):
    # Після сторінки 1 кандидатів 22 ≥ MIN_RESULTS, але 5 із них (сторінка 0) уже
    # провалили перевірку, поки вантажилась сторінка 1, — тож добираємо сторінку 2.
    # Провали поточної сторінки на рішення не впливають: її перевірки ще не завершились.
    provider = FakeProvider("fake", [
        images("ok", 5) + images("bad", 5),
        images("ok1-", 12),
        images("ok2-", 25),
    ])
    page = run(make_search(provider, bad=("bad",)).search("cat"))

    assert provider.calls == [0, 1, 2]
    assert len(page.images) == 5 + 12 + 25


def test_slow_checks_dropped(make_search, monkeypatch):
    monkeypatch.setattr(search_module, "CHECK_TIMEOUT", 0.05)
    s = make_search(FakeProvider("fake", [images("a", 2) + images("slow", 1)], max_pages=1))

    async def check_original(url: str) -> str | None:
        if "slow" in url:
            await asyncio.sleep(1)
        return url

    s._check_original = check_original
    page = run(s.search("cat"))
    assert urls(page) == urls_of(images("a", 2))


def test_redirected_original_replaced_by_final_url(make_search):
    original = images("a", 2)
    s = make_search(FakeProvider("fake", [original], max_pages=1))

    async def check_original(url: str) -> str | None:
        return url.replace("img.example", "cdn.example")

    s._check_original = check_original
    page = run(s.search("cat"))

    assert urls(page) == [u.replace("img.example", "cdn.example") for u in urls_of(original)]
    # Решта полів (мініатюра, розміри, заголовок) лишаються від провайдера
    assert [i.thumbnail_url for i in page.images] == [i.thumbnail_url for i in original]


def hosted(host: str, count: int) -> list[ImageResult]:
    return [ImageResult(f"https://{host}/{i}.jpg", f"https://thumb.example/{host}{i}", 100, 100, "cat")
            for i in range(count)]


def test_telegram_blocked_hosts_dropped_before_check(make_search):
    page0 = hosted("images.meme-arsenal.com", 10) + hosted("www.meme-arsenal.com", 5) + images("ok", 5)
    provider = FakeProvider("fake", [page0, images("next", 25)])
    s = make_search(provider)
    page = run(s.search("cat"))

    assert not any("meme-arsenal" in u for u in urls(page))
    assert not any("meme-arsenal" in u for u in s.checked)
    # Лишилось 5 < MIN_RESULTS — добрано наступну сторінку
    assert provider.calls == [0, 1]
    assert len(page.images) == 30


def test_redirect_to_blocked_host_dropped(make_search):
    s = make_search(FakeProvider("fake", [images("a", 3)], max_pages=1))

    async def check_original(url: str) -> str | None:
        return "https://images.meme-arsenal.com/x.jpg" if url.endswith("a0.jpg") else url

    s._check_original = check_original
    page = run(s.search("cat"))
    assert urls(page) == urls_of(images("a", 3)[1:])


def test_similar_host_not_blocked(make_search):
    page = run(make_search(FakeProvider("fake", [hosted("notmeme-arsenal.com", 3)], max_pages=1)).search("cat"))
    assert len(page.images) == 3


def test_check_results_cached_by_url(make_search):
    shared = images("same", 3)
    provider = FakeProvider("fake", [shared], max_pages=1)
    s = make_search(provider)

    run(s.search("cat"))
    run(s.search("kitten"))   # інший запит — сторінка не з кешу, але ті самі картинки
    assert provider.calls == [0, 0]
    assert sorted(s.checked) == sorted(urls_of(shared))


def test_fallback_to_next_provider(make_search):
    broken = FakeProvider("broken", [], error=SearchError("down"))
    empty = FakeProvider("empty", [])
    good = FakeProvider("good", [images("g", 25)])

    page = run(make_search(broken, empty, good).search("cat"))
    assert page.next_offset == "good:1:0"
    assert len(page.images) == 25


def test_all_providers_failed(make_search):
    s = make_search(FakeProvider("broken", [], error=SearchError("down")), FakeProvider("empty", []))
    with pytest.raises(SearchError, match="down"):
        run(s.search("cat"))


def test_next_pages_use_provider_from_offset(make_search):
    first = FakeProvider("first", [images("f", 25)])
    second = FakeProvider("second", [images("s0-", 25), images("s1-", 25)])

    page = run(make_search(first, second).search("cat", "second:1:0"))
    assert first.calls == []
    assert urls(page) == urls_of(images("s1-", 25))


@pytest.mark.parametrize("offset", ["gone:1:0", "garbage", "fake:x:0"])
def test_bad_offset(make_search, offset):
    s = make_search(FakeProvider("fake", [images("a", 5)]))
    with pytest.raises(SearchError):
        run(s.search("cat", offset))


def test_empty_query(make_search):
    provider = FakeProvider("fake", [images("a", 5)])
    page = run(make_search(provider).search("   "))
    assert (page.images, page.next_offset, provider.calls) == ([], "", [])
