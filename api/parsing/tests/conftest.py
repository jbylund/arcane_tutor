"""Shared fixtures for parsing tests."""

from functools import partial

import pytest

from api.parsing import parse_scryfall_query
from api.parsing.post_parse import parse_query as parse_with_pipeline
from api.parsing.pyparsing_based import parse_str_to_query as pyparsing_parse_str_to_query

parse_with_pyparsing = partial(parse_with_pipeline, parser_fn=pyparsing_parse_str_to_query)


@pytest.fixture(params=[parse_scryfall_query, parse_with_pyparsing], ids=["hand_rolled", "pyparsing"])
def parse_query(request):
    """Parametrized fixture that runs each test against both parser implementations."""
    return request.param
