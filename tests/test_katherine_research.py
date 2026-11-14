"""Regression tests for topic-routed public research adapters."""
import asyncio

import pytest

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


def test_source_context_includes_six_numbered_sources():
    sources = [{
        "title": f"Source {index}",
        "domain": f"source{index}.example",
        "url": f"https://source{index}.example",
        "excerpt": "Verified excerpt",
        "source_type": "test",
    } for index in range(1, 9)]
    context = research.build_source_context("test", sources)
    assert "FUENTE [6]" in context
    assert "FUENTE [7]" in context
    assert "FUENTE [8]" in context
    assert "FUENTE [9]" not in context
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


def test_source_context_bounds_source_count_and_large_excerpts():
    sources = [{
        "title": f"Source {index}",
        "domain": f"source{index}.example",
        "url": f"https://source{index}.example/article",
        "excerpt": "Evidence " * 500,
        "source_type": "test",
    } for index in range(1, 9)]
    context = research.build_source_context("test", sources)
    assert len(context) < 18_000
    assert all(f"FUENTE [{index}]" in context for index in range(1, 9))
    assert "FUENTE [9]" not in context
    assert "source8.example" in context


def test_public_page_fetch_uses_dns_pinning_and_stream_byte_limit():
    calls = []

    async def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return FakeResponse(text="<html><title>Public</title><p>" + ("A" * 120) + "</p></html>")

    page = run(research.fetch_public_page("https://example.org/article", request))
    assert page["title"] == "Public"
    assert calls[0][2]["safe_public"] is True
    assert calls[0][2]["max_response_bytes"] == 600_000


def test_provider_failure_is_logged_and_does_not_abort_other_sources(monkeypatch, caplog):
    async def fail(*args, **kwargs):
        raise RuntimeError("offline")

    async def success(*args, **kwargs):
        return [{
            "title": "Public result",
            "url": "https://example.org/article",
            "domain": "example.org",
            "snippet": "Evidence from source",
            "excerpt": "Evidence from source",
            "source_type": "public_web",
        }]

    monkeypatch.setattr(research, "search_wikidata", fail)
    monkeypatch.setattr(research, "search_wikipedia", success)
    monkeypatch.setattr(research, "_search_general_web", success)
    monkeypatch.setattr(research, "search_fandom_wikis", success)
    results = run(research.search_web("generic question about public topic", lambda *a, **k: None, limit=3))
    assert results
    assert "Fuente" in caplog.text



def test_source_coverage_counts_dates_domains_and_source_kinds():
    sources = [
        {"domain": "nasa.gov", "source_type": "nasa_official", "publication_date": "2026-01-01"},
        {"domain": "openalex.org", "source_type": "openalex"},
        {"domain": "community.example", "source_type": "fandom", "year": "2025"},
        {"domain": "nasa.gov", "source_type": "public_web"},
    ]
    coverage = research.summarize_source_coverage(sources)
    assert coverage == {
        "sources": 4,
        "unique_domains": 3,
        "dated_sources": 2,
        "undated_sources": 2,
        "academic_sources": 1,
        "official_sources": 1,
        "community_sources": 1,
        "news_sources": 0,
        "preprint_sources": 0,
        "biomedical_sources": 0,
        "documentation_sources": 0,
    }


def test_source_context_discloses_coverage_limitations():
    context = research.build_source_context(
        "tema de prueba",
        [{"title": "Fuente", "domain": "example.org", "source_type": "public_web"}],
    )
    assert "sources=1" in context
    assert "unique_domains=1" in context
    assert "undated_sources=1" in context
    assert "declara esa limitación" in context



def test_biomedical_and_news_intents_enable_specialized_sources():
    biomedical = research.classify_research_intent(
        "Investiga estudios clínicos sobre diabetes y tratamientos"
    )
    news = research.classify_research_intent(
        "Últimas noticias de energía en Chile esta semana"
    )

    assert biomedical["academic"] is True
    assert biomedical["biomedical"] is True
    assert news["news"] is True
    assert news["biomedical"] is False


