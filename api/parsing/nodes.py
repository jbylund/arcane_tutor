from abc import ABC, abstractmethod


# AST Classes
class QueryNode(ABC):
    """Base class for all query nodes"""

    @abstractmethod
    def to_sql(self) -> str:
        """Convert this node to SQL WHERE clause"""
        pass


class LeafNode(QueryNode):
    """Leaf node - not intended to be used directly"""

    pass


class ValueNode(LeafNode):
    """These represent values, like strings and numbers"""

    value: object

    def __repr__(self):
        return f"{self.__class__.__name__}({repr(self.value)})"

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return self.value == other.value

    def __hash__(self):
        return hash((self.__class__.__name__, self.value))


class StringValueNode(ValueNode):
    """These represent string values, like 'flying' or 'Lightning Bolt'"""

    def __init__(self, value):
        self.value = value

    def to_sql(self):
        """Serialize this node to SQL"""
        return f"'{self.value}'"


class NumericValueNode(ValueNode):
    def __init__(self, value):
        self.value = value

    def to_sql(self):
        """Serialize this node to SQL"""
        return str(self.value)


SEARCH_FIELD_TO_DB_FIELD = {
    "name": "card_name",
    "power": "creature_power",
    "toughness": "creature_toughness",
}

# Field type definitions for query translation
FIELD_TYPES = {
    "name": "text_search",  # Use ILIKE for partial text matching
    "card_name": "text_search",  # Use ILIKE for partial text matching
    "oracle": "text_search",  # Use ILIKE for partial text matching
    "type": "text_search",  # Use ILIKE for partial text matching
    "card_types": "jsonb_array",  # Use JSONB containment operators
    "card_subtypes": "jsonb_array",  # Use JSONB containment operators
    "card_colors": "jsonb_array",  # Use JSONB containment operators
    "cmc": "numeric",  # Use exact equality
    "power": "numeric",  # Use exact equality
    "toughness": "numeric",  # Use exact equality
    "creature_power": "numeric",  # Use exact equality
    "creature_toughness": "numeric",  # Use exact equality
    "mana_cost_text": "text_search",  # Use ILIKE for partial text matching
}


class AttributeNode(LeafNode):
    """These represent attributes of a card, like 'cmc' or 'power'"""

    def __init__(self, attribute_name):
        self.attribute_name = attribute_name

    def to_sql(self):
        """Serialize this node to SQL"""
        remapped_name = SEARCH_FIELD_TO_DB_FIELD.get(self.attribute_name, self.attribute_name)
        return f"card.{remapped_name}"

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return self.attribute_name == other.attribute_name

    def __hash__(self):
        return hash((self.__class__.__name__, self.attribute_name))

    def __repr__(self):
        return f"{self.__class__.__name__}({self.attribute_name})"


