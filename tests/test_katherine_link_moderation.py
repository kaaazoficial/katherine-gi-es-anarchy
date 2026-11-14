import pytest

from katherine_link_analysis import (
    classify_link_policy,
    extract_message_urls,
    inspect_message_links,
)


def test_extract_message_urls_from_chat_text():
    assert extract_message_urls(
        "Visita https://example.com/page y www.example.org/test."
    ) == ["https://example.com/page", "https://www.example.org/test"]


@pytest.mark.parametrize(
    "url",
    [
        "https://app.anarchyapps.org/c/12",
        "https://app.anarchyapps.org/c/80",
        "https://app.anarchyapps.org/c/other",
        "https://anarchyapps.org/c/79",
    ],
)
def test_blocks_other_anarchy_communities(url):
    result = classify_link_policy(url)
    assert result["blocked"] is True
    assert result["category"] == "other_anarchy_community"


def test_allows_only_community_79_exact_host():
    result = classify_link_policy("https://app.anarchyapps.org/c/79")
    assert result["blocked"] is False
    assert result["trusted"] is True


@pytest.mark.parametrize(
    "url",
    [
        "https://duckapps.com.br/",
        "https://www.duckapps.com.br/some-page",
    ],
)
def test_blocks_ducks_and_kyodo(url):
    assert classify_link_policy(url)["blocked"] is True


def test_blocks_katherine_named_page():
    result = classify_link_policy("https://app.anarchyapps.org/c/katherine")
    assert result["blocked"] is True


@pytest.mark.asyncio
async def test_automatic_message_moderation_blocks_disallowed_link_without_fetch():
    calls = []

    async def fetch_page(url):
        calls.append(url)
        return {}

    result = await inspect_message_links(
        "Miren esta comunidad: https://app.anarchyapps.org/c/22",
        fetch_page,
    )
    assert result["blocked"] is True
    assert result["category"] == "other_anarchy_community"
    assert calls == []


@pytest.mark.asyncio
async def test_automatic_message_moderation_checks_allowed_link_and_external_link():
    calls = []

    async def fetch_page(url):
        calls.append(url)
        return {
            "title": "A normal informational page",
            "excerpt": "This is a normal page with useful public information.",
            "url": url,
            "final_url": url,
            "redirect_chain": [url],
        }

    result = await inspect_message_links(
        "Comunidad https://app.anarchyapps.org/c/79 y https://example.org/article",
        fetch_page,
    )
    assert result["blocked"] is False
    assert result["urls_checked"] == 2
    assert calls == ["https://example.org/article"]
