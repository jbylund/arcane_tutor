"""Tests for card-text HTML escaping and mana formatting parity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.noscript_helpers import (
    convert_mana_symbols,
    create_card_html,
    format_card_text,
    format_oracle_text,
)

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "static" / "fixtures" / "card_text_escaping_cases.json"
ESCAPING_CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    argnames=["case"],
    argvalues=[(c,) for c in ESCAPING_CASES],
    ids=[c["id"] for c in ESCAPING_CASES],
)
def test_format_card_text_shared_fixture_parity(case: dict) -> None:
    """format_card_text must match all fixture permutations exactly."""
    raw = case["input"]
    assert format_card_text(raw, is_modal=False, convert_newlines=False) == case["non_modal_no_newlines"]
    assert format_card_text(raw, is_modal=False, convert_newlines=True) == case["non_modal_newlines"]
    assert format_card_text(raw, is_modal=True, convert_newlines=False) == case["modal_no_newlines"]
    assert format_card_text(raw, is_modal=True, convert_newlines=True) == case["modal_newlines"]


def test_format_card_text_empty_and_none() -> None:
    """Empty or None input produces empty string."""
    assert format_card_text("") == ""
    assert format_card_text(None) == ""  # type: ignore[arg-type]


def test_convert_mana_symbols_delegation() -> None:
    """convert_mana_symbols safely escapes raw input and does not convert newlines."""
    hostile = "<script>alert(1)</script>{W}\n{U}"
    expected = '&lt;script&gt;alert(1)&lt;/script&gt;<span class="mana-symbol ms ms-w ms-cost"></span>\n<span class="mana-symbol ms ms-u ms-cost"></span>'
    assert convert_mana_symbols(hostile, is_modal=False) == expected

    modal_expected = '&lt;script&gt;alert(1)&lt;/script&gt;<span class="modal-mana-symbol ms ms-w ms-cost"></span>\n<span class="modal-mana-symbol ms ms-u ms-cost"></span>'
    assert convert_mana_symbols(hostile, is_modal=True) == modal_expected


def test_format_oracle_text_delegation() -> None:
    """format_oracle_text safely escapes raw input and converts newlines to <br>."""
    hostile = "<script>alert(1)</script>{W}\n{U}"
    expected = '&lt;script&gt;alert(1)&lt;/script&gt;<span class="mana-symbol ms ms-w ms-cost"></span><br><span class="mana-symbol ms ms-u ms-cost"></span>'
    assert format_oracle_text(hostile, is_modal=False) == expected

    modal_expected = '&lt;script&gt;alert(1)&lt;/script&gt;<span class="modal-mana-symbol ms ms-w ms-cost"></span><br><span class="modal-mana-symbol ms ms-u ms-cost"></span>'
    assert format_oracle_text(hostile, is_modal=True) == modal_expected


def test_create_card_html_escapes_hostile_card_fields() -> None:
    """SSR create_card_html must render all hostile card text as inert escaped markup."""
    hostile_card = {
        "name": 'Hostile <script>alert("name")</script>',
        "mana_cost": '{W}<img src=x onerror=alert("cost")>',
        "type_line": 'Creature <b onclick="alert(1)">Inert</b>',
        "oracle_text": 'Deal 3 damage to <script>alert("oracle")</script>.\nPay {R} & "draw".',
        "power": "<svg/onload=alert(1)>",
        "toughness": '4" onmouseover="alert(2)',
        "set_name": 'Dangerous <iframe src="evil.com">Set</iframe>',
        "set_code": "tst",
        "collector_number": "1",
    }

    html = create_card_html(hostile_card, 0)

    # Verify no executable script, img onerror, svg onload, or iframe tags
    assert "<script>" not in html
    assert "<img src=x onerror=" not in html
    assert "<svg" not in html
    assert "<iframe" not in html
    assert 'onclick="alert(1)"' not in html

    # Verify properly escaped content
    assert "&lt;script&gt;alert(&quot;name&quot;)&lt;/script&gt;" in html
    assert '<span class="mana-symbol ms ms-w ms-cost"></span>&lt;img src=x onerror=alert(&quot;cost&quot;)&gt;' in html
    assert (
        'Deal 3 damage to &lt;script&gt;alert(&quot;oracle&quot;)&lt;/script&gt;.<br>Pay <span class="mana-symbol ms ms-r ms-cost"></span> &amp; &quot;draw&quot;.'
        in html
    )
    assert "&lt;svg/onload=alert(1)&gt; / 4&quot; onmouseover=&quot;alert(2)" in html
    assert "Dangerous &lt;iframe src=&quot;evil.com&quot;&gt;Set&lt;/iframe&gt;" in html
