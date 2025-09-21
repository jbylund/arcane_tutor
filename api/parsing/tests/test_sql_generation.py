"""Tests for SQL generation from parsed query ASTs."""

import pytest

from api import parsing
from api.parsing import (
    AndNode,
    AttributeNode,
    BinaryOperatorNode,
    NotNode,
    NumericValueNode,
    OrNode,
    QueryNode,
    StringValueNode,
)
from api.parsing.parsing_f import generate_sql_query
from api.parsing.scryfall_nodes import get_legality_comparison_object


@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql", "expected_parameters"),
    argvalues=[
        ("cmc=3", "(card.cmc = %(p_int_Mw)s)", {"p_int_Mw": 3}),
        ("power=3", "(card.creature_power = %(p_int_Mw)s)", {"p_int_Mw": 3}),
        ("cmc=3 power=3", "((card.cmc = %(p_int_Mw)s) AND (card.creature_power = %(p_int_Mw)s))", {"p_int_Mw": 3}),
        ("power=toughness", "(card.creature_power = card.creature_toughness)", {}),
        ("power:toughness", "(card.creature_power = card.creature_toughness)", {}),
        ("power>toughness", "(card.creature_power > card.creature_toughness)", {}),
        ("power<toughness", "(card.creature_power < card.creature_toughness)", {}),
        ("power>cmc+1", r"(card.creature_power > (card.cmc + %(p_int_MQ)s))", {"p_int_MQ": 1}),
        ("power-cmc>1", r"((card.creature_power - card.cmc) > %(p_int_MQ)s)", {"p_int_MQ": 1}),
        ("1<power-cmc", r"(%(p_int_MQ)s < (card.creature_power - card.cmc))", {"p_int_MQ": 1}),
        (
            "cmc+cmc+2<power+toughness",
            r"(((card.cmc + card.cmc) + %(p_int_Mg)s) < (card.creature_power + card.creature_toughness))",
            {"p_int_Mg": 2},
        ),
        # Test field-specific : operator behavior
        ("name:lightning", r"(card.card_name ILIKE %(p_str_JWxpZ2h0bmluZyU)s)", {"p_str_JWxpZ2h0bmluZyU": r"%lightning%"}),
        (
            "name:'lightning bolt'",
            r"(card.card_name ILIKE %(p_str_JWxpZ2h0bmluZyVib2x0JQ)s)",
            {"p_str_JWxpZ2h0bmluZyVib2x0JQ": r"%lightning%bolt%"},
        ),
        ("cmc:3", "(card.cmc = %(p_int_Mw)s)", {"p_int_Mw": 3}),  # Numeric field uses exact equality
        ("power:5", "(card.creature_power = %(p_int_NQ)s)", {"p_int_NQ": 5}),  # Numeric field uses exact equality
        # color
        ("color:g", "(card.card_colors @> %(p_dict_eydHJzogVHJ1ZX0)s)", {"p_dict_eydHJzogVHJ1ZX0": {"G": True}}),  # >=
        ("color=g", "(card.card_colors = %(p_dict_eydHJzogVHJ1ZX0)s)", {"p_dict_eydHJzogVHJ1ZX0": {"G": True}}),  # =
        ("color<=g", "(card.card_colors <@ %(p_dict_eydHJzogVHJ1ZX0)s)", {"p_dict_eydHJzogVHJ1ZX0": {"G": True}}),  # <=
        ("color>=g", "(card.card_colors @> %(p_dict_eydHJzogVHJ1ZX0)s)", {"p_dict_eydHJzogVHJ1ZX0": {"G": True}}),  # >=
        (
            "color>g",
            "(card.card_colors @> %(p_dict_eydHJzogVHJ1ZX0)s AND card.card_colors <> %(p_dict_eydHJzogVHJ1ZX0)s)",
            {"p_dict_eydHJzogVHJ1ZX0": {"G": True}},
        ),  # >
        (
            "color<g",
            "(card.card_colors <@ %(p_dict_eydHJzogVHJ1ZX0)s AND card.card_colors <> %(p_dict_eydHJzogVHJ1ZX0)s)",
            {"p_dict_eydHJzogVHJ1ZX0": {"G": True}},
        ),  # <
    ],
)
def test_full_sql_translation(input_query: str, expected_sql: str, expected_parameters: dict) -> None:
    parsed = parsing.parse_scryfall_query(input_query)
    context = {}
    observed_sql = parsed.to_sql(context)
    assert observed_sql == expected_sql
    assert context == expected_parameters


