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
from api.parsing.scryfall_nodes import calculate_cmc, mana_cost_str_to_dict


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

    # These should parse successfully (SQL generation would be tested in test_sql_gen.py)
    assert parsed_query is not None


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


@pytest.mark.parametrize(
    argnames=("test_input", "expected_ast"),
    argvalues=[
        ("mana:{1}{G}", BinaryOperatorNode(AttributeNode("mana"), ":", parsing.ManaValueNode("{1}{G}"))),
        ("m:{2}{R}{G}", BinaryOperatorNode(AttributeNode("m"), ":", parsing.ManaValueNode("{2}{R}{G}"))),
        ("mana:{W/U}", BinaryOperatorNode(AttributeNode("mana"), ":", parsing.ManaValueNode("{W/U}"))),
        ("m:{X}{X}{W}", BinaryOperatorNode(AttributeNode("m"), ":", parsing.ManaValueNode("{X}{X}{W}"))),
        ("mana:{0}", BinaryOperatorNode(AttributeNode("mana"), ":", parsing.ManaValueNode("{0}"))),
        ("m:{15}", BinaryOperatorNode(AttributeNode("m"), ":", parsing.ManaValueNode("{15}"))),
    ],
)
def test_parse_mana_cost_searches(test_input: str, expected_ast: BinaryOperatorNode) -> None:
    """Test parsing mana cost searches with full curly-brace notation."""
    observed = parsing.parse_search_query(test_input)
    assert observed.root == expected_ast


@pytest.mark.parametrize(
    argnames=("test_input", "expected_ast"),
    argvalues=[
        ("mana=1{G}", BinaryOperatorNode(AttributeNode("mana"), "=", parsing.ManaValueNode("1{G}"))),
        ("m:2{R}{G}", BinaryOperatorNode(AttributeNode("m"), ":", parsing.ManaValueNode("2{R}{G}"))),
        ("mana=W{U/R}", BinaryOperatorNode(AttributeNode("mana"), "=", parsing.ManaValueNode("W{U/R}"))),
        ("m:{2/W}G", BinaryOperatorNode(AttributeNode("m"), ":", parsing.ManaValueNode("{2/W}G"))),
        ("mana:1WU", BinaryOperatorNode(AttributeNode("mana"), ":", parsing.ManaValueNode("1WU"))),
        ("m=2RRG", BinaryOperatorNode(AttributeNode("m"), "=", parsing.ManaValueNode("2RRG"))),
        ("mana:WU", BinaryOperatorNode(AttributeNode("mana"), ":", parsing.ManaValueNode("WU"))),
    ],
)
def test_parse_mixed_mana_notation(test_input: str, expected_ast: BinaryOperatorNode) -> None:
    """Test parsing mana cost searches with mixed notation (per Scryfall rules)."""
    observed = parsing.parse_search_query(test_input)
    assert observed.root == expected_ast


def test_parse_combined_mana_queries() -> None:
    """Test parsing combined queries with mana cost searches."""
    # Test combining mana with other attributes
    query1 = "cmc<=3 mana:{1}{G}"
    result1 = parsing.parse_search_query(query1)
    expected1 = parsing.AndNode([
        BinaryOperatorNode(AttributeNode("cmc"), "<=", NumericValueNode(3)),
        BinaryOperatorNode(AttributeNode("mana"), ":", parsing.ManaValueNode("{1}{G}")),
    ])
    assert result1.root == expected1

    # Test combining multiple mana attributes
    query2 = "mana:{W} OR m:{U}"
    result2 = parsing.parse_search_query(query2)
    expected2 = parsing.OrNode([
        BinaryOperatorNode(AttributeNode("mana"), ":", parsing.ManaValueNode("{W}")),
        BinaryOperatorNode(AttributeNode("m"), ":", parsing.ManaValueNode("{U}")),
    ])
    assert result2.root == expected2


def test_mana_cost_with_comparison_operators() -> None:
    """Test that mana cost searches work with different operators."""
    # Test colon operator (most common)
    query1 = "mana:{1}{G}"
    result1 = parsing.parse_search_query(query1)
    expected1 = BinaryOperatorNode(AttributeNode("mana"), ":", parsing.ManaValueNode("{1}{G}"))
    assert result1.root == expected1

    # Test equals operator
    query2 = "m={W}{U}"
    result2 = parsing.parse_search_query(query2)
    expected2 = BinaryOperatorNode(AttributeNode("m"), "=", parsing.ManaValueNode("{W}{U}"))
    assert result2.root == expected2


