"""Query parsing functions for Scryfall search syntax."""

from __future__ import annotations

import re

from pyparsing import (
    CaselessKeyword,
    Combine,
    Forward,
    Group,
    Literal,
    Optional,
    ParserElement,
    QuotedString,
    Regex,
    Word,
    ZeroOrMore,
    alphas,
    nums,
    oneOf,
)

from .db_info import KNOWN_CARD_ATTRIBUTES, NON_NUMERIC_ATTRIBUTES, NUMERIC_ATTRIBUTES
from .nodes import (
    AndNode,
    AttributeNode,
    BinaryOperatorNode,
    NotNode,
    NumericValueNode,
    OrNode,
    Query,
    QueryNode,
    StringValueNode,
)
from .scryfall_nodes import to_scryfall_ast

# Enable pyparsing packrat caching for improved performance with increased cache size
ParserElement.enable_packrat(cache_size_limit=2**13)  # 8192 cache entries

# Constants
NEGATION_TOKEN_COUNT = 2


def balance_partial_query(query: str) -> str:
    """Balance quotes and parentheses for typeahead searches using a stack."""
    char_to_mirror = {
        "(": ")",
        "'": "'",  # single quote is own mirror
        '"': '"',  # double quote is own mirror
        ")": "(",
    }
    unbalanced_closing_chars = {")"}

    current_stack = []
    for char in query:
        mirrored_char = char_to_mirror.get(char)
        if not mirrored_char:
            continue
        if current_stack and current_stack[-1] == mirrored_char:
            current_stack.pop()
        else:
            if char in unbalanced_closing_chars:
                msg = f"Unbalanced closing character '{char}' cannot be balanced"
                raise ValueError(msg)
            current_stack.append(char)
    # add mirrored chars to the end of the query
    while current_stack:
        char = current_stack.pop()
        mirrored_char = char_to_mirror[char]
        query += mirrored_char
    return query


def flatten_nested_operations(node: QueryNode) -> QueryNode:
    """Flatten nested operations of the same type to create canonical n-ary forms.

    For example, (A AND (B AND C)) becomes (A AND B AND C).
    """
    # This function recursively flattens nested AND/OR nodes
    if isinstance(node, AndNode):
        operands: list[QueryNode] = []
        for operand in node.operands:
            if isinstance(operand, AndNode):
                flattened = flatten_nested_operations(operand)
                operands.extend(flattened.operands)
            else:
                operands.append(flatten_nested_operations(operand))
        return AndNode(operands)
    if isinstance(node, OrNode):
        operands: list[QueryNode] = []
        for operand in node.operands:
            if isinstance(operand, OrNode):
                flattened = flatten_nested_operations(operand)
                operands.extend(flattened.operands)
            else:
                operands.append(flatten_nested_operations(operand))
        return OrNode(operands)
    if isinstance(node, NotNode):
        return NotNode(flatten_nested_operations(node.operand))
    if isinstance(node, Query):
        return Query(flatten_nested_operations(node.root))
    return node


def create_value_node(value: object) -> QueryNode:
    """Create the appropriate QueryNode type for a value.

    Returns NumericValueNode, AttributeNode, or StringValueNode as
    appropriate.
    """
    # This function determines the correct node type for a value
    if isinstance(value, int | float):
        return NumericValueNode(value)
    if isinstance(value, str):
        if should_be_attribute(value):
            return AttributeNode(value)
        return StringValueNode(value)
    if isinstance(value, tuple) and value[0] == "quoted":
        return StringValueNode(value[1])
    return value  # Fallback for other types


def should_be_attribute(value: object) -> bool:
    """Check if a string value should be wrapped in AttributeNode.

    Returns True if the value is a string and is a known card attribute.
    """
    # Helper function to determine if a string should be an AttributeNode
    return isinstance(value, str) and value.lower() in KNOWN_CARD_ATTRIBUTES


def make_binary_operator_node(tokens: list[object]) -> BinaryOperatorNode:
    """Create a BinaryOperatorNode, properly wrapping attributes and values."""
    # Used as a parse action for binary operator expressions
    left, operator, right = tokens
    return BinaryOperatorNode(create_value_node(left), operator, create_value_node(right))


def make_chained_arithmetic(tokens: list[object]) -> QueryNode:
    """Create a chained arithmetic expression with left associativity.

    For example, [a, +, b, +, c] becomes ((a + b) + c)
    """
    if len(tokens) == 1:
        return create_value_node(tokens[0])

    # Start with the first term
    result = create_value_node(tokens[0])

    # Process the remaining operator-term pairs
    for i in range(1, len(tokens), 2):
        if i + 1 < len(tokens):
            operator = tokens[i]
            right_term = create_value_node(tokens[i + 1])
            result = BinaryOperatorNode(result, operator, right_term)

    return result