# @pytest.mark.xfail(reason="JSONB queries are not supported yet")
@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql", "expected_parameters"),
    argvalues=[
        (
            "colors:red",
            r"(card.card_colors @> %(p_dict_eydSJzogVHJ1ZX0)s)",
            {"p_dict_eydSJzogVHJ1ZX0": {"R": True}},
        ),  # JSONB object uses containment
        (
            "colors:rg",
            r"(card.card_colors @> %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),  # JSONB object uses containment
        # test exact equality of colors
        (
            "colors=rg",
            r"(card.card_colors = %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),
        # test colors greater than
        (
            "colors>=rg",
            r"(card.card_colors @> %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),
        # test colors less than
        (
            "colors<=rg",
            r"(card.card_colors <@ %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),
        # test colors strictly greater than
        (
            "colors>rg",
            r"(card.card_colors @> %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s AND card.card_colors <> %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),
        # test colors strictly less than
        (
            "colors<rg",
            r"(card.card_colors <@ %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s AND card.card_colors <> %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),
    ],
)
def test_full_sql_translation_jsonb_colors(input_query: str, expected_sql: str, expected_parameters: dict) -> None:
    parsed = parsing.parse_scryfall_query(input_query)
    observed_params = {}
    observed_sql = parsed.to_sql(observed_params)
    assert (observed_sql, observed_params) == (
        expected_sql,
        expected_parameters,
    ), f"\nExpected: {expected_sql}\t{expected_parameters}\nObserved: {observed_sql}\t{observed_params}"@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql", "expected_parameters"),
    argvalues=[
        (
            "color_identity:g",
            r"(card.card_color_identity <@ %(p_dict_eydHJzogVHJ1ZX0)s)",
            {"p_dict_eydHJzogVHJ1ZX0": {"G": True}},
        ),  # : maps to <= for color identity
        (
            "id:rg",
            r"(card.card_color_identity <@ %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),  # id is an alias for color_identity
        (
            "identity=rg",
            r"(card.card_color_identity = %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),  # = still means equality
        (
            "coloridentity>=rg",
            r"(card.card_color_identity @> %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),  # >= maps to >= (no inversion for >=)
        (
            "color_identity<=rg",
            r"(card.card_color_identity <@ %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),  # <= maps to <= (no inversion for <=)
        (
            "identity>g",
            r"(card.card_color_identity @> %(p_dict_eydHJzogVHJ1ZX0)s AND card.card_color_identity <> %(p_dict_eydHJzogVHJ1ZX0)s)",
            {"p_dict_eydHJzogVHJ1ZX0": {"G": True}},
        ),  # > maps to > (no inversion for >)
        (
            "id<rg",
            r"(card.card_color_identity <@ %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s AND card.card_color_identity <> %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),  # < maps to < (no inversion for <)
    ],
)
@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql", "expected_parameters"),
    argvalues=[
        (
            "color_identity:g",
            r"(card.card_color_identity <@ %(p_dict_eydHJzogVHJ1ZX0)s)",
            {"p_dict_eydHJzogVHJ1ZX0": {"G": True}},
        ),  # : maps to <= for color identity
        (
            "id:rg",
            r"(card.card_color_identity <@ %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),  # id is an alias for color_identity
        (
            "identity=rg",
            r"(card.card_color_identity = %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),  # = still means equality
        (
            "coloridentity>=rg",
            r"(card.card_color_identity @> %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),  # >= maps to >= (no inversion for >=)
        (
            "color_identity<=rg",
            r"(card.card_color_identity <@ %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),  # <= maps to <= (no inversion for <=)
        (
            "identity>g",
            r"(card.card_color_identity @> %(p_dict_eydHJzogVHJ1ZX0)s AND card.card_color_identity <> %(p_dict_eydHJzogVHJ1ZX0)s)",
            {"p_dict_eydHJzogVHJ1ZX0": {"G": True}},
        ),  # > maps to > (no inversion for >)
        (
            "id<rg",
            r"(card.card_color_identity <@ %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s AND card.card_color_identity <> %(p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ)s)",
            {"p_dict_eydSJzogVHJ1ZSwgJ0cnOiBUcnVlfQ": {"R": True, "G": True}},
        ),  # < maps to < (no inversion for <)
    ],
)
def test_color_identity_sql_translation(input_query: str, expected_sql: str, expected_parameters: dict) -> None:
    parsed = parsing.parse_scryfall_query(input_query)
    observed_params = {}
    observed_sql = parsed.to_sql(observed_params)
    assert (observed_sql, observed_params) == (
        expected_sql,
        expected_parameters,
    ), f"\nExpected: {expected_sql}\t{expected_parameters}\nObserved: {observed_sql}\t{observed_params}"


@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql", "expected_parameters"),
    argvalues=[
        (
            "card_types:creature",
            r"(%(p_list_WydDcmVhdHVyZSdd)s <@ card.card_types)",
            {"p_list_WydDcmVhdHVyZSdd": ["Creature"]},
        ),
        (
            "t:elf t:archer",
            r"((%(p_list_WydFbGYnXQ)s <@ card.card_subtypes) AND (%(p_list_WydBcmNoZXInXQ)s <@ card.card_subtypes))",
            {"p_list_WydFbGYnXQ": ["Elf"], "p_list_WydBcmNoZXInXQ": ["Archer"]},
        ),
    ],
)
def test_full_sql_translation_jsonb_card_types(input_query: str, expected_sql: str, expected_parameters: dict) -> None:
    parsed = parsing.parse_scryfall_query(input_query)
    observed_params = {}
    observed_sql = parsed.to_sql(observed_params)
    assert (observed_sql, observed_params) == (
        expected_sql,
        expected_parameters,
    ), f"\nExpected: {expected_sql}\t{expected_parameters}\nObserved: {observed_sql}\t{observed_params}"


@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql", "expected_parameters"),
    argvalues=[
        # Oracle text search tests
        ("oracle:flying", "(card.oracle_text ILIKE %(p_str_JWZseWluZyU)s)", {"p_str_JWZseWluZyU": "%flying%"}),
        ("oracle:'gain life'", "(card.oracle_text ILIKE %(p_str_JWdhaW4lbGlmZSU)s)", {"p_str_JWdhaW4lbGlmZSU": "%gain%life%"}),
        ('oracle:"gain life"', "(card.oracle_text ILIKE %(p_str_JWdhaW4lbGlmZSU)s)", {"p_str_JWdhaW4lbGlmZSU": "%gain%life%"}),
        ("oracle:haste", "(card.oracle_text ILIKE %(p_str_JWhhc3RlJQ)s)", {"p_str_JWhhc3RlJQ": "%haste%"}),
        # Test oracle search with complex phrases
        (
            "oracle:'tap target creature'",
            "(card.oracle_text ILIKE %(p_str_JXRhcCV0YXJnZXQlY3JlYXR1cmUl)s)",
            {"p_str_JXRhcCV0YXJnZXQlY3JlYXR1cmUl": "%tap%target%creature%"},
        ),
    ],
)
def test_oracle_text_sql_translation(input_query: str, expected_sql: str, expected_parameters: dict) -> None:
    """Test that oracle text search generates correct SQL with ILIKE patterns."""
    parsed = parsing.parse_scryfall_query(input_query)
    context = {}
    observed_sql = parsed.to_sql(context)
    assert observed_sql == expected_sql
    assert context == expected_parameters


class TestNodes:
    def test_node_equality(self) -> None:
        assert AttributeNode("name") == AttributeNode("name")


def test_arithmetic_vs_negation_ambiguity() -> None:
    """Test that the ambiguity between arithmetic and negation is resolved correctly."""
    # These should be treated as arithmetic operations (both sides are known attributes)
    arithmetic_cases = [
        ("cmc-power", BinaryOperatorNode(AttributeNode("cmc"), "-", AttributeNode("power"))),
        ("power-toughness", BinaryOperatorNode(AttributeNode("power"), "-", AttributeNode("toughness"))),
        ("cmc+power", BinaryOperatorNode(AttributeNode("cmc"), "+", AttributeNode("power"))),
    ]

    for query, expected in arithmetic_cases:
        result = parsing.parse_search_query(query)
        assert result.root == expected, f"Failed for query: {query}"

    # These should be treated as negation (one side is not a known attribute)
    negation_cases = [
        (
            "cmc -flying",
            AndNode(
                [
                    BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("cmc")),
                    NotNode(BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("flying"))),
                ],
            ),
        ),
        (
            "power -goblin",
            AndNode(
                [
                    BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("power")),
                    NotNode(BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("goblin"))),
                ],
            ),
        ),
    ]

    for query, expected in negation_cases:
        result = parsing.parse_search_query(query)
        assert result.root == expected, f"Failed for query: {query}"


@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql", "expected_parameters"),
    argvalues=[
        # Basic keyword search
        (
            "keyword:flying",
            r"(card.card_keywords @> %(p_dict_eydGbHlpbmcnOiBUcnVlfQ)s)",
            {"p_dict_eydGbHlpbmcnOiBUcnVlfQ": {"Flying": True}},
        ),
        # Keyword search with colon operator (should behave like @>)
        (
            "keyword:trample",
            r"(card.card_keywords @> %(p_dict_eydUcmFtcGxlJzogVHJ1ZX0)s)",
            {"p_dict_eydUcmFtcGxlJzogVHJ1ZX0": {"Trample": True}},
        ),
        # Keyword search (updated from alias 'k')
        (
            "keyword:haste",
            r"(card.card_keywords @> %(p_dict_eydIYXN0ZSc6IFRydWV9)s)",
            {"p_dict_eydIYXN0ZSc6IFRydWV9": {"Haste": True}},
        ),
        # Keyword equality
        (
            "keyword=vigilance",
            r"(card.card_keywords = %(p_dict_eydWaWdpbGFuY2UnOiBUcnVlfQ)s)",
            {"p_dict_eydWaWdpbGFuY2UnOiBUcnVlfQ": {"Vigilance": True}},
        ),
        # Custom keyword (not in the predefined list)
        (
            "keyword:customability",
            r"(card.card_keywords @> %(p_dict_eydDdXN0b21hYmlsaXR5JzogVHJ1ZX0)s)",
            {"p_dict_eydDdXN0b21hYmlsaXR5JzogVHJ1ZX0": {"Customability": True}},
        ),
        # Test different operators
        (
            "keyword>=flying",
            r"(card.card_keywords @> %(p_dict_eydGbHlpbmcnOiBUcnVlfQ)s)",
            {"p_dict_eydGbHlpbmcnOiBUcnVlfQ": {"Flying": True}},
        ),
        (
            "keyword<=haste",
            r"(card.card_keywords <@ %(p_dict_eydIYXN0ZSc6IFRydWV9)s)",
            {"p_dict_eydIYXN0ZSc6IFRydWV9": {"Haste": True}},
        ),
        (
            "keyword>trample",
            r"(card.card_keywords @> %(p_dict_eydUcmFtcGxlJzogVHJ1ZX0)s AND card.card_keywords <> %(p_dict_eydUcmFtcGxlJzogVHJ1ZX0)s)",
            {"p_dict_eydUcmFtcGxlJzogVHJ1ZX0": {"Trample": True}},
        ),
        (
            "keyword<vigilance",
            r"(card.card_keywords <@ %(p_dict_eydWaWdpbGFuY2UnOiBUcnVlfQ)s AND card.card_keywords <> %(p_dict_eydWaWdpbGFuY2UnOiBUcnVlfQ)s)",
            {"p_dict_eydWaWdpbGFuY2UnOiBUcnVlfQ": {"Vigilance": True}},
        ),
        (
            "keyword!=flying",
            r"(card.card_keywords <> %(p_dict_eydGbHlpbmcnOiBUcnVlfQ)s)",
            {"p_dict_eydGbHlpbmcnOiBUcnVlfQ": {"Flying": True}},
        ),
    ],
)
def test_keyword_sql_translation(input_query: str, expected_sql: str, expected_parameters: dict) -> None:
    """Test that keyword search generates correct SQL with JSONB operators."""
    parsed = parsing.parse_scryfall_query(input_query)
    context = {}
    observed_sql = parsed.to_sql(context)
    assert observed_sql == expected_sql, f"\nExpected: {expected_sql}\nObserved: {observed_sql}"
    assert context == expected_parameters, f"\nExpected params: {expected_parameters}\nObserved params: {context}"


@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql", "expected_parameters"),
    argvalues=[
        # Basic oracle tag search (should be lowercase)
        (
            "otag:flying",
            r"(card.card_oracle_tags @> %(p_dict_eydmbHlpbmcnOiBUcnVlfQ)s)",
            {"p_dict_eydmbHlpbmcnOiBUcnVlfQ": {"flying": True}},
        ),
        # Oracle tag search with hyphenated term - this currently fails but should work
        (
            "otag:dual-land",
            r"(card.card_oracle_tags @> %(p_dict_eydkdWFsLWxhbmQnOiBUcnVlfQ)s)",
            {"p_dict_eydkdWFsLWxhbmQnOiBUcnVlfQ": {"dual-land": True}},
        ),
        # Oracle tag with quoted hyphenated term should also work (and currently does)
        (
            'otag:"dual-land"',
            r"(card.card_oracle_tags @> %(p_dict_eydkdWFsLWxhbmQnOiBUcnVlfQ)s)",
            {"p_dict_eydkdWFsLWxhbmQnOiBUcnVlfQ": {"dual-land": True}},
        ),
        # Oracle tag with alias 'otag'
        (
            "otag:haste",
            r"(card.card_oracle_tags @> %(p_dict_eydoYXN0ZSc6IFRydWV9)s)",
            {"p_dict_eydoYXN0ZSc6IFRydWV9": {"haste": True}},
        ),
        # Oracle tag with numeric prefix like "40k-model" - issue #110
        (
            "otag:40k-model",
            r"(card.card_oracle_tags @> %(p_dict_eyc0MGstbW9kZWwnOiBUcnVlfQ)s)",
            {"p_dict_eyc0MGstbW9kZWwnOiBUcnVlfQ": {"40k-model": True}},
        ),
        # Oracle tag with complex hyphenated value containing digits
        (
            "otag:cycle-shm-common-hybrid-1-drop",
            r"(card.card_oracle_tags @> %(p_dict_eydjeWNsZS1zaG0tY29tbW9uLWh5YnJpZC0xLWRyb3AnOiBUcnVlfQ)s)",
            {"p_dict_eydjeWNsZS1zaG0tY29tbW9uLWh5YnJpZC0xLWRyb3AnOiBUcnVlfQ": {"cycle-shm-common-hybrid-1-drop": True}},
        ),
    ],
)
def test_oracle_tag_sql_translation(input_query: str, expected_sql: str, expected_parameters: dict) -> None:
    """Test that oracle tag search generates correct SQL with lowercase tags."""
    parsed = parsing.parse_scryfall_query(input_query)
    context = {}
    observed_sql = parsed.to_sql(context)
    assert observed_sql == expected_sql, f"\nExpected: {expected_sql}\nObserved: {observed_sql}"
    assert context == expected_parameters, f"\nExpected params: {expected_parameters}\nObserved params: {context}"


@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql", "expected_parameters"),
    argvalues=[
        # Case-insensitive oracle tag search
        (
            "Otag:flying",
            r"(card.card_oracle_tags @> %(p_dict_eydmbHlpbmcnOiBUcnVlfQ)s)",
            {"p_dict_eydmbHlpbmcnOiBUcnVlfQ": {"flying": True}},
        ),
        (
            "OTAG:flying",
            r"(card.card_oracle_tags @> %(p_dict_eydmbHlpbmcnOiBUcnVlfQ)s)",
            {"p_dict_eydmbHlpbmcnOiBUcnVlfQ": {"flying": True}},
        ),
        (
            "oTaG:flying",
            r"(card.card_oracle_tags @> %(p_dict_eydmbHlpbmcnOiBUcnVlfQ)s)",
            {"p_dict_eydmbHlpbmcnOiBUcnVlfQ": {"flying": True}},
        ),
        # Case-insensitive color attribute search
        (
            "Color:red",
            r"(card.card_colors @> %(p_dict_eydSJzogVHJ1ZX0)s)",
            {"p_dict_eydSJzogVHJ1ZX0": {"R": True}},
        ),
        (
            "COLOR:red",
            r"(card.card_colors @> %(p_dict_eydSJzogVHJ1ZX0)s)",
            {"p_dict_eydSJzogVHJ1ZX0": {"R": True}},
        ),
        # Case-insensitive single-letter alias
        (
            "C:red",
            r"(card.card_colors @> %(p_dict_eydSJzogVHJ1ZX0)s)",
            {"p_dict_eydSJzogVHJ1ZX0": {"R": True}},
        ),
        # Case-insensitive type attribute search
        (
            "Type:creature",
            r"(%(p_list_WydDcmVhdHVyZSdd)s <@ card.card_types)",
            {"p_list_WydDcmVhdHVyZSdd": ["Creature"]},
        ),
        (
            "TYPE:creature",
            r"(%(p_list_WydDcmVhdHVyZSdd)s <@ card.card_types)",
            {"p_list_WydDcmVhdHVyZSdd": ["Creature"]},
        ),
        # Case-insensitive alias 't'
        (
            "T:creature",
            r"(%(p_list_WydDcmVhdHVyZSdd)s <@ card.card_types)",
            {"p_list_WydDcmVhdHVyZSdd": ["Creature"]},
        ),
    ],
)
def test_case_insensitive_attributes(input_query: str, expected_sql: str, expected_parameters: dict) -> None:
    """Test that attribute names are case-insensitive."""
    parsed = parsing.parse_scryfall_query(input_query)
    context = {}
    observed_sql = parsed.to_sql(context)
    assert observed_sql == expected_sql, f"\nExpected: {expected_sql}\nObserved: {observed_sql}"
    assert context == expected_parameters, f"\nExpected params: {expected_parameters}\nObserved params: {context}"


@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql", "expected_parameters"),
    argvalues=[
        # Basic set search with full 'set:' syntax
        (
            "set:iko",
            r"(card.card_set_code = %(p_str_aWtv)s)",
            {"p_str_aWtv": "iko"},
        ),
        # Set search with 's:' shorthand
        (
            "s:iko",
            r"(card.card_set_code = %(p_str_aWtv)s)",
            {"p_str_aWtv": "iko"},
        ),
        # Case-insensitive set attribute search
        (
            "SET:iko",
            r"(card.card_set_code = %(p_str_aWtv)s)",
            {"p_str_aWtv": "iko"},
        ),
        # Set search with different set codes
        (
            "set:thb",
            r"(card.card_set_code = %(p_str_dGhi)s)",
            {"p_str_dGhi": "thb"},
        ),
        # Set search with multiple characters
        (
            "s:m21",
            r"(card.card_set_code = %(p_str_bTIx)s)",
            {"p_str_bTIx": "m21"},
        ),
    ],
)
def test_set_search_sql_translation(input_query: str, expected_sql: str, expected_parameters: dict) -> None:
    """Test that set searches generate correct SQL."""
    parsed = parsing.parse_scryfall_query(input_query)
    context = {}
    observed_sql = parsed.to_sql(context)
    assert observed_sql == expected_sql, f"\nExpected: {expected_sql}\nObserved: {observed_sql}"
    assert context == expected_parameters, f"\nExpected params: {expected_parameters}\nObserved params: {context}"


def generate_arithmetic_parser_testcases() -> list[tuple[str, BinaryOperatorNode]]:
    """Generate all 25 combinations using cross product for parametrized testing.

    Returns a list of (query, expected_ast) tuples covering all combinations of
    5 expression types: literal, literal arithmetic, mixed arithmetic,
    attribute arithmetic, and attribute.
    """
    expression_types = [
        ("1", NumericValueNode(1)),
        ("1+1", BinaryOperatorNode(NumericValueNode(1), "+", NumericValueNode(1))),
        ("cmc+1", BinaryOperatorNode(AttributeNode("cmc"), "+", NumericValueNode(1))),
        ("cmc+power", BinaryOperatorNode(AttributeNode("cmc"), "+", AttributeNode("power"))),
        ("power", AttributeNode("power")),
    ]

    query_ast_pairs = []
    for lhs_query, lhs_ast in expression_types:
        for rhs_query, rhs_ast in expression_types:
            query = f"{lhs_query}<{rhs_query}"
            expected_ast = BinaryOperatorNode(lhs_ast, "<", rhs_ast)
            query_ast_pairs.append((query, expected_ast))

    return query_ast_pairs


@pytest.mark.parametrize(argnames=("query", "expected_ast"), argvalues=generate_arithmetic_parser_testcases())
def test_arithmetic_parser_consolidation(query: str, expected_ast: BinaryOperatorNode) -> None:
    """Test that the fully consolidated arithmetic parser rules handle all cases correctly.

    This parametrized test runs each of the 25 combinations as separate test cases,
    making it easier to identify specific failures and providing better test reporting.

    Tests all combinations of 5 expression types:
    1. literal number
    2. arithmetic with just literal numbers
    3. arithmetic with numbers and numeric attributes
    4. arithmetic with numeric attributes
    5. only numeric attribute

    This verifies that after removing redundant rules and consolidating into a single
    unified_numeric_comparison rule, all arithmetic parsing still works correctly.
    """
    observed = parsing.parse_search_query(query).root
    assert observed == expected_ast, f"Query '{query}' failed\nExpected: {expected_ast}\nObserved: {observed}"


@pytest.mark.parametrize(
    argnames="invalid_query",
    argvalues=[
        "name:test and",      # Trailing AND with no right operand
        "power>1 or",         # Trailing OR with no right operand
        "cmc=3 and ()",       # Empty parentheses after AND
    ],
)
def test_invalid_queries_with_trailing_content_fail(invalid_query: str) -> None:
    """Test that queries with invalid trailing content properly fail to parse.

    This addresses issue #86 where invalid trailing content was being silently ignored.

    Note: Since issue #90, standalone numeric literals like "1" are now valid parse targets,
    so queries like "name:bolt and 1" now parse successfully (though they fail at DB level
    with datatype mismatch errors).
    """
    with pytest.raises(ValueError, match="Failed to parse query"):
        parsing.parse_scryfall_query(invalid_query)


@pytest.mark.parametrize(
    argnames="semantically_invalid_query",
    argvalues=[
        "name:bolt and 1",    # Valid parse but semantically invalid: AND between boolean and integer
        "cmc=3 and 2",        # Valid parse but semantically invalid: AND between boolean and integer
        "power>1 or 5",       # Valid parse but semantically invalid: OR between boolean and integer
    ],
)
def test_semantically_invalid_queries_parse_but_fail_at_db_level(semantically_invalid_query: str) -> None:
    """Test that queries with standalone numeric literals parse but would fail at DB level.

    These queries are syntactically valid after issue #90 (allowing standalone numeric literals),
    but they're semantically invalid because they combine boolean expressions with bare integers.
    They should parse successfully but would fail at the database level with datatype mismatch errors.
    """
    # These should parse without errors
    parsed_query = parsing.parse_scryfall_query(semantically_invalid_query)

    # Should be able to generate SQL (though it would fail at execution)
    sql, context = generate_sql_query(parsed_query)

    # SQL should be generated successfully (it's the execution that would fail)
    assert isinstance(sql, str)
    assert isinstance(context, dict)


def test_standalone_numeric_query_parses() -> None:
    """Test that standalone numeric queries like '1' parse to NumericValueNode.

    Per issue #90, queries like '1' should parse successfully to a NumericValueNode,
    but then fail at the database level with a datatype mismatch error since
    PostgreSQL expects boolean values in WHERE clauses, not integers.
    """
    # Test integer
    parsed_query = parsing.parse_scryfall_query("1")
    assert isinstance(parsed_query.root, NumericValueNode)
    assert parsed_query.root.value == 1

    # Test float
    parsed_query_float = parsing.parse_scryfall_query("2.5")
    assert isinstance(parsed_query_float.root, NumericValueNode)
    assert parsed_query_float.root.value == 2.5

    # Test SQL generation - this should produce a parameterized query
    sql, context = generate_sql_query(parsed_query)

    # Should be a parameterized value
    assert sql.startswith("%(")
    assert sql.endswith(")s")
    # Context should contain the numeric value
    assert 1 in context.values()


@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql", "expected_parameters"),
    argvalues=[
        # Basic rarity equality searches
        (
            "rarity:common",
            "(card.card_rarity_int = %(p_int_MA)s)",
            {"p_int_MA": 0},
        ),
        (
            "rarity:uncommon",
            "(card.card_rarity_int = %(p_int_MQ)s)",
            {"p_int_MQ": 1},
        ),
        (
            "rarity:rare",
            "(card.card_rarity_int = %(p_int_Mg)s)",
            {"p_int_Mg": 2},
        ),
        (
            "rarity:mythic",
            "(card.card_rarity_int = %(p_int_Mw)s)",
            {"p_int_Mw": 3},
        ),
        (
            "rarity:special",
            "(card.card_rarity_int = %(p_int_NA)s)",
            {"p_int_NA": 4},
        ),
        (
            "rarity:bonus",
            "(card.card_rarity_int = %(p_int_NQ)s)",
            {"p_int_NQ": 5},
        ),
        # Short alias tests
        (
            "r:common",
            "(card.card_rarity_int = %(p_int_MA)s)",
            {"p_int_MA": 0},
        ),
        (
            "r:mythic",
            "(card.card_rarity_int = %(p_int_Mw)s)",
            {"p_int_Mw": 3},
        ),
        # Comparison operators - greater than
        (
            "rarity>common",
            "(card.card_rarity_int > %(p_int_MA)s)",
            {"p_int_MA": 0},
        ),
        (
            "rarity>uncommon",
            "(card.card_rarity_int > %(p_int_MQ)s)",
            {"p_int_MQ": 1},
        ),
        # Comparison operators - greater than or equal
        (
            "rarity>=rare",
            "(card.card_rarity_int >= %(p_int_Mg)s)",
            {"p_int_Mg": 2},
        ),
        # Comparison operators - less than
        (
            "rarity<rare",
            "(card.card_rarity_int < %(p_int_Mg)s)",
            {"p_int_Mg": 2},
        ),
        # Comparison operators - less than or equal
        (
            "rarity<=uncommon",
            "(card.card_rarity_int <= %(p_int_MQ)s)",
            {"p_int_MQ": 1},
        ),
        # Comparison operators - not equal
        (
            "rarity!=common",
            "(card.card_rarity_int != %(p_int_MA)s)",
            {"p_int_MA": 0},
        ),
        # Short alias with comparison
        (
            "r>common",
            "(card.card_rarity_int > %(p_int_MA)s)",
            {"p_int_MA": 0},
        ),
    ],
)
def test_rarity_search_sql_translation(input_query: str, expected_sql: str, expected_parameters: dict) -> None:
    """Test that rarity search generates correct SQL with proper ordering."""
    parsed = parsing.parse_scryfall_query(input_query)
    context = {}
    observed_sql = parsed.to_sql(context)
    assert observed_sql == expected_sql, f"\nExpected: {expected_sql}\nObserved: {observed_sql}"
    assert context == expected_parameters, f"\nExpected params: {expected_parameters}\nObserved params: {context}"


def test_rarity_invalid_values() -> None:
    """Test that invalid rarity values raise appropriate errors."""
    # This should parse successfully but fail during SQL generation

    parsed = parsing.parse_scryfall_query("rarity>invalid")

    # Should raise ValueError when generating SQL due to invalid rarity
    with pytest.raises(ValueError, match="Invalid rarity in comparison"):
        generate_sql_query(parsed)

    # Test with another invalid rarity
    parsed2 = parsing.parse_scryfall_query("r<unknown")

    with pytest.raises(ValueError, match="Invalid rarity in comparison"):
        generate_sql_query(parsed2)


def test_rarity_case_insensitive() -> None:
    """Test that rarity values are case-insensitive."""
    # Test different cases for equality
    queries = ["rarity:Common", "rarity:RARE", "r:Mythic", "rarity:UnComMoN"]

    for query_str in queries:
        parsed = parsing.parse_scryfall_query(query_str)
        sql, params = generate_sql_query(parsed)

        # Should not raise errors and should generate valid SQL
        assert sql.startswith("(card.card_rarity_int")
        assert len(params) == 1

    # Test different cases for comparisons
    parsed_comparison = parsing.parse_scryfall_query("rarity>Common")
    sql, params = generate_sql_query(parsed_comparison)

    # Should contain simple numeric comparison and not raise errors
    assert "card.card_rarity_int >" in sql
    assert params[next(iter(params.keys()))] == 0  # common = 0


@pytest.mark.parametrize(
    argnames=("input_query", "expected_ast"),
    argvalues=[
        ("artist:moeller", BinaryOperatorNode(AttributeNode("artist"), ":", StringValueNode("moeller"))),
        ("a:moeller", BinaryOperatorNode(AttributeNode("a"), ":", StringValueNode("moeller"))),
        ('artist:"Christopher Moeller"', BinaryOperatorNode(AttributeNode("artist"), ":", StringValueNode("Christopher Moeller"))),
        ("artist:nielsen", BinaryOperatorNode(AttributeNode("artist"), ":", StringValueNode("nielsen"))),
        ("ARTIST:moeller", BinaryOperatorNode(AttributeNode("ARTIST"), ":", StringValueNode("moeller"))),
    ],
)
def test_parse_artist_searches(input_query: str, expected_ast: BinaryOperatorNode) -> None:
    """Test parsing artist search queries."""
    result = parsing.parse_search_query(input_query)
    assert result.root == expected_ast


@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql", "expected_parameters"),
    argvalues=[
        ("artist:moeller", r"(card.card_artist ILIKE %(p_str_JW1vZWxsZXIl)s)", {"p_str_JW1vZWxsZXIl": r"%moeller%"}),
        ("a:moeller", r"(card.card_artist ILIKE %(p_str_JW1vZWxsZXIl)s)", {"p_str_JW1vZWxsZXIl": r"%moeller%"}),
        ('artist:"Christopher Moeller"', r"(card.card_artist ILIKE %(p_str_JUNocmlzdG9waGVyJU1vZWxsZXIl)s)", {"p_str_JUNocmlzdG9waGVyJU1vZWxsZXIl": r"%Christopher%Moeller%"}),
        ("artist:nielsen", r"(card.card_artist ILIKE %(p_str_JW5pZWxzZW4l)s)", {"p_str_JW5pZWxzZW4l": r"%nielsen%"}),
        ("ARTIST:moeller", r"(card.card_artist ILIKE %(p_str_JW1vZWxsZXIl)s)", {"p_str_JW1vZWxsZXIl": r"%moeller%"}),
    ],
)
def test_artist_sql_translation(input_query: str, expected_sql: str, expected_parameters: dict) -> None:
    """Test SQL generation for artist search queries."""
    parsed = parsing.parse_scryfall_query(input_query)
    context = {}
    observed_sql = parsed.to_sql(context)
    assert observed_sql == expected_sql
    assert context == expected_parameters


