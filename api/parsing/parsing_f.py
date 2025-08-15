from pyparsing import (
    CaselessKeyword,
    Combine,
    Forward,
    Group,
    Literal,
    Optional,
    QuotedString,
    Word,
    ZeroOrMore,
    alphas,
    nums,
    oneOf,
)

from .nodes import AndNode, AttributeNode, BinaryOperatorNode, NotNode, NumericValueNode, OrNode, Query, QueryNode, StringValueNode

# Known card attributes that should be wrapped in AttributeNode
KNOWN_CARD_ATTRIBUTES = {
    "cmc",
    "power",
    "toughness",
    "name",
    "type",
    "oracle",
    "mana_cost",
    "card_types",
    "card_subtypes",
    "card_colors",
    "creature_power",
    "creature_toughness",
    "mana_cost_text",
}


def flatten_nested_operations(node: QueryNode) -> QueryNode:
    """Flatten nested operations of the same type to create canonical n-ary forms"""
    if isinstance(node, AndNode):
        # Collect all operands, recursively flattening nested AND operations
        operands = []
        for operand in node.operands:
            if isinstance(operand, AndNode):
                # Recursively flatten nested AND operations
                flattened = flatten_nested_operations(operand)
                operands.extend(flattened.operands)
            else:
                # Recursively flatten other types of nodes
                operands.append(flatten_nested_operations(operand))
        return AndNode(operands)

    elif isinstance(node, OrNode):
        # Collect all operands, recursively flattening nested OR operations
        operands = []
        for operand in node.operands:
            if isinstance(operand, OrNode):
                # Recursively flatten nested OR operations
                flattened = flatten_nested_operations(operand)
                operands.extend(flattened.operands)
            else:
                # Recursively flatten other types of nodes
                operands.append(flatten_nested_operations(operand))
        return OrNode(operands)

    elif isinstance(node, NotNode):
        # Recursively flatten the operand
        return NotNode(flatten_nested_operations(node.operand))

    elif isinstance(node, Query):
        # Recursively flatten the root
        return Query(flatten_nested_operations(node.root))

    else:
        # BinaryOperatorNode and other leaf nodes don't need flattening
        return node


def create_value_node(value):
    """Create appropriate node type for a value"""
    if isinstance(value, (int, float)):
        return NumericValueNode(value)
    if isinstance(value, str):
        # Check if it should be an attribute or a string value
        if should_be_attribute(value):
            return AttributeNode(value)
        return StringValueNode(value)
    if isinstance(value, tuple) and value[0] == "quoted":
        # Quoted strings are always string values
        return StringValueNode(value[1])
    return value  # Fallback for other types


# Helper function to determine if a string should be an AttributeNode
def should_be_attribute(value):
    """Check if a string value should be wrapped in AttributeNode"""
    return isinstance(value, str) and value in KNOWN_CARD_ATTRIBUTES


def make_binary_operator_node(tokens):
    """Create a BinaryOperatorNode, properly wrapping attributes and values"""
    left, operator, right = tokens
    return BinaryOperatorNode(create_value_node(left), operator, create_value_node(right))


