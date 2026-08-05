"""Tests for the shared TUI search language and its adapter contracts."""

import pytest

from tui.search_query import SearchSpec, SearchSyntaxError, parse_search


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "super reverb @tone3000",
            SearchSpec(text="super reverb", authors=("tone3000",)),
        ),
        (
            "author:tone3000 tag:clean super reverb",
            SearchSpec(text="super reverb", authors=("tone3000",), tags=("clean",)),
        ),
        (
            "two rock clean @coretonecaptures",
            SearchSpec(text="two rock clean", authors=("coretonecaptures",)),
        ),
        (
            'make:"Two Rock Traditional Clean" @coretonecaptures',
            SearchSpec(makes=("Two Rock Traditional Clean",),
                       authors=("coretonecaptures",)),
        ),
        (
            'tag:"edge of breakup" marshall',
            SearchSpec(text="marshall", tags=("edge of breakup",)),
        ),
        (
            "model:123 model_id:456",
            SearchSpec(model_ids=(123, 456)),
        ),
        (
            "123",
            SearchSpec(model_ids=(123,)),
        ),
    ],
)
def test_documented_examples_parse(query, expected):
    assert parse_search(query) == expected


def test_filters_are_deduplicated_and_same_field_values_are_preserved():
    assert parse_search("@alice @alice #clean #clean author:bob") == SearchSpec(
        authors=("alice", "bob"), tags=("clean",))


def test_unknown_colon_terms_remain_search_text():
    assert parse_search("JCM:800") == SearchSpec(text="JCM:800")


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("@", "@author needs a username"),
        ("#", "#tag needs a tag name"),
        ("author:", "author: needs a value"),
        ("model:abc", "model: needs a numeric model ID"),
        ("model:0", "model: needs a positive model ID"),
        ('tag:"edge of breakup', "unclosed quote"),
    ],
)
def test_invalid_queries_raise_search_syntax_error(query, message):
    with pytest.raises(SearchSyntaxError, match=message):
        parse_search(query)