def test_parse_combined_artist_queries() -> None:
    """Test parsing combined queries with artist attributes."""
    # Test combining artist with other attributes
    query1 = "cmc<=3 artist:moeller"
    result1 = parsing.parse_search_query(query1)
    expected1 = AndNode([
        BinaryOperatorNode(AttributeNode("cmc"), "<=", NumericValueNode(3)),
        BinaryOperatorNode(AttributeNode("artist"), ":", StringValueNode("moeller")),
    ])
    assert result1.root == expected1

    # Test combining multiple text attributes including artist
    query2 = "name:lightning OR artist:moeller"
    result2 = parsing.parse_search_query(query2)
    expected2 = OrNode([
        BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("lightning")),
        BinaryOperatorNode(AttributeNode("artist"), ":", StringValueNode("moeller")),
    ])
    assert result2.root == expected2


@pytest.mark.parametrize(
    argnames=("test_input", "expected_ast"),
    argvalues=[
        ("format:standard", BinaryOperatorNode(AttributeNode("format"), ":", StringValueNode("standard"))),
        ("f:modern", BinaryOperatorNode(AttributeNode("f"), ":", StringValueNode("modern"))),
        ("legal:legacy", BinaryOperatorNode(AttributeNode("legal"), ":", StringValueNode("legacy"))),
        ("banned:standard", BinaryOperatorNode(AttributeNode("banned"), ":", StringValueNode("standard"))),
        ("restricted:vintage", BinaryOperatorNode(AttributeNode("restricted"), ":", StringValueNode("vintage"))),
        ('format:"Commander"', BinaryOperatorNode(AttributeNode("format"), ":", StringValueNode("Commander"))),
        ("FORMAT:standard", BinaryOperatorNode(AttributeNode("FORMAT"), ":", StringValueNode("standard"))),
        ("LEGAL:modern", BinaryOperatorNode(AttributeNode("LEGAL"), ":", StringValueNode("modern"))),
    ],
)
def test_parse_legality_searches(test_input: str, expected_ast: QueryNode) -> None:
    """Test that legality search queries parse to expected AST nodes."""
    result = parsing.parse_search_query(test_input)
    assert result.root == expected_ast


