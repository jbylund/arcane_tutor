
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
    argnames=["test_input", "expected_ast"],
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
        (
            "cmc+cmc<power+toughness",
            BinaryOperatorNode(
                BinaryOperatorNode(AttributeNode("cmc"), "+", AttributeNode("cmc")),
                "<",
                BinaryOperatorNode(AttributeNode("power"), "+", AttributeNode("toughness")),
            ),
        ),
        ("cmc-power", BinaryOperatorNode(AttributeNode("cmc"), "-", AttributeNode("power"))),
        (
            "cmc+1<power",
            BinaryOperatorNode(BinaryOperatorNode(AttributeNode("cmc"), "+", NumericValueNode(1)), "<", AttributeNode("power")),
        ),
        (
            "cmc + 1 < power",
            BinaryOperatorNode(BinaryOperatorNode(AttributeNode("cmc"), "+", NumericValueNode(1)), "<", AttributeNode("power")),
        ),
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


def test_sql_generation() -> None:
    """Test that AST can be converted to SQL."""
    query = "cmc=2 AND type=creature"
    result = parsing.parse_search_query(query)

    sql = parsing.generate_sql_query(result)
    expected_sql = "((card.cmc = 2) AND (card.type = 'creature'))"
    assert sql == expected_sql


def test_name_vs_name_attribute() -> None:
    """Test that we can distinguish between the string 'name' and card
    names.
    """
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
    argnames=["operator"],
    argvalues=[["AND"], ["OR"]],
)
def test_nary_operator_associativity(operator: str) -> None:
    """Test that AND operator associativity now creates the same AST
    structure.
    """
    # These should now create the same AST structure with n-ary operations
    query1 = f"a {operator} (b {operator} c)"
    query2 = f"(a {operator} b) {operator} c"

    result1 = parsing.parse_search_query(query1)
    result2 = parsing.parse_search_query(query2)

    # With n-ary operations, both should now create the same AST structure
    # Both should be: AndNode([a, b, c])
    assert result1 == result2


@pytest.mark.parametrize(
    argnames=["input_query", "expected_sql"],
    argvalues=[
        ["cmc=3", "(card.cmc = 3)"],
        ["power=3", "(card.creature_power = 3)"],
        ["cmc=3 power=3", "((card.cmc = 3) AND (card.creature_power = 3))"],
        ["power=toughness", "(card.creature_power = card.creature_toughness)"],
        ["power>toughness", "(card.creature_power > card.creature_toughness)"],
        ["cmc=3 power=3", "((card.cmc = 3) AND (card.creature_power = 3))"],
        ["power=toughness", "(card.creature_power = card.creature_toughness)"],
        ["power>toughness", "(card.creature_power > card.creature_toughness)"],
        # Test field-specific : operator behavior
        ["name:lightning", r"(card.card_name ILIKE '%lightning%')"],
        ["name:'lightning bolt'", r"(card.card_name ILIKE '%lightning%bolt%')"],
        ["cmc:3", "(card.cmc = 3)"],  # Numeric field uses exact equality
        ["power:5", "(card.creature_power = 5)"],  # Numeric field uses exact equality
        ["card_types:creature", "(card.card_types @> '[\"creature\"]'::jsonb)"],  # JSONB array uses containment
        ["colors:red", "(card.colors @> '{\"R\": true}'::jsonb)"],  # JSONB object uses containment
        ["colors:rg", "(card.colors @> '{\"G\": true, \"R\": true}'::jsonb)"],  # JSONB object uses containment
        # test exact equality of colors
        ["colors=rg", "(card.colors = '{\"G\": true, \"R\": true}'::jsonb)"],
        # test colors greater than
        ["colors>=rg", "(card.colors @> '{\"G\": true, \"R\": true}'::jsonb)"],
        # test colors less than
        ["colors<=rg", "(card.colors <@ '{\"G\": true, \"R\": true}'::jsonb)"],
        # test colors strictly greater than
        ["colors>rg", "(card.colors @> '{\"G\": true, \"R\": true}'::jsonb AND card.colors <> '{\"G\": true, \"R\": true}'::jsonb)"],
        # test colors strictly less than
        ["colors<rg", "(card.colors <@ '{\"G\": true, \"R\": true}'::jsonb AND card.colors <> '{\"G\": true, \"R\": true}'::jsonb)"],
    ],
)
def test_full_sql_translation(input_query: str, expected_sql: str) -> None:
    parsed = parsing.parse_scryfall_query(input_query)
    observed_sql = parsed.to_sql()
    assert observed_sql == expected_sql


class TestNodes:
    def test_node_equality(self) -> None:
        assert AttributeNode("name") == AttributeNode("name")


def test_arithmetic_vs_negation_ambiguity() -> None:
    """Test that the ambiguity between arithmetic and negation is resolved
    correctly.
    """
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
