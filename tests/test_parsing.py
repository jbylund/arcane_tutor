import pytest
from api import parsing


@pytest.mark.parametrize(
    "test_input, expected",
    [
        ("a", [["a"]]),
        ("a b c", [["a"], ["b"], ["c"]]),
        ("a -b", [["a"], "-", ["b"]]),
        ("a -b:c", [["a"], "-", ["b", ":", "c"]]),
        ("a -(c or d)", [["a"], "-", [["c"], "OR", ["d"]]]),
        ("a:b", [["a", ":", "b"]]),
        ("a:b c:d", [["a", ":", "b"], ["c", ":", "d"]]),
        ("a:b c:d e:f", [["a", ":", "b"], ["c", ":", "d"], ["e", ":", "f"]]),
        ("a:b and c:d", [["a", ":", "b"], "AND", ["c", ":", "d"]]),
        ("a:b or c:d", [["a", ":", "b"], "OR", ["c", ":", "d"]]),
        ("a:b and c:d or e:f", [["a", ":", "b"], "AND", ["c", ":", "d"], "OR", ["e", ":", "f"]]),
        ("a b:c", [["a"], ["b", ":", "c"]]),

        # other operators
        ("a=b", [["a", "=", "b"]]),
        ("cmc=3", [["cmc", "=", 3]]),
        ("a!=b", [["a", "!=", "b"]]),
        
        # more parens
        ("a:b and (c:d or e:f)", [["a", ":", "b"], "AND", [["c", ":", "d"], "OR", ["e", ":", "f"]]]),
    ],
)
def test_parse(test_input, expected):
    observed = parsing.parse(test_input)
    assert observed == expected