@pytest.mark.parametrize(
    argnames=("input_query", "expected_parameters"),
    argvalues=[
        # Basic format search (format: means legal in format)
        (
            "format:standard",
            {"standard": "legal"},
        ),
        # Format alias 'f:'
        (
            "f:modern",
            {"modern": "legal"},
        ),
        # Legal search (explicit legal status)
        (
            "legal:legacy",
            {"legacy": "legal"},
        ),
        # Banned search
        (
            "banned:standard",
            {"standard": "banned"},
        ),
        # Restricted search
        (
            "restricted:vintage",
            {"vintage": "restricted"},
        ),
        # Case insensitive format names
        (
            "format:Standard",
            {"standard": "legal"},
        ),
        # Format with spaces in quotes
        (
            'format:"Historic Brawl"',
            {"historic brawl": "legal"},
        ),
    ],
)
def test_legality_search_sql_translation(input_query: str, expected_parameters: dict) -> None:
    """Test that legality search generates correct SQL with JSONB operators."""
    parsed = parsing.parse_scryfall_query(input_query)
    context = {}
    observed_sql = parsed.to_sql(context)
    # Note: The parameter names will be auto-generated hashes, so we need a more flexible comparison
    assert "card.card_legalities @>" in observed_sql, f"Expected JSONB containment in SQL: {observed_sql}"
    # Check that we have exactly one parameter
    assert len(context) == 1, f"Expected exactly one parameter in context: {context}"

    # Verify the parameter value matches expected format and status
    param_value = next(iter(context.values()))
    assert param_value == expected_parameters, f"Expected parameter value: {expected_parameters}, got: {param_value}"


