import pytest


def balance_query(query):
    """Balance quotes and parentheses for typeahead searches."""
    balanced = query
    
    # Count unmatched quotes (both single and double)
    double_quote_count = 0
    single_quote_count = 0
    paren_count = 0
    
    for char in balanced:
        if char == '"':
            double_quote_count += 1
        elif char == "'":
            single_quote_count += 1
        elif char == '(':
            paren_count += 1
        elif char == ')':
            paren_count -= 1
    
    # Close unmatched double quotes
    if double_quote_count % 2 == 1:
        balanced += '"'
    
    # Close unmatched single quotes
    if single_quote_count % 2 == 1:
        balanced += "'"
    
    # Close unmatched parentheses
    while paren_count > 0:
        balanced += ')'
        paren_count -= 1
    
    return balanced


@pytest.mark.parametrize(
    argnames=("input_query", "expected_balanced"),
    argvalues=[
        # Quote balancing tests
        ('name:"hydr', 'name:"hydr"'),
        ("name:'hydr", "name:'hydr'"),
        ('name:"hydra"', 'name:"hydra"'),  # already balanced
        ("name:'hydra'", "name:'hydra'"),  # already balanced
        ('name:"hydr" power:3', 'name:"hydr" power:3'),  # mixed balanced
        ('name:"hydr power:3', 'name:"hydr power:3"'),  # quote spans multiple terms
        
        # Parentheses balancing tests
        ('(t:goblin c:r) or (t:beast c:g', '(t:goblin c:r) or (t:beast c:g)'),
        ('(t:goblin c:r) or (t:beast c:g)', '(t:goblin c:r) or (t:beast c:g)'),  # already balanced
        ('((cmc=3) and (power>2', '((cmc=3) and (power>2))'),
        ('(((nested', '(((nested)))'),
        
        # Combined quote and parentheses tests
        ('(name:"lightning', '(name:"lightning")'),
        ('(name:"lightning bolt" and (power>2', '(name:"lightning bolt" and (power>2))'),
        
        # Edge cases
        ('', ''),
        ('simple', 'simple'),
        ('name:hydra', 'name:hydra'),
    ],
)
def test_balance_query(input_query: str, expected_balanced: str) -> None:
    """Test that the balanceQuery function correctly balances quotes and parentheses."""
    result = balance_query(input_query)
    assert result == expected_balanced, f"Input: '{input_query}' -> Expected: '{expected_balanced}', Got: '{result}'"


def test_balance_query_integration_with_parsing() -> None:
    """Test that balanced queries can be successfully parsed."""
    from api.parsing import parse_scryfall_query
    
    # Test cases that would fail without balancing but succeed with balancing
    unbalanced_queries = [
        'name:"hydr',
        '(name:"lightning',
    ]
    
    for original_query in unbalanced_queries:
        balanced_query = balance_query(original_query)
        
        # Original should fail (at least for quote cases)
        if '"' in original_query and original_query.count('"') % 2 == 1:
            with pytest.raises(ValueError, match="Unmatched"):
                parse_scryfall_query(original_query)
        
        # Balanced should succeed
        try:
            result = parse_scryfall_query(balanced_query)
            assert result is not None, f"Failed to parse balanced query: {balanced_query}"
        except Exception as e:
            pytest.fail(f"Balanced query '{balanced_query}' should parse successfully, but got: {e}")