from pyparsing import (
    CaselessKeyword,
    CaselessLiteral,
    Combine,
    Forward,
    Group,
    Literal,
    Optional,
    ParseResults,
    QuotedString,
    Word,
    ZeroOrMore,
    alphas,
    nums,
    oneOf,
)
from abc import ABC, abstractmethod
from typing import List, Union, Optional as OptionalType


# AST Classes
class QueryNode(ABC):
    """Base class for all query nodes"""
    
    @abstractmethod
    def to_sql(self) -> str:
        """Convert this node to SQL WHERE clause"""
        pass





class Condition(QueryNode):
    """Represents a single condition like 'oracle:flying' or 'type:creature'"""
    
    def __init__(self, attribute: str, operator: str, value: Union[str, int, float]):
        self.attribute = attribute
        self.operator = operator
        self.value = value
    
    def to_sql(self) -> str:
        # Map Scryfall attributes to database columns
        column_map = {
            "name": "name",
            "cmc": "cmc",
            "oracle": "oracle_text", 
            "type": "type_line",
            "set": "set_name",
            "color": "colors",
            "power": "power",
            "toughness": "toughness",
            "rarity": "rarity",
            "artist": "artist",
        }
        
        column = column_map.get(self.attribute, self.attribute)
        
        # Handle different operators
        if self.operator == ":":
            if isinstance(self.value, str):
                return f'{column} LIKE "%{self.value}%"'
            else:
                return f'{column} = {self.value}'
        elif self.operator == "=":
            if isinstance(self.value, str):
                return f'{column} = "{self.value}"'
            else:
                return f'{column} = {self.value}'
        elif self.operator == "!=":
            if isinstance(self.value, str):
                return f'{column} != "{self.value}"'
            else:
                return f'{column} != {self.value}'
        elif self.operator == ">":
            return f'{column} > {self.value}'
        elif self.operator == ">=":
            return f'{column} >= {self.value}'
        elif self.operator == "<":
            return f'{column} < {self.value}'
        elif self.operator == "<=":
            return f'{column} <= {self.value}'
        else:
            raise ValueError(f"Unknown operator: {self.operator}")
    
    def __repr__(self):
        return f'Condition({self.attribute}{self.operator}{self.value})'
    
    def __eq__(self, other):
        if not isinstance(other, Condition):
            return False
        return (self.attribute == other.attribute and 
                self.operator == other.operator and 
                self.value == other.value)
    
    def __hash__(self):
        return hash((self.attribute, self.operator, self.value))


class AndNode(QueryNode):
    """Represents AND operation between multiple conditions"""
    
    def __init__(self, operands: List[QueryNode]):
        self.operands = operands
    
    def to_sql(self) -> str:
        if not self.operands:
            return "TRUE"
        elif len(self.operands) == 1:
            return self.operands[0].to_sql()
        else:
            sql_parts = [operand.to_sql() for operand in self.operands]
            return f"({' AND '.join(sql_parts)})"
    
    def __repr__(self):
        return f'And({", ".join(repr(op) for op in self.operands)})'
    
    def __eq__(self, other):
        if not isinstance(other, AndNode):
            return False
        return self.operands == other.operands
    
    def __hash__(self):
        return hash(('And', tuple(self.operands)))


class OrNode(QueryNode):
    """Represents OR operation between multiple conditions"""
    
    def __init__(self, operands: List[QueryNode]):
        self.operands = operands
    
    def to_sql(self) -> str:
        if not self.operands:
            return "FALSE"
        elif len(self.operands) == 1:
            return self.operands[0].to_sql()
        else:
            sql_parts = [operand.to_sql() for operand in self.operands]
            return f"({' OR '.join(sql_parts)})"
    
    def __repr__(self):
        return f'Or({", ".join(repr(op) for op in self.operands)})'
    
    def __eq__(self, other):
        if not isinstance(other, OrNode):
            return False
        return self.operands == other.operands
    
    def __hash__(self):
        return hash(('Or', tuple(self.operands)))


class NotNode(QueryNode):
    """Represents NOT operation"""
    
    def __init__(self, operand: QueryNode):
        self.operand = operand
    
    def to_sql(self) -> str:
        operand_sql = self.operand.to_sql()
        return f"NOT ({operand_sql})"
    
    def __repr__(self):
        return f'Not({self.operand})'
    
    def __eq__(self, other):
        if not isinstance(other, NotNode):
            return False
        return self.operand == other.operand
    
    def __hash__(self):
        return hash(('Not', self.operand))


class Query(QueryNode):
    """Top-level query container"""
    
    def __init__(self, root: QueryNode):
        self.root = root
    
    def to_sql(self) -> str:
        return self.root.to_sql()
    
    def __repr__(self):
        return f'Query({self.root})'
    
    def __eq__(self, other):
        if not isinstance(other, Query):
            return False
        return self.root == other.root
    
    def __hash__(self):
        return hash(('Query', self.root))


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
        # Condition and other leaf nodes don't need flattening
        return node


