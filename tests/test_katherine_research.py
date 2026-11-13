"""Regression tests for topic-routed public research adapters."""
import asyncio

import katherine_research as research


class FakeResponse:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload or {}
        self.text = text
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._payload


def run(coro):
    return asyncio.run(coro)


def test_intent_routing_keeps_genshin_separate_from_nasa():
    intent = research.classify_research_intent("Cyno Genshin Impact build y lore")
    assert intent["genshin"] is True
    assert intent["fandom"] is True
    assert intent["nasa"] is False
    assert intent["music"] is False


def test_generic_anarchy_question_does_not_enable_game_sources():
    intent = research.classify_research_intent("Que es Anarchy Apps y como funciona")
    assert not intent["genshin"]
    assert not intent["fandom"]
    assert not intent["nasa"]
    assert not intent["music"]
    assert not intent["academic"]


def test_academic_intent_enables_openalex_and_crossref_only_for_research():
    intent = research.classify_research_intent(
        "Busca articulos cientificos y papers sobre inteligencia artificial"
    )
    assert intent["academic"] is True
    assert intent["genshin"] is False
    assert intent["music"] is False


def test_wikidata_search_returns_entity_page_and_description():
    async def request(method, url, **kwargs):
        assert url == "https://www.wikidata.org/w/api.php"
        return FakeResponse({"search": [{
            "id": "Q42", "label": "Douglas Adams", "description": "Escritor británico"
        }]})

    results = run(research.search_wikidata("Douglas Adams", request))
    assert results[0]["url"] == "https://www.wikidata.org/wiki/Q42"
    assert results[0]["source_type"] == "wikidata"
    assert "Escritor británico" in results[0]["excerpt"]


def test_internet_archive_uses_public_advanced_search():
    async def request(method, url, **kwargs):
        assert url == "https://archive.org/advancedsearch.php"
        return FakeResponse({"response": {"docs": [{
            "identifier": "example-book", "title": "Example Book",
            "creator": "Author", "year": "1920", "mediatype": "texts"
        }]}})

    results = run(research.search_internet_archive("Example Book", request))
    assert results[0]["url"] == "https://archive.org/details/example-book"
    assert results[0]["source_type"] == "internet_archive"


def test_openalex_returns_work_metadata_and_doi():
    async def request(method, url, **kwargs):
        assert url == "https://api.openalex.org/works"
        return FakeResponse({"results": [{
            "title": "A research paper",
            "doi": "https://doi.org/10.1234/example",
            "publication_year": 2024,
            "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
            "abstract_inverted_index": {"Research": [0], "works": [1]},
            "type": "article",
        }]})

    results = run(research.search_openalex("research paper", request))
    assert results[0]["url"] == "https://doi.org/10.1234/example"
    assert "Ada Lovelace" in results[0]["excerpt"]
    assert results[0]["source_type"] == "openalex"


def test_crossref_returns_doi_metadata():
    async def request(method, url, **kwargs):
        assert url == "https://api.crossref.org/works"
        return FakeResponse({"message": {"items": [{
            "DOI": "10.5555/test-doi",
            "title": ["A Crossref article"],
            "author": [{"given": "Grace", "family": "Hopper"}],
            "published": {"date-parts": [[2023, 5, 1]]},
            "container-title": ["Journal of Tests"],
        }]}})

    results = run(research.search_crossref("A Crossref article", request))
    assert results[0]["url"] == "https://doi.org/10.5555/test-doi"
    assert "Grace Hopper" in results[0]["excerpt"]
    assert results[0]["source_type"] == "crossref"


def test_nasa_search_uses_nasa_public_media_api():
    async def request(method, url, **kwargs):
        assert url == "https://images-api.nasa.gov/search"
        return FakeResponse({"collection": {"items": [{
            "data": [{
                "nasa_id": "PIA123",
                "title": "Mars image",
                "description": "A NASA image of Mars",
                "date_created": "2024-01-02T00:00:00Z",
            }]
        }]}})

    results = run(research.search_nasa("Mars", request))
    assert results[0]["url"] == "https://images.nasa.gov/details/PIA123"
    assert results[0]["source_type"] == "nasa_library"


def test_musicbrainz_search_uses_identified_artist_metadata():
    async def request(method, url, **kwargs):
        assert url == "https://musicbrainz.org/ws/2/artist/"
        return FakeResponse({"artists": [{
            "id": "00000000-0000-0000-0000-000000000001",
            "name": "Example Artist",
            "country": "GB",
            "type": "Person",
        }]})

    results = run(research.search_musicbrainz("artista Example Artist", request))
    assert results[0]["domain"] == "musicbrainz.org"
    assert results[0]["source_type"] == "musicbrainz"
    assert "Example Artist" in results[0]["title"]


