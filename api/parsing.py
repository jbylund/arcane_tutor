from pyparsing import (
    alphas,
    CaselessKeyword,
    Forward,
    Group,
    Literal,
    Optional,
    ParseResults,
    oneOf,
    Combine,
    QuotedString,
    Word,
    nums,
    CaselessLiteral,
    ZeroOrMore,
)


def default_parse_action(original_string, location, tokens):
    # print(original_string, location, tokens)
    # s = the original string being parsed (see note below)
    # loc = the location of the matching substring
    # toks = a list of the matched tokens, packaged as a ParseResults object
    pass


class Triplet:
    def __init__(self, attrname, operator, attrval) -> None:
        self.attrname = attrname
        self.operator = operator
        self.attrval = attrval

    def __repr__(self) -> str:
        return f"""Triplet("{self.attrname}", "{self.operator}", "{self.attrval}")"""

    def __eq__(self, o: object) -> bool:
        if not isinstance(o, Triplet):
            return False
        return (self.attrname == o.attrname) and (self.operator == o.operator) and (self.attrval == o.attrval)


def replace_with_triplet(original_string, location, tokens):
    as_triplet = Triplet(*tokens)
    while tokens:
        tokens.pop()
    tokens.append(as_triplet)


def singlet_to_triplet(original_string, location, tokens):
    as_triplet = Triplet("defaultattr", ":", tokens[0])
    while tokens:
        tokens.pop()
    tokens.append(as_triplet)


def floatify(original_string, location, tokens):
    tokens[0] = float(tokens[0])


def intify(original_string, location, tokens):
    tokens[0] = int(tokens[0])


def parse(query):
    attrname = Word(alphas)

    attrname.set_parse_action(default_parse_action)

    attrop = oneOf(": > < >= <= = !=")

    integer = Word(nums).setParseAction(intify)
    float_number = Combine(Word(nums) + Optional(Literal(".") + Optional(Word(nums)))).setParseAction(floatify)

    lparen = Literal("(").suppress()
    rparen = Literal(")").suppress()

    operator_and = CaselessKeyword("AND")
    operator_in = CaselessLiteral("IN")  # TODO
    operator_not = Literal("-")
    operator_or = CaselessKeyword("OR")

    attrval = QuotedString('"', escChar="\\") | Word(alphas) | integer | float_number

    triplet = attrname + attrop + attrval
    triplet.set_parse_action(replace_with_triplet)

    singlet = QuotedString('"', escChar="\\") | Word(alphas)
    singlet.set_parse_action(singlet_to_triplet)

    expr = Forward()
    group = Group(lparen + expr + rparen)
    factor = Optional(operator_not) + (triplet | group | singlet)
    expr <<= factor + ZeroOrMore(Optional(operator_and | operator_or) + factor)

    return expr.parseString(query).asList()


def generate_sql_query(parsed_query):
    # Define mappings from Scryfall search terms to SQL syntax
    print(parsed_query)


def main():
    query = "cmc:2 (o:flying OR o:haste) AND (c:red OR c:green)"
    print(parse(query))


if __name__ == "__main__":
    main()
