"""Tests for query parsing functionality."""

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


@pytest.mark.parametrize(
    argnames=("test_input", "expected_ast"),
    argvalues=[
        ("cmc=3", BinaryOperatorNode(AttributeNode("cmc"), "=", NumericValueNode(3))),
        (
            "cmc=3 power=3",
            AndNode(
                [
                    BinaryOperatorNode(AttributeNode("cmc"), "=", NumericValueNode(3)),
                    BinaryOperatorNode(AttributeNode("power"), "=", NumericValueNode(3)),
                ],
            ),
        ),
        ("name:'power'", BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("power"))),
        ('name:"power"', BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("power"))),
        (
            "cmc+cmc<power+toughness",
            BinaryOperatorNode(
                BinaryOperatorNode(AttributeNode("cmc"), "+", AttributeNode("cmc")),
                "<",
                BinaryOperatorNode(AttributeNode("power"), "+", AttributeNode("toughness")),
            ),
        ),
        (
            "cmc+1<power",
            BinaryOperatorNode(BinaryOperatorNode(AttributeNode("cmc"), "+", NumericValueNode(1)), "<", AttributeNode("power")),
        ),
        (
            "cmc<power+1",
            BinaryOperatorNode(AttributeNode("cmc"), "<", BinaryOperatorNode(AttributeNode("power"), "+", NumericValueNode(1))),
        ),
        (
            "cmc+1<power+2",
            BinaryOperatorNode(
                BinaryOperatorNode(AttributeNode("cmc"), "+", NumericValueNode(1)),
                "<",
                BinaryOperatorNode(AttributeNode("power"), "+", NumericValueNode(2)),
            ),
        ),
        ("cmc+power", BinaryOperatorNode(AttributeNode("cmc"), "+", AttributeNode("power"))),
        ("cmc-power", BinaryOperatorNode(AttributeNode("cmc"), "-", AttributeNode("power"))),
        (
            "cmc + 1 < power",
            BinaryOperatorNode(BinaryOperatorNode(AttributeNode("cmc"), "+", NumericValueNode(1)), "<", AttributeNode("power")),
        ),
        # Test cases for the numeric < attribute bug
        ("0<power", BinaryOperatorNode(NumericValueNode(0), "<", AttributeNode("power"))),
        ("1<power", BinaryOperatorNode(NumericValueNode(1), "<", AttributeNode("power"))),
        ("3>cmc", BinaryOperatorNode(NumericValueNode(3), ">", AttributeNode("cmc"))),
        ("0<=toughness", BinaryOperatorNode(NumericValueNode(0), "<=", AttributeNode("toughness"))),
    ],
)
def test_parse_to_nodes(test_input: str, expected_ast: QueryNode) -> None:
    """Test that queries parse into the expected AST structure."""
    observed = parsing.parse_search_query(test_input).root

    # Compare the full AST structure
    assert observed == expected_ast, f"\nExpected: {expected_ast}\nObserved: {observed}"


def test_parse_simple_condition() -> None:
    """Test parsing a simple condition."""
    query = "cmc:2"
    result = parsing.parse_search_query(query)
    expected = BinaryOperatorNode(AttributeNode("cmc"), ":", NumericValueNode(2))
    assert result.root == expected


def test_parse_and_operation() -> None:
    """Test parsing AND operations."""
    query = "a AND b"
    result = parsing.parse_search_query(query).root

    assert result == AndNode(
        [
            BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("a")),
            BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("b")),
        ],
    )


def test_parse_or_operation() -> None:
    """Test parsing OR operations."""
    query = "a OR b"
    result = parsing.parse_search_query(query).root
    assert result == OrNode(
        [
            BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("a")),
            BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("b")),
        ],
    )


def test_parse_implicit_and() -> None:
    """Test parsing implicit AND operations."""
    query = "a b"
    result = parsing.parse_search_query(query).root
    assert result == AndNode(
        [
            BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("a")),
            BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("b")),
        ],
    )


def test_parse_complex_nested() -> None:
    """Test parsing complex nested queries."""
    query = "cmc:2 AND (oracle:flying OR oracle:haste)"
    result = parsing.parse_search_query(query)

    assert isinstance(result, parsing.Query)
    assert isinstance(result.root, AndNode)
    assert len(result.root.operands) == 2
    # The right side should be an OR operation
    assert isinstance(result.root.operands[1], OrNode)


def test_parse_quoted_strings() -> None:
    """Test parsing quoted strings."""
    query = 'name:"Lightning Bolt"'
    observed_ast = parsing.parse_search_query(query)
    expected_ast = BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("Lightning Bolt"))
    assert observed_ast.root == expected_ast


