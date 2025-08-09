import pytest

from api import parsing


@pytest.mark.parametrize(
    "test_input, expected_ast",
    [
        ("a", parsing.Condition("name", ":", "a")),
        ("a b", parsing.AndNode([
            parsing.Condition("name", ":", "a"),
            parsing.Condition("name", ":", "b")
        ])),
        ("a -b", parsing.AndNode([
            parsing.Condition("name", ":", "a"),
            parsing.NotNode(parsing.Condition("name", ":", "b"))
        ])),
        ("a:b", parsing.Condition("a", ":", "b")),
        ("a:b c:d", parsing.AndNode([
            parsing.Condition("a", ":", "b"),
            parsing.Condition("c", ":", "d")
        ])),
        ("a:b and c:d", parsing.AndNode([
            parsing.Condition("a", ":", "b"),
            parsing.Condition("c", ":", "d")
        ])),
        ("a:b or c:d", parsing.OrNode([
            parsing.Condition("a", ":", "b"),
            parsing.Condition("c", ":", "d")
        ])),
        ("a b:c", parsing.AndNode([
            parsing.Condition("name", ":", "a"),
            parsing.Condition("b", ":", "c")
        ])),
        # other operators
        ("a=b", parsing.Condition("a", "=", "b")),
        ("cmc=3", parsing.Condition("cmc", "=", 3)),
        ("a!=b", parsing.Condition("a", "!=", "b")),
        # more parens
        ("a:b and (c:d or e:f)", parsing.AndNode([
            parsing.Condition("a", ":", "b"),
            parsing.OrNode([
                parsing.Condition("c", ":", "d"),
                parsing.Condition("e", ":", "f")
            ])
        ])),
    ],
)
def test_parse_basic_structure(test_input, expected_ast):
    """Test that queries parse into the expected AST structure"""
    observed = parsing.parse_search_query(test_input)
    assert observed is not None
    assert hasattr(observed, 'root')
    
    # Compare the full AST structure
    assert observed.root == expected_ast, f"Expected {expected_ast}, got {observed.root}"


def test_parse_simple_condition():
    """Test parsing a simple condition"""
    query = "cmc:2"
    result = parsing.parse_search_query(query)
    
    assert isinstance(result, parsing.Query)
    assert isinstance(result.root, parsing.Condition)
    assert result.root.attribute == "cmc"
    assert result.root.operator == ":"
    assert result.root.value == 2


def test_parse_and_operation():
    """Test parsing AND operations"""
    query = "a AND b"
    result = parsing.parse_search_query(query)
    
    assert isinstance(result, parsing.Query)
    assert isinstance(result.root, parsing.AndNode)
    assert len(result.root.operands) == 2
    assert isinstance(result.root.operands[0], parsing.Condition)
    assert isinstance(result.root.operands[1], parsing.Condition)


def test_parse_or_operation():
    """Test parsing OR operations"""
    query = "a OR b"
    result = parsing.parse_search_query(query)
    
    assert isinstance(result, parsing.Query)
    assert isinstance(result.root, parsing.OrNode)
    assert len(result.root.operands) == 2
    assert isinstance(result.root.operands[0], parsing.Condition)
    assert isinstance(result.root.operands[1], parsing.Condition)


def test_parse_implicit_and():
    """Test parsing implicit AND operations"""
    query = "a b"
    result = parsing.parse_search_query(query)
    
    assert isinstance(result, parsing.Query)
    assert isinstance(result.root, parsing.AndNode)
    assert len(result.root.operands) == 2
    assert isinstance(result.root.operands[0], parsing.Condition)
    assert isinstance(result.root.operands[1], parsing.Condition)


def test_parse_complex_nested():
    """Test parsing complex nested queries"""
    query = "cmc:2 AND (oracle:flying OR oracle:haste)"
    result = parsing.parse_search_query(query)
    
    assert isinstance(result, parsing.Query)
    assert isinstance(result.root, parsing.AndNode)
    assert len(result.root.operands) == 2
    # The right side should be an OR operation
    assert isinstance(result.root.operands[1], parsing.OrNode)


def test_parse_quoted_strings():
    """Test parsing quoted strings"""
    query = 'name:"Lightning Bolt"'
    result = parsing.parse_search_query(query)
    
    assert isinstance(result, parsing.Query)
    assert isinstance(result.root, parsing.Condition)
    assert result.root.attribute == "name"
    assert result.root.operator == ":"
    assert result.root.value == "Lightning Bolt"