def parse_scryfall_query(query: str) -> Query:
    """Parse a Scryfall search query and convert to Scryfall-specific AST.

    Args:
        query: The search query string to parse.

    Returns:
        A Scryfall-specific Query AST.
    """
    generic_query = parse_search_query(query)
    return to_scryfall_ast(generic_query)


def parse_search_query(query: str) -> Query:  # noqa: C901, PLR0915
    """Parse a Scryfall search query string into an AST Query object.

    Raises ValueError if parsing fails.
    """
    if query is None or not query.strip():
        # Return empty query
        return Query(BinaryOperatorNode(AttributeNode("name"), ":", ""))

    # Pre-process the query to handle implicit AND operations
    # Convert "a b" to "a AND b" when b is not an operator
    query = preprocess_implicit_and(query)

    # Define the grammar components
    attrop = oneOf(": > < >= <= = !=")
    arithmetic_op = oneOf("+ - * /")
    integer = Word(nums).setParseAction(lambda t: int(t[0]))
    float_number = Combine(Word(nums) + Optional(Literal(".") + Optional(Word(nums)))).setParseAction(lambda t: float(t[0]))
    lparen = Literal("(").suppress()
    rparen = Literal(")").suppress()

    # Keywords must be recognized before regular words
    operator_and = CaselessKeyword("AND")
    operator_or = CaselessKeyword("OR")
    operator_not = Literal("-")

    # Handle quoted strings and regular words (but not keywords)
    def make_quoted_string(tokens: list[str]) -> tuple[str, str]:
        """Mark quoted strings so they're always treated as string values."""
        return ("quoted", tokens[0])

    quoted_string = (QuotedString('"', escChar="\\") | QuotedString("'", escChar="\\")).setParseAction(make_quoted_string)

    # Word that doesn't match keywords
    def make_word(tokens: list[str]) -> str:
        """Reject reserved keywords as words."""
        word_str = tokens[0]
        if word_str.upper() in ["AND", "OR"]:
            msg = f"'{word_str}' is a reserved keyword"
            raise ValueError(msg)
        return word_str

    word = Word(alphas + "_").setParseAction(make_word)

    # Define different types of attribute words based on their types using Regex
    # Sort by length (longest first) to avoid partial matches
    # Use case-insensitive regex patterns for attribute matching
    numeric_attr_word = Regex("|".join(sorted(NUMERIC_ATTRIBUTES, key=len, reverse=True)), flags=re.IGNORECASE)
    non_numeric_attr_word = Regex("|".join(sorted(NON_NUMERIC_ATTRIBUTES, key=len, reverse=True)), flags=re.IGNORECASE)

    # Create a literal number parser for numeric constants
    literal_number = integer | float_number

    # For attribute values, we want the raw string
    # Use Regex to match words that may contain hyphens for string values
    string_value_word = Regex(r"[a-zA-Z_][a-zA-Z0-9_-]*")

    # Build the grammar with proper precedence
    expr = Forward()

    # Define arithmetic expressions with proper precedence
    # Start with the most basic arithmetic terms
    # Only numeric attributes can be used in arithmetic expressions
    arithmetic_term = numeric_attr_word | literal_number | Group(lparen + expr + rparen)

    # Define arithmetic expressions that can be chained
    # Only match if there's at least one arithmetic operator
    arithmetic_expr = Forward()
    arithmetic_expr <<= arithmetic_term + arithmetic_op + arithmetic_term + ZeroOrMore(arithmetic_op + arithmetic_term)
    arithmetic_expr.setParseAction(make_chained_arithmetic)

    # Unified numeric comparison rule: handles all combinations of arithmetic expressions, numeric attributes, and literals
    # This consolidates the previous arithmetic_comparison and numeric_condition rules
    unified_numeric_comparison = (arithmetic_expr | numeric_attr_word | literal_number) + attrop + (arithmetic_expr | numeric_attr_word | literal_number)
    unified_numeric_comparison.setParseAction(make_binary_operator_node)

    # Attribute-to-attribute comparison should be between same types
    # Non-numeric to non-numeric (numeric comparisons are now handled by unified_numeric_comparison)
    non_numeric_attr_attr_condition = non_numeric_attr_word + attrop + non_numeric_attr_word
    non_numeric_attr_attr_condition.setParseAction(make_binary_operator_node)

    # Non-numeric attributes compared with string values
    non_numeric_condition = non_numeric_attr_word + attrop + (quoted_string | string_value_word)
    non_numeric_condition.setParseAction(make_binary_operator_node)

    condition = unified_numeric_comparison | non_numeric_condition

    # Special rule for non-numeric attribute-colon-hyphenated-value to handle cases like "otag:dual-land"
    # Only non-numeric attributes should have hyphenated string values
    hyphenated_condition = non_numeric_attr_word + Literal(":") + Regex(r"[a-zA-Z_][a-zA-Z0-9_-]+")
    hyphenated_condition.setParseAction(make_binary_operator_node)

    # Single word (implicit name search)
    def make_single_word(tokens: list[str]) -> BinaryOperatorNode:
        """For single words, always search in the name field."""
        return BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode(tokens[0]))

    single_word = word.setParseAction(make_single_word)

    # Grouped expression
    def make_group(tokens: list[object]) -> object:
        """Return the grouped expression inside parentheses."""
        return tokens[0]

    group = Group(lparen + expr + rparen).setParseAction(make_group)

    # Factor: can be negated (but not arithmetic expressions)
    def handle_negation(tokens: list[object]) -> object:
        """Handle negation (NOT) for factors, disallowing arithmetic negation.

        Args:
            tokens: List of tokens to process.

        Returns:
            The processed token(s).
        """
        if len(tokens) == 1:
            return tokens[0]
        if len(tokens) == NEGATION_TOKEN_COUNT and tokens[0] == "-":
            # Don't allow negation of arithmetic expressions
            if isinstance(tokens[1], BinaryOperatorNode) and tokens[1].operator in ["+", "-", "*", "/"]:
                msg = "Cannot negate arithmetic expressions"
                raise ValueError(msg)
            return NotNode(tokens[1])
        return tokens[0]

    # For negation, we exclude arithmetic expressions from being negated
    # Test: revert to original order to confirm this breaks it
    negatable_primary = non_numeric_attr_attr_condition | condition | group | single_word
    negatable_factor = Optional(operator_not) + negatable_primary
    negatable_factor.setParseAction(handle_negation)

    # Factor includes both negatable expressions and arithmetic expressions
    # SPECIAL: hyphenated_condition first to handle "otag:dual-land", then condition (includes comparisons) before standalone arithmetic
    # Note: arithmetic_comparison is now consolidated into unified_numeric_comparison within condition
    factor = hyphenated_condition | condition | arithmetic_expr | negatable_factor

    # Expression with explicit AND/OR operators (highest precedence)
    def handle_operators(tokens: list[object]) -> object:
        """Handle AND/OR operators, grouping operands by operator type and building n-ary nodes.

        Args:
            tokens: List of tokens to process.

        Returns:
            The processed token(s).
        """
        if len(tokens) == 1:
            return tokens[0]
        # Group operands by operator type
        current_operands = [tokens[0]]
        current_operator = None
        for i in range(1, len(tokens), 2):
            if i + 1 < len(tokens):
                operator = tokens[i]
                right = tokens[i + 1]
                if current_operator is None:
                    # First operator, start collecting
                    current_operator = operator.upper()
                    current_operands.append(right)
                elif operator.upper() == current_operator:
                    # Same operator, add to current group
                    current_operands.append(right)
                else:
                    # Different operator, create node for current group and start new group
                    if current_operator == "AND":
                        result = AndNode(current_operands)
                    elif current_operator == "OR":
                        result = OrNode(current_operands)
                    else:
                        msg = f"Unknown operator: {current_operator}"
                        raise ValueError(msg)
                    # Start new group with the result as first operand
                    current_operands = [result, right]
                    current_operator = operator.upper()
        # Create final node for remaining operands
        if current_operator == "AND":
            return AndNode(current_operands)
        if current_operator == "OR":
            return OrNode(current_operands)
        # No operators, just return the single operand
        return current_operands[0]

    # The main expression: factors separated by AND/OR operators
    expr <<= factor + ZeroOrMore((operator_and | operator_or) + factor)
    expr.setParseAction(handle_operators)

    # Parse the query
    try:
        parsed = expr.parseString(query)
        if parsed:
            # Flatten nested operations to create canonical n-ary forms
            return flatten_nested_operations(Query(parsed[0]))
        return Query(BinaryOperatorNode("name", ":", ""))
    except (ValueError, TypeError, IndexError) as e:
        msg = f"Failed to parse query '{query}': {e}"
        raise ValueError(msg) from e