def test_arxiv_search_parses_atom_metadata_and_abstract():
    atom = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2401.12345v2</id>
        <title>Efficient Language Models</title>
        <summary>A study of efficient model architectures.</summary>
        <published>2024-01-20T00:00:00Z</published>
        <updated>2024-02-01T00:00:00Z</updated>
        <author><name>Ada Lovelace</name></author>
      </entry>
    </feed>"""

    async def request(method, url, **kwargs):
        assert url == "https://export.arxiv.org/api/query"
        assert kwargs["params"]["max_results"] == 2
        return FakeResponse(text=atom)

    results = run(research.search_arxiv("efficient language models", request))
    assert results[0]["url"] == "https://arxiv.org/abs/2401.12345v2"
    assert results[0]["source_type"] == "arxiv"
    assert results[0]["authors"] == "Ada Lovelace"
    assert "efficient model architectures" in results[0]["excerpt"]


def test_arxiv_search_rejects_untrusted_entry_urls():
    atom = """<feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://evil.example/abs/2401.12345</id>
        <title>Untrusted</title>
        <summary>Not a valid arXiv result.</summary>
      </entry>
    </feed>"""

    async def request(method, url, **kwargs):
        return FakeResponse(text=atom)

    assert run(research.search_arxiv("test topic", request)) == []


def test_europe_pmc_search_returns_bibliographic_metadata_and_abstract():
    async def request(method, url, **kwargs):
        assert url == "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        assert kwargs["params"]["resultType"] == "core"
        return FakeResponse({"resultList": {"result": [{
            "title": "A study of respiratory health",
            "doi": "10.1234/respiratory.2026",
            "authorString": "Ada Lovelace et al.",
            "journalTitle": "Journal of Clinical Research",
            "firstPublicationDate": "2026-04-02",
            "abstractText": "A controlled study of respiratory health outcomes.",
        }]}})

    results = run(research.search_europe_pmc("respiratory health", request))
    assert results[0]["url"] == "https://doi.org/10.1234/respiratory.2026"
    assert results[0]["source_type"] == "europe_pmc"
    assert results[0]["publication_date"] == "2026-04-02"
    assert "controlled study" in results[0]["excerpt"]


def test_gdelt_news_search_keeps_only_public_https_article_urls():
    async def request(method, url, **kwargs):
        assert url == "https://api.gdeltproject.org/api/v2/doc/doc"
        assert kwargs["params"]["timespan"] == "3m"
        return FakeResponse({"articles": [
            {
                "title": "Energy policy update",
                "url": "https://news.example.org/energy",
                "seendate": "20261114T030000Z",
                "language": "Spanish",
                "sourcecountry": "Chile",
            },
            {
                "title": "Private endpoint",
                "url": "https://127.0.0.1/admin",
                "seendate": "20261114T030000Z",
            },
        ]})

    results = run(research.search_gdelt_news("energy policy Chile", request))
    assert len(results) == 1
    assert results[0]["domain"] == "news.example.org"
    assert results[0]["source_type"] == "gdelt_news"
    assert results[0]["indexed_at"] == "2026-11-14 03:00:00 UTC"


def test_software_document_targets_are_narrow_and_topic_specific():
    targets = research._software_document_targets(
        "Deploy FastAPI with Python, GitHub Actions and Render"
    )
    domains = [domain for domain, _ in targets]

    assert "docs.python.org" in domains
    assert "fastapi.tiangolo.com" in domains
    assert "docs.github.com" in domains
    assert len(targets) == 3
    assert research._software_document_targets("Cyno Genshin lore") == []


def test_topic_router_queries_europe_pmc_for_biomedical_requests(monkeypatch):
    calls = []

    async def empty(query, request_func, limit=2):
        return []

    async def record(query, request_func, limit=2):
        calls.append("europe_pmc")
        return [{
            "title": "Clinical source",
            "url": "https://europepmc.org/article/MED/123",
            "domain": "europepmc.org",
            "snippet": "Clinical abstract",
            "excerpt": "Clinical abstract",
            "source_type": "europe_pmc",
        }]

    monkeypatch.setattr(research, "search_wikidata", empty)
    monkeypatch.setattr(research, "search_wikipedia", empty)
    monkeypatch.setattr(research, "_search_general_web", empty)
    monkeypatch.setattr(research, "search_openalex", empty)
    monkeypatch.setattr(research, "search_crossref", empty)
    monkeypatch.setattr(research, "search_arxiv", empty)
    monkeypatch.setattr(research, "search_europe_pmc", record)

    results = run(research.search_web(
        "Investiga estudios clínicos sobre diabetes",
        lambda *a, **k: None,
        limit=8,
    ))
    assert calls == ["europe_pmc"]
    assert results[0]["source_type"] == "europe_pmc"


def test_topic_router_queries_gdelt_for_news_without_other_specialist_sources(monkeypatch):
    calls = []

    async def empty(query, request_func, limit=2):
        return []

    async def record(query, request_func, limit=3):
        calls.append("gdelt")
        return [{
            "title": "Latest report",
            "url": "https://news.example.org/article",
            "domain": "news.example.org",
            "snippet": "News metadata",
            "excerpt": "News metadata",
            "source_type": "gdelt_news",
        }]

    monkeypatch.setattr(research, "search_wikidata", empty)
    monkeypatch.setattr(research, "search_wikipedia", empty)
    monkeypatch.setattr(research, "_search_general_web", empty)
    monkeypatch.setattr(research, "search_gdelt_news", record)
    monkeypatch.setattr(research, "search_fandom_wikis", empty)

    results = run(research.search_web(
        "Últimas noticias sobre energía en Chile",
        lambda *a, **k: None,
        limit=8,
    ))
    assert calls == ["gdelt"]
    assert results[0]["source_type"] == "gdelt_news"


def test_topic_router_queries_official_docs_for_software_topics(monkeypatch):
    domains = []

    async def empty(query, request_func, limit=2):
        return []

    async def scoped(query, domain, request_func, **kwargs):
        domains.append(domain)
        return [{
            "title": "Official documentation",
            "url": f"https://{domain}/guide",
            "domain": domain,
            "snippet": "Official docs result",
            "excerpt": "Official docs result",
            "source_type": kwargs["source_type"],
            "attribution": kwargs["attribution"],
        }]

    monkeypatch.setattr(research, "search_wikidata", empty)
    monkeypatch.setattr(research, "search_wikipedia", empty)
    monkeypatch.setattr(research, "_search_general_web", empty)
    monkeypatch.setattr(research, "search_scoped_site", scoped)

    results = run(research.search_web(
        "Cómo desplegar FastAPI con Python y GitHub Actions",
        lambda *a, **k: None,
        limit=8,
    ))
    assert "docs.python.org" in domains
    assert "fastapi.tiangolo.com" in domains
    assert "docs.github.com" in domains
    assert all(item["source_type"] == "official_software_docs" for item in results)



def test_biomedical_router_recognizes_common_clinical_language():
    for query in (
        "Investiga estudios clínicos sobre diabetes y tratamientos",
        "Alzheimer disease and biomarkers",
        "Clinical research about cancer therapies",
    ):
        intent = research.classify_research_intent(query)
        assert intent["biomedical"] is True
        assert intent["academic"] is True


def test_search_web_bounds_concurrent_source_requests(monkeypatch):
    active = 0
    maximum_active = 0
    calls = []

    async def tracked(*args, **kwargs):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        calls.append((args, kwargs))
        try:
            await asyncio.sleep(0.01)
            return []
        finally:
            active -= 1

    for name in (
        "search_wikidata",
        "search_wikipedia",
        "_search_general_web",
        "search_gdelt_news",
        "search_openalex",
        "search_crossref",
        "search_arxiv",
        "search_europe_pmc",
        "search_scoped_site",
    ):
        monkeypatch.setattr(research, name, tracked)

    run(research.search_web(
        "Investiga noticias y estudios clínicos sobre diabetes con Python y FastAPI",
        lambda *args, **kwargs: None,
        limit=8,
    ))

    assert len(calls) >= 8
    assert maximum_active <= 5
    assert maximum_active > 1


def test_search_web_bounds_parallel_page_enrichment(monkeypatch):
    active = 0
    maximum_active = 0

    async def empty(*args, **kwargs):
        return []

    async def news(query, request_func, limit=4):
        return [
            {
                "title": f"Noticia {index}",
                "url": f"https://news{index}.example/article",
                "domain": f"news{index}.example",
                "snippet": "Cobertura informativa reciente",
                "source_type": "gdelt_news",
            }
            for index in range(6)
        ]

    async def fetch_page(url, request_func):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        try:
            await asyncio.sleep(0.01)
            return {"title": "Noticia", "excerpt": "Texto público"}
        finally:
            active -= 1

    monkeypatch.setattr(research, "search_wikidata", empty)
    monkeypatch.setattr(research, "search_wikipedia", empty)
    monkeypatch.setattr(research, "_search_general_web", empty)
    monkeypatch.setattr(research, "search_gdelt_news", news)
    monkeypatch.setattr(research, "fetch_public_page", fetch_page)

    results = run(research.search_web(
        "Últimas noticias de energía en Chile esta semana",
        lambda *args, **kwargs: None,
        limit=8,
    ))

    assert len(results) == 6
    assert maximum_active == 3



def test_extract_page_content_prefers_article_text_and_removes_boilerplate():
    markup = """
    <html><head><title>Artículo técnico</title></head><body>
      <nav>Menú Inicio Productos</nav>
      <main><article>
        <h1>Hallazgo principal</h1>
        <p>Este artículo explica un hallazgo importante con contexto, metodología,
        resultados y conclusiones. Su contenido permite comprobar que la extracción
        conserva el cuerpo útil de la página para un análisis posterior.</p>
        <p>También incluye una segunda sección con información adicional y detalles
        concretos que deben mantenerse después de eliminar los elementos secundarios.</p>
      </article></main>
      <script>window.stealCredentials = true;</script>
      <footer>Publicidad y enlaces secundarios</footer>
    </body></html>
    """
    title, text = research.extract_page_content(markup)
    assert title == "Artículo técnico"
    assert "Hallazgo principal" in text
    assert "metodología" in text
    assert "Menú Inicio" not in text
    assert "Publicidad" not in text
    assert "stealCredentials" not in text


def test_hardened_xml_parser_rejects_entity_expansion_payload():
    malicious_xml = b"""<!DOCTYPE feed [<!ENTITY secret "local-secret">]>
    <feed><title>&secret;</title></feed>"""
    with pytest.raises(research.ET.EntitiesForbidden):
        research.ET.fromstring(malicious_xml)