def test_legality_invalid_attribute() -> None:
    """Test that invalid legality attributes raise appropriate errors."""
    with pytest.raises(ValueError, match="Unknown legality attribute"):
        get_legality_comparison_object("standard", "invalid_attr")


def test_parse_combined_legality_queries() -> None:
    """Test parsing of complex queries combining legality searches."""
    # Test AND combination
    query1 = "format:standard banned:modern"
    result1 = parsing.parse_search_query(query1)
    expected1 = AndNode([
        BinaryOperatorNode(AttributeNode("format"), ":", StringValueNode("standard")),
        BinaryOperatorNode(AttributeNode("banned"), ":", StringValueNode("modern")),
    ])
    assert result1.root == expected1

    # Test OR combination
    query2 = "legal:legacy or restricted:vintage"
    result2 = parsing.parse_search_query(query2)
    expected2 = OrNode([
        BinaryOperatorNode(AttributeNode("legal"), ":", StringValueNode("legacy")),
        BinaryOperatorNode(AttributeNode("restricted"), ":", StringValueNode("vintage")),
    ])
    assert result2.root == expected2


@pytest.mark.parametrize(
    argnames=("test_input", "expected_ast"),
    argvalues=[
        ("number:123", BinaryOperatorNode(AttributeNode("number"), ":", StringValueNode("123"))),
        ("cn:45", BinaryOperatorNode(AttributeNode("cn"), ":", StringValueNode("45"))),
        ("number:1a", BinaryOperatorNode(AttributeNode("number"), ":", StringValueNode("1a"))),
        ("cn:100b", BinaryOperatorNode(AttributeNode("cn"), ":", StringValueNode("100b"))),
        ('number:"123"', BinaryOperatorNode(AttributeNode("number"), ":", StringValueNode("123"))),
        ("cn:'45a'", BinaryOperatorNode(AttributeNode("cn"), ":", StringValueNode("45a"))),
        ("NUMBER:123", BinaryOperatorNode(AttributeNode("NUMBER"), ":", StringValueNode("123"))),
        ("CN:45", BinaryOperatorNode(AttributeNode("CN"), ":", StringValueNode("45"))),
    ],
)
def test_parse_collector_number_searches(test_input: str, expected_ast: BinaryOperatorNode) -> None:
    """Test parsing of collector number searches with various aliases and formats."""
    observed = parsing.parse_search_query(test_input)
    assert observed.root == expected_ast