def test_parse_different_operators():
    """Test parsing different comparison operators"""
    operators = [">", "<", ">=", "<=", "=", "!="]
    
    for op in operators:
        query = f"cmc{op}3"
        result = parsing.parse_search_query(query)
        
        assert isinstance(result, parsing.Query)
        assert isinstance(result.root, parsing.Condition)
        assert result.root.attribute == "cmc"
        assert result.root.operator == op
        assert result.root.value == 3


def test_parse_empty_query():
    """Test parsing empty or None queries"""
    # Empty string
    result = parsing.parse_search_query("")
    assert isinstance(result, parsing.Query)
    
    # None
    result = parsing.parse_search_query(None)
    assert isinstance(result, parsing.Query)


def test_sql_generation():
    """Test that AST can be converted to SQL"""
    query = "cmc:2 AND type:creature"
    result = parsing.parse_search_query(query)
    
    sql = parsing.generate_sql_query(result)
    assert isinstance(sql, str)
    assert "cmc = 2" in sql
    assert "type_line LIKE" in sql
    assert "AND" in sql


def test_name_vs_name_attribute():
    """Test that we can distinguish between the string 'name' and card names"""
    # This should create a Condition for "name" (searching for cards with "name" in their name)
    query1 = "name"
    result1 = parsing.parse_search_query(query1)
    assert isinstance(result1.root, parsing.Condition)
    assert result1.root.attribute == "name"
    assert result1.root.operator == ":"
    assert result1.root.value == "name"
    
    # This should create a Condition for name:value (same as bare word "value")
    query2 = "name:value"
    result2 = parsing.parse_search_query(query2)
    assert isinstance(result2.root, parsing.Condition)
    assert result2.root.attribute == "name"
    assert result2.root.operator == ":"
    assert result2.root.value == "value"
    
    # This should create a Condition for cmc operations
    query3 = "cmc:3"
    result3 = parsing.parse_search_query(query3)
    assert isinstance(result3.root, parsing.Condition)
    assert result3.root.attribute == "cmc"
    assert result3.root.operator == ":"
    assert result3.root.value == 3
    
    # This should create a Condition for other attributes
    query4 = "oracle:flying"
    result4 = parsing.parse_search_query(query4)
    assert isinstance(result4.root, parsing.Condition)
    assert result4.root.attribute == "oracle"
    assert result4.root.value == "flying"


def test_and_operator_associativity():
    """Test that AND operator associativity now creates the same AST structure"""
    # These should now create the same AST structure with n-ary operations
    query1 = "a AND (b AND c)"
    query2 = "(a AND b) AND c"
    
    result1 = parsing.parse_search_query(query1)
    result2 = parsing.parse_search_query(query2)
    
    # With n-ary operations, both should now create the same AST structure
    # Both should be: AndNode([a, b, c])
    assert result1.root == result2.root, "These should now be identical with n-ary operations"
    
    # Verify the structure: AndNode([a, b, c])
    assert isinstance(result1.root, parsing.AndNode)
    assert len(result1.root.operands) == 3
    assert isinstance(result1.root.operands[0], parsing.Condition)
    assert isinstance(result1.root.operands[1], parsing.Condition)
    assert isinstance(result1.root.operands[2], parsing.Condition)
    
    # Both should generate identical SQL
    sql1 = parsing.generate_sql_query(result1)
    sql2 = parsing.generate_sql_query(result2)
    
    assert sql1 == sql2, "SQL should be identical"
    print(f"SQL: {sql1}")


def test_or_operator_associativity():
    """Test that OR operator associativity now creates the same AST structure"""
    # These should now create the same AST structure with n-ary operations
    query1 = "a OR (b OR c)"
    query2 = "(a OR b) OR c"
    
    result1 = parsing.parse_search_query(query1)
    result2 = parsing.parse_search_query(query2)
    
    # With n-ary operations, both should now create the same AST structure
    # Both should be: OrNode([a, b, c])
    assert result1.root == result2.root, "These should now be identical with n-ary operations"
    
    # Verify the structure: OrNode([a, b, c])
    assert isinstance(result1.root, parsing.OrNode)
    assert len(result1.root.operands) == 3
    assert isinstance(result1.root.operands[0], parsing.Condition)
    assert isinstance(result1.root.operands[1], parsing.Condition)
    assert isinstance(result1.root.operands[2], parsing.Condition)
    
    # Both should generate identical SQL
    sql1 = parsing.generate_sql_query(result1)
    sql2 = parsing.generate_sql_query(result2)
    
    assert sql1 == sql2, "SQL should be identical"
    print(f"SQL: {sql2}")