def test_mana_cost_approximate_comparisons() -> None:
    """Test mana cost approximate comparisons with <, <=, >, >= operators."""
    # Test <= operator - use regular parser for AST structure validation
    query1 = "mana<={2}{R}{R}"
    result1 = parsing.parse_search_query(query1)
    expected1 = BinaryOperatorNode(AttributeNode("mana"), "<=", parsing.ManaValueNode("{2}{R}{R}"))
    assert result1.root == expected1

    # Test < operator
    query2 = "m<{1}{G}"
    result2 = parsing.parse_search_query(query2)
    expected2 = BinaryOperatorNode(AttributeNode("m"), "<", parsing.ManaValueNode("{1}{G}"))
    assert result2.root == expected2

    # Test >= operator (parsing test)
    query3 = "mana>={W}{U}"
    result3 = parsing.parse_search_query(query3)
    expected3 = BinaryOperatorNode(AttributeNode("mana"), ">=", parsing.ManaValueNode("{W}{U}"))
    assert result3.root == expected3

    # Test > operator
    query4 = "m>{0}"
    result4 = parsing.parse_search_query(query4)
    expected4 = BinaryOperatorNode(AttributeNode("m"), ">", parsing.ManaValueNode("{0}"))
    assert result4.root == expected4


def test_mana_cost_sql_generation() -> None:
    """Test SQL generation for mana cost comparisons."""
    # Test basic equality (colon operator) - use scryfall parser for proper node types
    result1 = parsing.parse_scryfall_query("mana:{1}{G}")
    context1 = {}
    sql1 = result1.to_sql(context1)
    assert "(card.mana_cost_text =" in sql1
    assert "{1}{G}" in context1.values()

    # Test <= operator generates containment + cmc check
    result2 = parsing.parse_scryfall_query("mana<={2}{R}{R}")
    context2 = {}
    sql2 = result2.to_sql(context2)
    assert "card.mana_cost_jsonb <@" in sql2
    assert "card.cmc <=" in sql2
    assert {"R": [1, 2]} in context2.values()
    assert 4 in context2.values()  # CMC of {2}{R}{R}

    # Test < operator includes inequality check
    result3 = parsing.parse_scryfall_query("mana<{1}{G}")
    context3 = {}
    sql3 = result3.to_sql(context3)
    assert "card.mana_cost_jsonb <@" in sql3
    assert "card.cmc <=" in sql3
    assert "card.mana_cost_jsonb <>" in sql3

    # Test >= operator reverses containment direction
    result4 = parsing.parse_scryfall_query("mana>={W}{U}")
    context4 = {}
    sql4 = result4.to_sql(context4)
    assert "<@ card.mana_cost_jsonb" in sql4
    assert "card.cmc >=" in sql4

    # Test > operator includes inequality
    result5 = parsing.parse_scryfall_query("mana>{0}")
    context5 = {}
    sql5 = result5.to_sql(context5)
    assert "<@ card.mana_cost_jsonb" in sql5
    assert "card.cmc >=" in sql5
    assert "card.mana_cost_jsonb <>" in sql5


def test_mana_cost_cmc_calculation() -> None:
    """Test CMC calculation for various mana costs."""
    # Test basic costs
    assert calculate_cmc("{1}{G}") == 2
    assert calculate_cmc("{2}{R}{R}") == 4
    assert calculate_cmc("{W}{U}") == 2
    assert calculate_cmc("{0}") == 0
    assert calculate_cmc("{15}") == 15

    # Test hybrid costs (each counts as 1)
    assert calculate_cmc("{W/U}") == 1
    assert calculate_cmc("{2/W}") == 1
    assert calculate_cmc("{W/U/P}") == 1

    # Test X costs (X counts as 0 for CMC calculation)
    assert calculate_cmc("{X}{X}{W}") == 1


def test_mana_cost_dict_conversion() -> None:
    """Test mana cost to dict conversion."""
    # Test basic conversions
    assert mana_cost_str_to_dict("{1}{G}") == {"G": [1]}
    assert mana_cost_str_to_dict("{2}{R}{R}") == {"R": [1, 2]}
    assert mana_cost_str_to_dict("{W}{U}") == {"W": [1], "U": [1]}
    assert mana_cost_str_to_dict("{0}") == {}

    # Test complex symbols (they should still count as single symbols)
    assert mana_cost_str_to_dict("{W/U}") == {"W/U": [1]}
    assert mana_cost_str_to_dict("{2/W}") == {"2/W": [1]}
    assert mana_cost_str_to_dict("{X}{X}{W}") == {"X": [1, 2], "W": [1]}