@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql_fragment", "expected_parameters"),
    argvalues=[
        (
            "number:123",
            "(card.collector_number = %(p_str_",
            {"123"},
        ),
        (
            "cn:45",
            "(card.collector_number = %(p_str_",
            {"45"},
        ),
        (
            "number:1a",
            "(card.collector_number = %(p_str_",
            {"1a"},
        ),
        (
            "cn:100b",
            "(card.collector_number = %(p_str_",
            {"100b"},
        ),
        (
            'number:"123"',
            "(card.collector_number = %(p_str_",
            {"123"},
        ),
    ],
)
def test_collector_number_sql_translation(input_query: str, expected_sql_fragment: str, expected_parameters: set) -> None:
    """Test that collector number searches generate correct SQL with exact matching for colon operator."""
    parsed = parsing.parse_scryfall_query(input_query)
    context = {}
    observed_sql = parsed.to_sql(context)
    assert expected_sql_fragment in observed_sql, f"Expected SQL fragment in: {observed_sql}"
    # Check that we have exactly one parameter
    assert len(context) == 1, f"Expected exactly one parameter in context: {context}"
    # Verify the parameter value is in expected set
    param_value = next(iter(context.values()))
    assert param_value in expected_parameters, f"Expected parameter value in {expected_parameters}, got: {param_value}"