def parse_search_query(query: str) -> Query:
    """Parse a Scryfall search query into an AST"""
    if query is None or not query.strip():
        # Return empty query
        return Query(Condition("name", ":", ""))
    
    # Pre-process the query to handle implicit AND operations
    # Convert "a b" to "a AND b" when b is not an operator
    query = preprocess_implicit_and(query)
    
    # Define the grammar components
    attrname = Word(alphas)
    attrop = oneOf(": > < >= <= = !=")
    
    integer = Word(nums).setParseAction(lambda t: int(t[0]))
    float_number = Combine(Word(nums) + Optional(Literal(".") + Optional(Word(nums)))).setParseAction(lambda t: float(t[0]))
    
    lparen = Literal("(").suppress()
    rparen = Literal(")").suppress()
    
    # Keywords must be recognized before regular words
    operator_and = CaselessKeyword("AND")
    operator_or = CaselessKeyword("OR")
    operator_not = Literal("-")
    
    # Handle quoted strings and regular words (but not keywords)
    quoted_string = QuotedString('"', escChar="\\")
    # Word that doesn't match keywords
    def make_word(tokens):
        word_str = tokens[0]
        if word_str.upper() in ["AND", "OR"]:
            raise ValueError(f"'{word_str}' is a reserved keyword")
        return word_str
    
    word = Word(alphas).setParseAction(make_word)
    
    # For attribute values, we want the raw string
    # Create a separate word parser for attribute values
    attr_word = Word(alphas).setParseAction(lambda t: t[0])
    attrval = quoted_string | attr_word | integer | float_number
    
    # Build the grammar with proper precedence
    expr = Forward()
    
    # Basic condition: attribute operator value
    def make_condition(tokens):
        return Condition(tokens[0], tokens[1], tokens[2])
    
    condition = attrname + attrop + attrval
    condition.setParseAction(make_condition)
    
    # Single word (implicit name search)
    def make_single_word(tokens):
        return Condition("name", ":", tokens[0])
    
    single_word = word.setParseAction(make_single_word)
    
    # Grouped expression
    def make_group(tokens):
        return tokens[0]
    
    group = Group(lparen + expr + rparen).setParseAction(make_group)
    
    # Primary: condition, group, or single word
    primary = condition | group | single_word
    
    # Factor: can be negated
    def handle_negation(tokens):
        if len(tokens) == 1:
            return tokens[0]
        elif len(tokens) == 2 and tokens[0] == "-":
            return NotNode(tokens[1])
        else:
            return tokens[0]
    
    factor = Optional(operator_not) + primary
    factor.setParseAction(handle_negation)
    
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
        elif current_operator == "OR":
            return OrNode(current_operands)
        else:
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
            flattened = flatten_nested_operations(Query(parsed[0]))
            return flattened
        else:
            return Query(Condition("name", ":", ""))
    except Exception as e:
        raise ValueError(f"Failed to parse query '{query}': {e}")


def preprocess_implicit_and(query: str) -> str:
    """Pre-process query to convert implicit AND operations to explicit ones"""
    import re
    
    # Split the query into tokens while preserving quoted strings and operators
    tokens = []
    i = 0
    while i < len(query):
        char = query[i]
        
        if char == '"':
            # Handle quoted string
            end_quote = query.find('"', i + 1)
            if end_quote == -1:
                raise ValueError("Unmatched quote in query")
            tokens.append(query[i:end_quote + 1])
            i = end_quote + 1
        elif char in '()':
            # Handle parentheses
            tokens.append(char)
            i += 1
        elif char.isspace():
            # Skip whitespace
            i += 1
        elif char in '><=!':
            # Handle operators
            if i + 1 < len(query) and query[i:i+2] in ['>=', '<=', '!=']:
                tokens.append(query[i:i+2])
                i += 2
            else:
                tokens.append(char)
                i += 1
        elif char == '-':
            # Handle negation as a separate token
            tokens.append(char)
            i += 1
        elif char == ':':
            # Handle colon operator
            tokens.append(char)
            i += 1
        else:
            # Handle words
            word_end = i
            while word_end < len(query) and (query[word_end].isalnum() or query[word_end] == '_'):
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
            if (not is_operator(token) and 
                not is_operator(next_token) and
                token != '(' and 
                next_token != ')' and
                token.upper() not in ['AND', 'OR'] and
                next_token.upper() not in ['AND', 'OR']):
                result.append('AND')
            # Special case: if current token is not an operator and next token is negation,
            # we need to insert AND to separate them as factors
            elif (not is_operator(token) and 
                  next_token == '-' and
                  token != '(' and
                  token.upper() not in ['AND', 'OR']):
                result.append('AND')
        
        i += 1
    
    return ' '.join(result)


def is_operator(token: str) -> bool:
    """Check if a token is an operator"""
    return token in [':', '>', '<', '>=', '<=', '=', '!=', '-']


def generate_sql_query(parsed_query: Query) -> str:
    """Generate SQL WHERE clause from parsed query"""
    return parsed_query.to_sql()


def main():
    """Test the parser"""
    test_queries = [
        "cmc:2",
        "name:\"Lightning Bolt\"",
        "cmc:2 AND oracle:flying",
        "oracle:flying OR oracle:haste",
        "cmc:2 AND (oracle:flying OR oracle:haste)",
        "color:red OR color:green",
        "cmc>3",
        "type:creature AND power>5",
        "a b",  # implicit AND
        "a AND b",  # explicit AND
        "a OR b",  # explicit OR
    ]
    
    for query in test_queries:
        print(f"\nQuery: {query}")
        try:
            parsed = parse_search_query(query)
            print(f"AST: {parsed}")
            sql = generate_sql_query(parsed)
            print(f"SQL: {sql}")
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
