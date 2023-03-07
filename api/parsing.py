from pyparsing import (
    alphas,
    alphanums,
    CaselessKeyword,
    Forward,
    Group,
    Literal,
    OneOrMore,
    Optional,
    ParseException,
    ParseResults,
    oneOf,
    Combine,
    FollowedBy,
    QuotedString,
    Suppress,
    Word,
    nums,
    CaselessLiteral,
    ZeroOrMore,
)


def parse_search_query(query):
    if query is None:
        return []
    # Define keywords and operators
    keyword = Word(alphas)
    operator_or = CaselessKeyword("OR")
    operator_and = CaselessKeyword("AND")
    operator_not = Literal("-")

    operator = operator_or | operator_and | operator_not

    # Define filters
    filter_name = Word(alphas, alphanums + "_")
    filter_value = QuotedString('"') | Word(alphanums)
    filter_expr = Group(filter_name + Literal(":") + filter_value)

    # Define search terms
    term = filter_expr | QuotedString('"', escChar="\\") | keyword

    # Define expression
    expr = Forward()
    group = Group(Suppress("(") + expr + Suppress(")"))
    factor = Optional(operator_not) + (term | group)
    expr <<= factor + OneOrMore((operator_and | operator_or) + factor)

    try:
        parsed = expr.parseString(query)
    except ParseException as e:
        print(f"Error: {e.msg}")
        return None

    return parsed.asList()


def parser_2(query):
    # Define some basic terms
    attrname = Word(alphas)
    colon = Literal(":")
    dash = Literal("-")
    dot = Literal(".")
    comma = Literal(",")
    lparen = Literal("(").suppress()
    rparen = Literal(")").suppress()
    integer = Word(nums).setParseAction(lambda t: int(t[0]))
    float_number = Combine(Word(nums) + Optional(dot + Optional(Word(nums)))).setParseAction(lambda t: float(t[0]))
    quoted_string = QuotedString('"', escChar="\\")

    # Define some basic search terms
    operator = oneOf(": > < >= <=")
    identifier = oneOf("t o f q e c m cm ci is")
    identifier_group = Group(attrname + Optional(colon + identifier))
    search_term = identifier_group + operator + (integer | float_number | quoted_string)

    # Define some more complex search terms
    in_operator = CaselessLiteral("in")
    set_literal = (lparen + Group(OneOrMore(search_term)) + rparen) | quoted_string
    set_search_term = identifier_group + in_operator + set_literal
    and_operator = CaselessLiteral("and")
    or_operator = CaselessLiteral("or")
    not_operator = CaselessLiteral("not")
    boolean_operator = and_operator | or_operator | not_operator

    grammar = OneOrMore(set_search_term | search_term + FollowedBy(set_search_term | boolean_operator)) + ZeroOrMore(
        boolean_operator + OneOrMore(set_search_term | search_term + FollowedBy(set_search_term | boolean_operator))
    )

    return grammar.parseWithTabs().parseString(query)


def parser_3(query):
    attrname = Word(alphas)
    attrop = oneOf(": > < >= <= = !=")

    integer = Word(nums).setParseAction(lambda t: int(t[0]))
    float_number = Combine(Word(nums) + Optional(Literal(".") + Optional(Word(nums)))).setParseAction(lambda t: float(t[0]))

    lparen = Literal("(").suppress()
    rparen = Literal(")").suppress()

    operator_and = CaselessKeyword("AND")
    operator_in = CaselessLiteral("IN")
    operator_not = Literal("-")
    operator_or = CaselessKeyword("OR")

    attrval = QuotedString('"', escChar="\\") | Word(alphas) | integer | float_number

    triplet = Group(attrname + attrop + attrval)
    singlet = Group(QuotedString('"', escChar="\\") | Word(alphas))

    expr = Forward()
    group = Group(lparen + expr + rparen)
    factor = Optional(operator_not) + (triplet | group | singlet)
    expr <<= factor + ZeroOrMore(Optional(operator_and | operator_or) + factor)

    return expr.parseString(query).asList()


def generate_sql_query(parsed_query):
    # Define mappings from Scryfall search terms to SQL syntax
    keyword_map = {
        "name": 'name LIKE "%{value}%"',
        "oracle": 'oracle_text LIKE "%{value}%"',
        "type": 'type_line LIKE "%{value}%"',
        "set": 'set_name = "{value}"',
    }
    operator_map = {
        "OR": "OR",
        "AND": "AND",
        "-": "NOT",
    }
    filter_map = {
        "cmc": "cmc = {value}",
        "color": 'colors LIKE "%{value}%"',
        "name": 'name LIKE "%{value}%"',
    }

    def convert_term(term):
        if isinstance(term, str):
            if term in operator_map:
                return operator_map[term]
            return keyword_map["name"].format(value=term)
        if isinstance(term, ParseResults):
            if len(term) == 1:
                return convert_term(term[0])
            if term[0] == "-":
                return f"NOT ({convert_term(term[1])})"
            operator = operator_map[term[1]]
            left = convert_term(term[0])
            right = convert_term(term[2])
            return f"({left} {operator} {right})"
        if isinstance(term, list):
            return " ".join(convert_term(t) for t in term)

    def convert_filter(filter_expr):
        filter_name, operator, filter_value = filter_expr
        return filter_map[filter_name].format(value=filter_value)

    return {
        # "convert_filter(f)": [convert_filter(f) for f in parsed_query if isinstance(f, list)],
        "convert_term(parsed_query)": convert_term(parsed_query),
    }
    # sql_query = 'SELECT * FROM cards WHERE {}'
    # sql_query += convert_term(parsed_query)
    # sql_query += ' AND '.join(' AND ' + convert_filter(f) for f in parsed_query if isinstance(f, list))
    # return sql_query


def main():
    query = "cmc:2 (o:flying OR o:haste) AND (c:red OR c:green)"
    print(parser_2(query))


if __name__ == "__main__":
    main()
