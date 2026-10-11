import pytest

from src.services.filters import is_russian


@pytest.mark.parametrize("title", [
    "Котики на дивані",
    "Київ взимку",
    "Львівська кава",
    "Їжак і ґудзик",
    "Харків",
    "Cute cat",
])
def test_ukrainian_and_english_titles_pass(title):
    assert not is_russian(title, "https://example.com/a.jpg")


@pytest.mark.parametrize("title", [
    "Котик ест рыбу",        # ы
    "Это кот",               # э
    "Ёжик",                  # ё
    "Путін",
    "Кремль ночью",
    "Вечірня Москва",        # стоп-слово ловить відмінки
    "Moscow skyline",
    "Russia map",
])
def test_russian_titles_blocked(title):
    assert is_russian(title)


@pytest.mark.parametrize("url", [
    "https://site.ru/a.jpg",
    "https://xn--80a.xn--p1ai/a.jpg",
    "https://vk.com/photo.jpg",
    "https://sun9-1.userapi.com/a.jpg",
    "https://avatars.mds.yandex.net/x.jpg",
    "https://example.com/%D1%8B.jpg",        # «ы» у закодованій адресі
    "https://example.com/moscow-night.jpg",
])
def test_russian_urls_blocked(url):
    assert is_russian("", url)


@pytest.mark.parametrize("url", [
    "https://example.com/a.jpg",
    "https://notvk.com/a.jpg",               # не піддомен vk.com
    "https://bigmoscowcats.com/a.jpg",       # стоп-слово лише на початку слова
])
def test_other_urls_pass(url):
    assert not is_russian("", url)


def test_source_page_is_checked_too():
    assert is_russian("Cat", "https://example.com/a.jpg", "https://dzen.ru/post/1")


def test_empty_urls_ignored():
    assert not is_russian("Cat", "", "")