@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql_fragment", "expected_parameters"),
    argvalues=[
        (
            "number>50",
            "(card.collector_number_int > %(p_int_",
            {50},
        ),
        (
            "cn<100",
            "(card.collector_number_int < %(p_int_",
            {100},
        ),
        (
            "number>=25",
            "(card.collector_number_int >= %(p_int_",
            {25},
        ),
        (
            "cn<=75",
            "(card.collector_number_int <= %(p_int_",
            {75},
        ),
    ],
)
def test_collector_number_numeric_comparison_sql_translation(input_query: str, expected_sql_fragment: str, expected_parameters: set) -> None:
    """Test that collector number numeric comparisons generate correct SQL using the integer column."""
    parsed = parsing.parse_scryfall_query(input_query)
    context = {}
    observed_sql = parsed.to_sql(context)
    assert expected_sql_fragment in observed_sql, f"Expected SQL fragment in: {observed_sql}"
    # Check that we have exactly one parameter
    assert len(context) == 1, f"Expected exactly one parameter in context: {context}"
    # Verify the parameter value is in expected set
    param_value = next(iter(context.values()))
    assert param_value in expected_parameters, f"Expected parameter value in {expected_parameters}, got: {param_value}"


def test_parse_combined_collector_number_queries() -> None:
    """Test parsing of complex queries combining collector number searches."""
    # Test AND combination
    query1 = "number:123 set:dom"
    result1 = parsing.parse_search_query(query1)
    expected1 = AndNode([
        BinaryOperatorNode(AttributeNode("number"), ":", StringValueNode("123")),
        BinaryOperatorNode(AttributeNode("set"), ":", StringValueNode("dom")),
    ])
    assert result1.root == expected1

    # Test OR combination
    query2 = "cn:1 or cn:2"
    result2 = parsing.parse_search_query(query2)
    expected2 = OrNode([
        BinaryOperatorNode(AttributeNode("cn"), ":", StringValueNode("1")),
        BinaryOperatorNode(AttributeNode("cn"), ":", StringValueNode("2")),
    ])
    assert result2.root == expected2