def preprocess_implicit_and(query: str) -> str:  # noqa: C901, PLR0915, PLR0912
    """Pre-process query to convert implicit AND operations to explicit ones.

    For example, 'foo bar' becomes 'foo AND bar'.
    """
    # Split the query into tokens while preserving quoted strings and operators
    tokens: list[str] = []
    i = 0
    while i < len(query):
        if len(tokens) > len(query):
            msg = f"tokens is longer than query, {tokens} vs {query}"
            raise AssertionError(msg)
        char = query[i]
        if char in ['"', "'"]:
            # Handle quoted string (both double and single quotes)
            quote_char = char
            end_quote = query.find(quote_char, i + 1)
            if end_quote == -1:
                msg = f"Unmatched {quote_char} quote in query"
                raise ValueError(msg)
            tokens.append(query[i : end_quote + 1])
            i = end_quote + 1
        elif char in "()":
            # Handle parentheses
            tokens.append(char)
            i += 1
        elif char.isspace():
            # Skip whitespace
            i += 1
        elif char in "><=!+-*/":
            # Handle operators including arithmetic operators
            if i + 1 < len(query) and query[i : i + 2] in [">=", "<=", "!="]:
                tokens.append(query[i : i + 2])
                i += 2
            else:
                tokens.append(char)
                i += 1
        elif char == ":":
            # Handle colon operator
            tokens.append(char)
            i += 1
        elif char == "-":
            # Check if this hyphen is part of an attribute value
            in_attr_value_context = tokens and tokens[-1] == ":"
            if in_attr_value_context:
                # In attribute value context, treat hyphen as part of the word
                word_end = i
                # Go back to find the start of this word (should be right after the colon)
                # Continue reading the full hyphenated word
                while word_end < len(query) and (query[word_end].isalnum() or query[word_end] in "_-"):
                    word_end += 1
                tokens.append(query[i:word_end])
                i = word_end
            else:
                # Handle negation as a separate token
                tokens.append(char)
                i += 1
        else:
            # Handle words (alphanumeric starting)
            word_end = i
            # Check if we're in an attribute value context (previous token was a colon)
            in_attr_value_context = tokens and tokens[-1] == ":"

            if in_attr_value_context:
                # In attribute value context, allow alphanumeric, underscore, and hyphens
                while word_end < len(query) and (query[word_end].isalnum() or query[word_end] in "_-"):
                    word_end += 1
            else:
                # Regular word context, only alphanumeric and underscore
                while word_end < len(query) and (query[word_end].isalnum() or query[word_end] == "_"):
                    word_end += 1

            tokens.append(query[i:word_end])
            i = word_end
    # Convert implicit AND operations
    result: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        result.append(token)
        # Check if we need to insert an implicit AND
        if i + 1 < len(tokens):
            next_token = tokens[i + 1]
            # Insert AND if:
            # 1. Current token is not an operator
            # 2. Next token is not an operator (but allow negation)
            # 3. Current token is not a left parenthesis
            # 4. Next token is not a right parenthesis
            # 5. Current token is not AND/OR
            # 6. Next token is not AND/OR
            if (
                not is_operator(token)
                and not is_operator(next_token)
                and token != "("
                and next_token != ")"
                and token.upper() not in ["AND", "OR"]
                and next_token.upper() not in ["AND", "OR"]
            ):
                result.append("AND")
            # Special case: if current token is not an operator and next token is negation,
            # we need to insert AND to separate them as factors
            # BUT: if the next token after the negation is a word, it might be arithmetic
            elif not is_operator(token) and next_token == "-" and token.upper() not in ["AND", "OR", "("]:
                # Check if this looks like arithmetic: word - word
                if i + 2 < len(tokens) and not is_operator(tokens[i + 2]) and tokens[i + 2] not in ["AND", "OR"]:
                    # Only treat as arithmetic if both sides are known card attributes
                    if token in KNOWN_CARD_ATTRIBUTES and tokens[i + 2] in KNOWN_CARD_ATTRIBUTES:
                        # This looks like arithmetic: attribute - attribute, so don't insert AND
                        # The - will be treated as an arithmetic operator
                        pass
                    else:
                        # This looks like negation: word - word (but not attributes), so insert AND
                        result.append("AND")
                else:
                    # This looks like negation: word - (something else), so insert AND
                    result.append("AND")
        i += 1
    return " ".join(result)


def is_operator(token: str) -> bool:
    """Check if a token is an operator (comparison, arithmetic, or negation).

    Args:
        token: The token to check.

    Returns:
        True if the token is an operator, False otherwise.
    """
    return token in [":", ">", "<", ">=", "<=", "=", "!=", "-", "+", "*", "/"]


def generate_sql_query(parsed_query: Query) -> tuple[str, dict]:
    """Generate a SQL WHERE clause string from a parsed Query AST."""
    scryfall_ast = to_scryfall_ast(parsed_query)
    query_context = {}
    return scryfall_ast.to_sql(query_context), query_context
