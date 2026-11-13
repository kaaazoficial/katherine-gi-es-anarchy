"""Regression tests for the public research source adapters."""
import asyncio

from katherine_research import search_fandom_wikis, search_wikipedia


class FakeResponse:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload or {}
        self.text = text
        self.status_code = status_code
        self.is_success = status_code < 400

    def json(self):
        return self._payload


def test_wikipedia_search_uses_mediawiki_and_returns_citable_urls():
    calls = []

    async def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return FakeResponse({
            "query": {"search": [{"title": "Inteligencia artificial", "snippet": "Campo de estudio."}]}
        })

    results = asyncio.run(search_wikipedia("inteligencia artificial", request))
    assert len(results) == 2
    assert results[0]["domain"] == "es.wikipedia.org"
    assert results[0]["url"].endswith("/wiki/Inteligencia_artificial")
    assert results[0]["source_type"] == "wikipedia"
    assert all("w/api.php" in call[1] for call in calls)


def test_fandom_search_discovers_wiki_then_uses_its_mediawiki_api():
    async def request(method, url, **kwargs):
        if url.endswith("html.duckduckgo.com/html/"):
            return FakeResponse(text=(
                '<a class="result__a" href="https://genshin-impact.fandom.com/wiki/Cyno">Cyno</a>'
            ))
        if url == "https://genshin-impact.fandom.com/api.php":
            return FakeResponse({
                "query": {"search": [{"title": "Cyno", "snippet": "General Mahamatra."}]}
            })
        raise AssertionError(f"Unexpected URL: {url}")

    results = asyncio.run(search_fandom_wikis("Cyno Genshin Impact", request))
    assert len(results) == 1
    assert results[0]["domain"] == "genshin-impact.fandom.com"
    assert results[0]["source_type"] == "fandom_mediawiki"
    assert "General Mahamatra" in results[0]["excerpt"]
