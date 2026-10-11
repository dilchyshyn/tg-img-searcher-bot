import asyncio
from pathlib import Path

from src.services.search import BingProvider

# Справжня відповідь bing.com/images/async?q=cat, обрізана до 8 картинок (2 з них — PNG)
FIXTURE = Path(__file__).parent / "fixtures" / "bing_cat.html"


class FakeResponse:
    status = 200
    reason = "OK"

    def __init__(self, text: str) -> None:
        self._text = text

    async def text(self) -> str:
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, text: str) -> None:
        self.text = text
        self.params = None

    def get(self, url, params=None, headers=None):
        self.params = params
        return FakeResponse(self.text)


def parse(make_config, text: str, page_no: int = 0):
    session = FakeSession(text)
    results = asyncio.run(BingProvider(make_config(), lambda: session).fetch_page("cat", page_no))
    return results, session


def test_parses_saved_response(make_config):
    results, _ = parse(make_config, FIXTURE.read_text(encoding="utf-8"))

    assert len(results) == 6
    assert not any(r.url.lower().split("?")[0].endswith(".png") for r in results)

    first = results[0]
    assert first.url.startswith("https://spotpetinsurance.ca/_next/image?url=")
    # &amp; у turl має бути розекранований
    assert first.thumbnail_url == "https://ts4.mm.bing.net/th?id=OIP.OytIzSUybdg3xIeUWcoRlQHaFj&pid=15.1"
    assert (first.width, first.height) == (1200, 900)
    assert first.title == "8 House Cat Breeds You Should Consider"
    assert first.source_url == "https://spotpetinsurance.ca/blog/breed-tips/8-house-cat-breeds"

    assert results[2].url == (
        "https://animalko.com/wp-content/uploads/2025/03/"
        "14-Domestic-Cat-Breeds-Known-For-Their-Incredible-Strength-1.jpg"
    )
    assert (results[2].width, results[2].height) == (1920, 1080)


def block(meta: str, size: str = "") -> str:
    return f'<a class="iusc" m="{meta}"></a><span>{size}</span>'


def test_skips_broken_blocks(make_config):
    text = "".join([
        block("{&quot;murl&quot;:&quot;https://a.com/1.jpg&quot;}"),          # без розміру й turl
        block("{broken json"),
        block("{&quot;turl&quot;:&quot;https://t.com/2&quot;}"),              # без murl
        block("{&quot;murl&quot;:&quot;https://a.com/3.webp&quot;}"),
    ])
    results, _ = parse(make_config, text)

    assert len(results) == 1
    r = results[0]
    assert (r.url, r.thumbnail_url, r.width, r.height, r.title, r.source_url) == (
        "https://a.com/1.jpg", "https://a.com/1.jpg", None, None, "", "",
    )


def test_page_offset(make_config):
    _, session = parse(make_config, "", page_no=2)
    assert session.params["first"] == 2 * BingProvider.PAGE_SIZE
