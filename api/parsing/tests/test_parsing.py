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
from api.parsing.parsing_f import generate_sql_query


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
        # Test cases for pricing attributes
        ("usd>10", BinaryOperatorNode(AttributeNode("usd"), ">", NumericValueNode(10))),
        ("eur<=5", BinaryOperatorNode(AttributeNode("eur"), "<=", NumericValueNode(5))),
        ("tix<1", BinaryOperatorNode(AttributeNode("tix"), "<", NumericValueNode(1))),
        ("usd=2.5", BinaryOperatorNode(AttributeNode("usd"), "=", NumericValueNode(2.5))),
        ("eur!=10", BinaryOperatorNode(AttributeNode("eur"), "!=", NumericValueNode(10))),
        ("tix>=0.5", BinaryOperatorNode(AttributeNode("tix"), ">=", NumericValueNode(0.5))),
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


def test_parse_set_searches() -> None:
    """Test parsing set search queries."""
    # Test full 'set:' syntax
    query = "set:iko"
    result = parsing.parse_search_query(query)
    expected = BinaryOperatorNode(AttributeNode("set"), ":", StringValueNode("iko"))
    assert result.root == expected

    # Test 's:' shorthand
    query = "s:thb"
    result = parsing.parse_search_query(query)
    expected = BinaryOperatorNode(AttributeNode("s"), ":", StringValueNode("thb"))
    assert result.root == expected

    # Test case insensitivity
    query = "SET:m21"
    result = parsing.parse_search_query(query)
    expected = BinaryOperatorNode(AttributeNode("SET"), ":", StringValueNode("m21"))
    assert result.root == expected


def test_parse_different_operators() -> None:
    """Test parsing different comparison operators."""
    operators = [">", "<", ">=", "<=", "=", "!="]

    for op in operators:
        query = f"cmc{op}3"
        result = parsing.parse_search_query(query)
        expected = BinaryOperatorNode(AttributeNode("cmc"), op, NumericValueNode(3))
        assert result.root == expected


def _generate_pricing_operator_test_cases() -> list[tuple[str, BinaryOperatorNode]]:
    """Generate test cases for pricing operators."""
    operators = [">", "<", ">=", "<=", "=", "!="]
    pricing_attrs = ["usd", "eur", "tix"]

    test_cases = []
    for attr in pricing_attrs:
        for op in operators:
            query = f"{attr}{op}5"
            expected = BinaryOperatorNode(AttributeNode(attr), op, NumericValueNode(5))
            test_cases.append((query, expected))

    return test_cases


@pytest.mark.parametrize(
    argnames=("test_input", "expected_ast"),
    argvalues=_generate_pricing_operator_test_cases(),
)
def test_parse_pricing_operators(test_input: str, expected_ast: BinaryOperatorNode) -> None:
    """Test parsing different comparison operators with pricing attributes."""
    result = parsing.parse_search_query(test_input)
    assert result.root == expected_ast, f"Failed for {test_input}"


def test_parse_combined_pricing_queries() -> None:
    """Test parsing combined queries with pricing attributes."""
    # Test combining pricing with other attributes
    query1 = "cmc<=3 usd<5"
    result1 = parsing.parse_search_query(query1)
    expected1 = AndNode([
        BinaryOperatorNode(AttributeNode("cmc"), "<=", NumericValueNode(3)),
        BinaryOperatorNode(AttributeNode("usd"), "<", NumericValueNode(5)),
    ])
    assert result1.root == expected1

    # Test combining multiple pricing attributes
    query2 = "usd>10 OR eur<5"
    result2 = parsing.parse_search_query(query2)
    expected2 = OrNode([
        BinaryOperatorNode(AttributeNode("usd"), ">", NumericValueNode(10)),
        BinaryOperatorNode(AttributeNode("eur"), "<", NumericValueNode(5)),
    ])
    assert result2.root == expected2


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
    argnames=("input_query", "expected_ast"),
    argvalues=[
        ("number:123", BinaryOperatorNode(AttributeNode("number"), ":", StringValueNode("123"))),
        ("cn:456", BinaryOperatorNode(AttributeNode("cn"), ":", StringValueNode("456"))),
        ("number:123a", BinaryOperatorNode(AttributeNode("number"), ":", StringValueNode("123a"))),
        ("cn:123-b", BinaryOperatorNode(AttributeNode("cn"), ":", StringValueNode("123-b"))),
        ("NUMBER:123", BinaryOperatorNode(AttributeNode("NUMBER"), ":", StringValueNode("123"))),
        ("CN:456", BinaryOperatorNode(AttributeNode("CN"), ":", StringValueNode("456"))),
    ],
)
def test_parse_collector_number_searches(input_query: str, expected_ast: QueryNode) -> None:
    """Test parsing collector number search queries."""
    result = parsing.parse_search_query(input_query)
    assert result.root == expected_ast


@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql", "expected_parameters"),
    argvalues=[
        # Basic collector number search with full 'number:' syntax
        (
            "number:123",
            r"(card.collector_number = %(p_str_MTIz)s)",
            {"p_str_MTIz": "123"},
        ),
        # Collector number search with 'cn:' shorthand
        (
            "cn:123",
            r"(card.collector_number = %(p_str_MTIz)s)",
            {"p_str_MTIz": "123"},
        ),
        # Collector number with letters (like "123a")
        (
            "number:123a",
            r"(card.collector_number = %(p_str_MTIzYQ)s)",
            {"p_str_MTIzYQ": "123a"},
        ),
        # Collector number with hyphen
        (
            "cn:123-b",
            r"(card.collector_number = %(p_str_MTIzLWI)s)",
            {"p_str_MTIzLWI": "123-b"},
        ),
        # Case-insensitive collector number attribute search
        (
            "NUMBER:123",
            r"(card.collector_number = %(p_str_MTIz)s)",
            {"p_str_MTIz": "123"},
        ),
        (
            "CN:456",
            r"(card.collector_number = %(p_str_NDU2)s)",
            {"p_str_NDU2": "456"},
        ),
    ],
)
def test_collector_number_sql_translation(input_query: str, expected_sql: str, expected_parameters: dict) -> None:
    """Test that collector number search generates correct SQL with exact matching like set codes."""
    parsed = parsing.parse_scryfall_query(input_query)
    context = {}
    observed_sql = parsed.to_sql(context)
    assert observed_sql == expected_sql, f"\nExpected: {expected_sql}\nObserved: {observed_sql}"
    assert context == expected_parameters, f"\nExpected params: {expected_parameters}\nObserved params: {context}"


def test_parse_combined_collector_number_queries() -> None:
    """Test parsing combined queries with collector number attributes."""
    # Test combining collector number with other attributes
    query1 = "set:iko number:123"
    result1 = parsing.parse_search_query(query1)
    expected1 = AndNode([
        BinaryOperatorNode(AttributeNode("set"), ":", StringValueNode("iko")),
        BinaryOperatorNode(AttributeNode("number"), ":", StringValueNode("123")),
    ])
    assert result1.root == expected1

    # Test combining collector number with multiple attributes
    query2 = "cmc<=3 OR cn:456"
    result2 = parsing.parse_search_query(query2)
    expected2 = OrNode([
        BinaryOperatorNode(AttributeNode("cmc"), "<=", NumericValueNode(3)),
        BinaryOperatorNode(AttributeNode("cn"), ":", StringValueNode("456")),
    ])
    assert result2.root == expected2