class BinaryOperatorNode(QueryNode):
    """Represents a binary operator node"""

    def __init__(self, lhs: QueryNode, operator: str, rhs: QueryNode):
        self.lhs = lhs
        self.operator = operator
        self.rhs = rhs
        bin_ops = {
            "-",
            "!=",
            "*",
            "/",
            "+",
            "<",
            "<=",
            "=",
            ">",
            ">=",
            ":",  # special operator that depends on the types of the compared nodes
        }
        if operator not in bin_ops:
            raise ValueError(f"Unknown operator: {operator}")

    def to_sql(self) -> str:
        """Serialize this node to SQL"""
        # Special handling for the : operator based on field type
        if self.operator == ":":
            return self._handle_colon_operator()

        # Default behavior for other operators
        return f"({self.lhs.to_sql()} {self.operator} {self.rhs.to_sql()})"

    def _handle_colon_operator(self) -> str:
        """Handle the : operator based on field type and value type"""
        if not isinstance(self.lhs, AttributeNode):
            # If LHS is not an attribute, fall back to default behavior
            return f"({self.lhs.to_sql()} = {self.rhs.to_sql()})"

        field_name = self.lhs.attribute_name
        field_type = FIELD_TYPES.get(field_name, "text_search")  # Default to text_search

        if field_type == "text_search" and isinstance(self.rhs, StringValueNode):
            # Text search: use ILIKE with wildcards
            search_terms = self.rhs.value.split()
            if len(search_terms) == 1:
                # Single word: wrap with % for partial matching
                ilike_pattern = f"%{search_terms[0]}%"
            else:
                # Multiple words: join with % for word boundary matching and wrap with %
                ilike_pattern = f"%{'%'.join(search_terms)}%"

            return f"({self.lhs.to_sql()} ILIKE '{ilike_pattern}')"

        elif field_type == "jsonb_array" and isinstance(self.rhs, StringValueNode):
            # JSONB array: use containment operator @>
            # Convert the string value to a JSONB array
            jsonb_value = f'["{self.rhs.value}"]'
            return f"({self.lhs.to_sql()} @> '{jsonb_value}'::jsonb)"

        elif field_type == "jsonb_array" and self.operator in ["<=", "<"]:
            # For JSONB arrays with subset operators, check if card colors are subset of specified colors
            # Convert the string value to a JSONB array and use @< (is contained by) operator
            jsonb_value = f'["{self.rhs.value}"]'
            return f"({self.lhs.to_sql()} <@ '{jsonb_value}'::jsonb)"

        elif field_type == "numeric":
            # Numeric fields: use exact equality
            return f"({self.lhs.to_sql()} = {self.rhs.to_sql()})"

        else:
            # Default fallback: use exact equality
            return f"({self.lhs.to_sql()} = {self.rhs.to_sql()})"

    def __repr__(self):
        return f"{self.__class__.__name__}({self.lhs}, {self.operator}, {self.rhs})"

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return self.lhs == other.lhs and self.operator == other.operator and self.rhs == other.rhs

    def __hash__(self):
        return hash((self.__class__.__name__, self.lhs, self.operator, self.rhs))


class NaryOperatorNode(QueryNode):
    """Base class for n-ary operators (AND, OR) that take multiple operands"""

    def __init__(self, operands: list[QueryNode]):
        self.operands = operands

    def to_sql(self) -> str:
        """Serialize this node to SQL"""
        if not self.operands:
            return self._empty_result()
        elif len(self.operands) == 1:
            return self.operands[0].to_sql()
        else:
            inners = f" {self._operator()} ".join(operand.to_sql() for operand in self.operands)
            return f"({inners})"

    def _operator(self) -> str:
        """Return the SQL operator string - to be implemented by subclasses"""
        raise NotImplementedError

    def _empty_result(self) -> str:
        """Return the result for empty operands - to be implemented by subclasses"""
        raise NotImplementedError

    def __repr__(self):
        return f'{self.__class__.__name__}({", ".join(repr(op) for op in self.operands)})'

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return self.operands == other.operands

    def __hash__(self):
        return hash((self.__class__.__name__, tuple(self.operands)))


class AndNode(NaryOperatorNode):
    """Represents AND operation between multiple conditions"""

    def _operator(self) -> str:
        return "AND"

    def _empty_result(self) -> str:
        return "TRUE"


class OrNode(NaryOperatorNode):
    """Represents OR operation between multiple conditions"""

    def _operator(self) -> str:
        return "OR"

    def _empty_result(self) -> str:
        return "FALSE"


class NotNode(QueryNode):
    """Represents NOT operation"""

    def __init__(self, operand: QueryNode):
        self.operand = operand

    def to_sql(self) -> str:
        """Serialize this node to SQL"""
        operand_sql = self.operand.to_sql()
        return f"NOT ({operand_sql})"

    def __repr__(self):
        return f"Not({self.operand})"

    def __eq__(self, other):
        if not isinstance(other, NotNode):
            return False
        return self.operand == other.operand

    def __hash__(self):
        return hash(("Not", self.operand))


class Query(QueryNode):
    """Top-level query container"""

    def __init__(self, root: QueryNode):
        self.root = root

    def to_sql(self) -> str:
        """Serialize this query to SQL"""
        return self.root.to_sql()

    def __repr__(self):
        return f"Query({self.root})"

    def __eq__(self, other):
        if not isinstance(other, Query):
            return False
        return self.root == other.root

    def __hash__(self):
        return hash(("Query", self.root))
