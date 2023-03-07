import pytest
from api.parsing import parse, Triplet, generate_sql_query


def dt(attrval):
    return Triplet("defaultattr", ":", attrval)


@pytest.mark.parametrize(
    "test_input, expected",
    [
        ("a", [dt("a")]),
        ("a b c", [dt("a"), dt("b"), dt("c")]),
        ("a -b", [dt("a"), "-", dt("b")]),
        ("a -b:c", [dt("a"), "-", Triplet("b", ":", "c")]),
        (
            "a -(c or d)",
            [Triplet("defaultattr", ":", "a"), "-", [Triplet("defaultattr", ":", "c"), "OR", Triplet("defaultattr", ":", "d")]],
        ),
        ("a:b", [Triplet("a", ":", "b")]),
        ("a:b c:d", [Triplet("a", ":", "b"), Triplet("c", ":", "d")]),
        ("a:b c:d e:f", [Triplet("a", ":", "b"), Triplet("c", ":", "d"), Triplet("e", ":", "f")]),
        ("a:b and c:d", [Triplet("a", ":", "b"), "AND", Triplet("c", ":", "d")]),
        ("a:b or c:d", [Triplet("a", ":", "b"), "OR", Triplet("c", ":", "d")]),
        ("a:b and c:d or e:f", [Triplet("a", ":", "b"), "AND", Triplet("c", ":", "d"), "OR", Triplet("e", ":", "f")]),
        ("a b:c", [Triplet("defaultattr", ":", "a"), Triplet("b", ":", "c")]),
        # other operators
        ("a=b", [Triplet("a", "=", "b")]),
        ("cmc=3", [Triplet("cmc", "=", 3)]),
        ("a!=b", [Triplet("a", "!=", "b")]),
        # more parens
        ("a:b and (c:d or e:f)", [Triplet("a", ":", "b"), "AND", [Triplet("c", ":", "d"), "OR", Triplet("e", ":", "f")]]),
    ],
)
def test_parse(test_input, expected):
    observed = parse(test_input)
    assert observed == expected


def test_parse_then_sql():
    query = "cmc:2 (o:flying OR o:haste) AND (c:red OR c:green)"
    parsed_query = parse(query)
    sql_query = generate_sql_query(parsed_query)
    assert sql_query == "SELECT * FROM cards WHERE cmc = 2 AND (o = 'flying' OR o = 'haste') AND (c = 'red' OR c = 'green')"
