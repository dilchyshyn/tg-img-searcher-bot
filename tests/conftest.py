import pytest

from src.config import Config


@pytest.fixture
def make_config():
    def make(providers=("bing",), block_russian=True) -> Config:
        return Config(
            bot_token="1:x",
            search_providers=providers,
            google_api_key="",
            google_cse_id="",
            serpapi_key="",
            safe_search=True,
            block_russian=block_russian,
            cache_time=300,
        )
    return make