def parse_search_query(query: str) -> Query:
    """Parse a Scryfall search query into an AST"""
    if query is None or not query.strip():
        # Return empty query
        return Query(BinaryOperatorNode("name", ":", ""))

    # Pre-process the query to handle implicit AND operations
    # Convert "a b" to "a AND b" when b is not an operator
    query = preprocess_implicit_and(query)

    # Define the grammar components
    attrname = Word(alphas + "_")
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
    def make_quoted_string(tokens):
        """Mark quoted strings so they're always treated as string values"""
        return ("quoted", tokens[0])

    quoted_string = (QuotedString('"', escChar="\\") | QuotedString("'", escChar="\\")).setParseAction(make_quoted_string)

    # Word that doesn't match keywords
    def make_word(tokens):
        word_str = tokens[0]
        if word_str.upper() in ["AND", "OR"]:
            raise ValueError(f"'{word_str}' is a reserved keyword")
        return word_str

    word = Word(alphas + "_").setParseAction(make_word)

    # For attribute values, we want the raw string
    # Create a separate word parser for attribute values
    attr_word = Word(alphas + "_").setParseAction(lambda t: t[0])
    attrval = quoted_string | attr_word | integer | float_number

    # Build the grammar with proper precedence
    expr = Forward()

    # Arithmetic expression: attribute arithmetic_op (attribute | numeric_value)
    arithmetic_expr = attrname + arithmetic_op + (attrname | integer | float_number)
    arithmetic_expr.setParseAction(make_binary_operator_node)

    # Comparison between arithmetic expressions and attributes: arithmetic_expr attrop (arithmetic_expr | attrname)
    arithmetic_comparison = arithmetic_expr + attrop + (arithmetic_expr | attrname)
    arithmetic_comparison.setParseAction(make_binary_operator_node)

    # Comparison between attributes and arithmetic expressions: attrname attrop arithmetic_expr
    attr_arithmetic_comparison = attrname + attrop + arithmetic_expr
    attr_arithmetic_comparison.setParseAction(make_binary_operator_node)

    # Attribute-to-attribute comparison has higher precedence than regular conditions
    attr_attr_condition = attrname + attrop + attrname
    attr_attr_condition.setParseAction(make_binary_operator_node)

    condition = attrname + attrop + attrval
    condition.setParseAction(make_binary_operator_node)

    # Single word (implicit name search)
    def make_single_word(tokens):
        # For single words, we always search in the name field
        return BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode(tokens[0]))

    single_word = word.setParseAction(make_single_word)

    # Grouped expression
    def make_group(tokens):
        return tokens[0]

    group = Group(lparen + expr + rparen).setParseAction(make_group)

    # Factor: can be negated (but not arithmetic expressions)
    def handle_negation(tokens):
        if len(tokens) == 1:
            return tokens[0]

        if len(tokens) == 2 and tokens[0] == "-":
            # Don't allow negation of arithmetic expressions
            if isinstance(tokens[1], BinaryOperatorNode) and tokens[1].operator in ["+", "-", "*", "/"]:
                raise ValueError("Cannot negate arithmetic expressions")
            return NotNode(tokens[1])

        return tokens[0]

    # For negation, we exclude arithmetic expressions from being negated
    negatable_primary = attr_attr_condition | condition | group | single_word
    negatable_factor = Optional(operator_not) + negatable_primary
    negatable_factor.setParseAction(handle_negation)

    # Factor includes both negatable expressions and arithmetic expressions
    # Order matters: arithmetic expressions must come before negatable expressions to avoid ambiguity
    factor = arithmetic_comparison | attr_arithmetic_comparison | arithmetic_expr | negatable_factor

    # Expression with explicit AND/OR operators (highest precedence)
    def handle_operators(tokens):
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
                        raise ValueError(f"Unknown operator: {current_operator}")

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
    except Exception as e:
        raise ValueError(f"Failed to parse query '{query}': {e}")


def preprocess_implicit_and(query: str) -> str:
    """Pre-process query to convert implicit AND operations to explicit ones"""

    # Split the query into tokens while preserving quoted strings and operators
    tokens = []
    i = 0
    while i < len(query):
        if len(tokens) > len(query):
            raise AssertionError(f"tokens is longer than query, {tokens} vs {query}")
        char = query[i]

        if char in ['"', "'"]:
            # Handle quoted string (both double and single quotes)
            quote_char = char
            end_quote = query.find(quote_char, i + 1)
            if end_quote == -1:
                raise ValueError(f"Unmatched {quote_char} quote in query")
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
        elif char == "-":
            # Handle negation as a separate token
            tokens.append(char)
            i += 1
        elif char == ":":
            # Handle colon operator
            tokens.append(char)
            i += 1
        else:
            # Handle words
            word_end = i
            while word_end < len(query) and (query[word_end].isalnum() or query[word_end] == "_"):
                word_end += 1
            tokens.append(query[i:word_end])
            i = word_end

    # Convert implicit AND operations
    result = []
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
            elif not is_operator(token) and next_token == "-" and token != "(" and token.upper() not in ["AND", "OR"]:
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
    """Check if a token is an operator"""
    return token in [":", ">", "<", ">=", "<=", "=", "!=", "-", "+", "*", "/"]


def generate_sql_query(parsed_query: Query) -> str:
    """Generate SQL WHERE clause from parsed query"""
    return parsed_query.to_sql()