def test_parse_different_operators() -> None:
    """Test parsing different comparison operators."""
    operators = [">", "<", ">=", "<=", "=", "!="]

    for op in operators:
        query = f"cmc{op}3"
        result = parsing.parse_search_query(query)
        expected = BinaryOperatorNode(AttributeNode("cmc"), op, NumericValueNode(3))
        assert result.root == expected


def test_parse_empty_query() -> None:
    """Test parsing empty or None queries."""
    # Empty string
    result = parsing.parse_search_query("")
    assert isinstance(result, parsing.Query)

    # None
    result = parsing.parse_search_query(None)
    assert isinstance(result, parsing.Query)


def test_name_vs_name_attribute() -> None:
    """Test that we can distinguish between the string 'name' and card names."""
    # This should create a BinaryOperatorNode for "name" (searching for cards with "name" in their name)
    query1 = "name"
    result1 = parsing.parse_search_query(query1)
    expected = BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("name"))
    assert result1.root == expected

    # This should create a BinaryOperatorNode for name:value (same as bare word "value")
    query2 = "name:value"
    result2 = parsing.parse_search_query(query2)
    expected = BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("value"))
    assert result2.root == expected

    # This should create a BinaryOperatorNode for cmc operations
    query3 = "cmc:3"
    result3 = parsing.parse_search_query(query3)
    expected = BinaryOperatorNode(AttributeNode("cmc"), ":", NumericValueNode(3))
    assert result3.root == expected

    # This should create a BinaryOperatorNode for other attributes
    query4 = "oracle:flying"
    result4 = parsing.parse_search_query(query4)
    expected = BinaryOperatorNode(AttributeNode("oracle"), ":", StringValueNode("flying"))
    assert result4.root == expected


@pytest.mark.parametrize(
    argnames="operator",
    argvalues=["AND", "OR"],
)
def test_nary_operator_associativity(operator: str) -> None:
    """Test that AND operator associativity now creates the same AST structure."""
    # These should now create the same AST structure with n-ary operations
    query1 = f"a {operator} (b {operator} c)"
    query2 = f"(a {operator} b) {operator} c"

    result1 = parsing.parse_search_query(query1)
    result2 = parsing.parse_search_query(query2)

    # With n-ary operations, both should now create the same AST structure
    # Both should be: AndNode([a, b, c])
    assert result1 == result2


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
    ), f"\nExpected: {expected_sql}\t{expected_parameters}\nObserved: {observed_sql}\t{observed_params}"


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
            "keywords:trample",
            r"(card.card_keywords @> %(p_dict_eydUcmFtcGxlJzogVHJ1ZX0)s)",
            {"p_dict_eydUcmFtcGxlJzogVHJ1ZX0": {"Trample": True}},
        ),
        # Keyword search with alias 'k'
        (
            "k:haste",
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
            "keywords>=flying",
            r"(card.card_keywords @> %(p_dict_eydGbHlpbmcnOiBUcnVlfQ)s)",
            {"p_dict_eydGbHlpbmcnOiBUcnVlfQ": {"Flying": True}},
        ),
        (
            "keywords<=haste",
            r"(card.card_keywords <@ %(p_dict_eydIYXN0ZSc6IFRydWV9)s)",
            {"p_dict_eydIYXN0ZSc6IFRydWV9": {"Haste": True}},
        ),
        (
            "keywords>trample",
            r"(card.card_keywords @> %(p_dict_eydUcmFtcGxlJzogVHJ1ZX0)s AND card.card_keywords <> %(p_dict_eydUcmFtcGxlJzogVHJ1ZX0)s)",
            {"p_dict_eydUcmFtcGxlJzogVHJ1ZX0": {"Trample": True}},
        ),
        (
            "keywords<vigilance",
            r"(card.card_keywords <@ %(p_dict_eydWaWdpbGFuY2UnOiBUcnVlfQ)s AND card.card_keywords <> %(p_dict_eydWaWdpbGFuY2UnOiBUcnVlfQ)s)",
            {"p_dict_eydWaWdpbGFuY2UnOiBUcnVlfQ": {"Vigilance": True}},
        ),
        (
            "keywords!=flying",
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
        # Oracle tag with alias 'ot'
        (
            "ot:haste",
            r"(card.card_oracle_tags @> %(p_dict_eydoYXN0ZSc6IFRydWV9)s)",
            {"p_dict_eydoYXN0ZSc6IFRydWV9": {"haste": True}},
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