def test_jamendo_catalog_requires_client_id_and_returns_license(monkeypatch):
    monkeypatch.setenv("JAMENDO_CLIENT_ID", "test-client-id")

    async def request(method, url, **kwargs):
        assert url == "https://api.jamendo.com/v3.0/tracks/"
        assert kwargs["params"]["client_id"] == "test-client-id"
        return FakeResponse({"results": [{
            "id": "12345",
            "name": "Independent Track",
            "artist_name": "Artist",
            "album_name": "Album",
            "license_ccurl": "https://creativecommons.org/licenses/by/3.0/",
        }]})

    results = run(research.search_jamendo_catalog("ambient music", request))
    assert results[0]["url"] == "https://www.jamendo.com/track/12345"
    assert results[0]["source_type"] == "jamendo_catalog"
    assert "creativecommons" in results[0]["excerpt"]


def test_fandom_search_discovers_wiki_then_uses_mediawiki_api():
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

    results = run(research.search_fandom_wikis("Cyno Genshin Impact", request))
    assert len(results) == 1
    assert results[0]["domain"] == "genshin-impact.fandom.com"
    assert results[0]["source_type"] == "fandom_mediawiki"


def test_topic_router_calls_kqm_hoyolab_and_fandom_for_genshin_only(monkeypatch):
    calls = []

    async def empty(query, request_func, limit=2):
        return []

    async def scoped(query, domain, request_func, **kwargs):
        calls.append(domain)
        return [{
            "title": domain, "url": f"https://{domain}/article",
            "domain": domain, "snippet": "Community source",
            "excerpt": "Community source", "source_type": kwargs["source_type"],
        }]

    monkeypatch.setattr(research, "search_wikidata", empty)
    monkeypatch.setattr(research, "search_wikipedia", empty)
    monkeypatch.setattr(research, "_search_general_web", empty)
    monkeypatch.setattr(research, "search_fandom_wikis", empty)
    monkeypatch.setattr(research, "search_scoped_site", scoped)

    results = run(research.search_web("Cyno Genshin Impact build", lambda *a, **k: None, limit=8))
    assert "keqingmains.com" in calls
    assert "hoyolab.com" in calls
    assert all(item["source_type"] in {"keqingmains", "hoyolab"} for item in results)


def test_topic_router_does_not_call_game_sources_for_general_question(monkeypatch):
    calls = []

    async def empty(query, request_func, limit=2):
        return []

    async def scoped(query, domain, request_func, **kwargs):
        calls.append(domain)
        return []

    monkeypatch.setattr(research, "search_wikidata", empty)
    monkeypatch.setattr(research, "search_wikipedia", empty)
    monkeypatch.setattr(research, "_search_general_web", empty)
    monkeypatch.setattr(research, "search_fandom_wikis", empty)
    monkeypatch.setattr(research, "search_scoped_site", scoped)

    run(research.search_web("Que es Anarchy Apps", lambda *a, **k: None, limit=8))
    assert calls == []


def test_source_context_includes_eight_numbered_sources():
    sources = [{
        "title": f"Source {index}",
        "domain": f"source{index}.example",
        "url": f"https://source{index}.example",
        "excerpt": "Verified excerpt",
        "source_type": "test",
    } for index in range(1, 9)]
    context = research.build_source_context("test", sources)
    assert "FUENTE [8]" in context
    assert "source8.example" in context


def test_parse_research_modes_and_preserve_subject():
    assert research.parse_research_mode("comparar: energía nuclear y solar") == (
        "comparar", "energía nuclear y solar"
    )
    assert research.parse_research_mode("verificar: esta afirmación es cierta") == (
        "verificar", "esta afirmación es cierta"
    )
    assert research.parse_research_mode("cronología de la exploración espacial") == (
        "cronologia", "la exploración espacial"
    )
    assert research.parse_research_mode("informe: inteligencia artificial") == (
        "informe", "inteligencia artificial"
    )
    assert research.parse_research_mode("cambios en Chile") == (
        "general", "cambios en Chile"
    )


def test_research_mode_parser_does_not_accept_empty_mode_subject():
    assert research.parse_research_mode("comparar:") == ("general", "comparar:")
