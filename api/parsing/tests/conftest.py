"""Shared fixtures for parsing tests."""

import pytest

from api.parsing import parse_scryfall_query
from api.parsing.post_parse import parse_pyparsing_query


@pytest.fixture(params=[parse_scryfall_query, parse_pyparsing_query], ids=["hand_rolled", "pyparsing"])
def parse_query(request):
    """Parametrized fixture that runs each test against both parser implementations."""
    return request.param
