# `to_human_explanation`'s Empty-Value Guard Missed `ManaValueNode`

**DONE — fixed on the `mana-atom-and-explanation-followups` branch, stacked on #909.**

[#950](https://github.com/jbylund/sylvan_librarian/issues/950), found during review of #909.

`CardAttributeBinaryOpNode.to_human_explanation`'s empty-value guard only checked
`isinstance(self.rhs, StringValueNode)`, so it no longer caught the `ManaValueNode` that
`parse_mana_value` produces for mana/devotion values as of #909.

## Why

Before #909, a quoted empty mana value parsed to a `StringValueNode`, which this guard suppressed.
#909 routes mana values through `ManaValueNode` instead, and the check at
[api/parsing/card_query_nodes.py:638](../../api/parsing/card_query_nodes.py#L638) wasn't updated to
match — even though the identical fix already exists a few hundred lines down, in
`_handle_text_field_pattern_matching`:

```python
if isinstance(self.rhs, StringValueNode | ManaValueNode):
```

Confirmed reachable: `parsing_f.parse_scryfall_query('mana:""').root.to_human_explanation()` returned
`'mana cost contains '` (trailing space, garbled) instead of `''`.

## Fix

```python
if isinstance(self.rhs, StringValueNode | ManaValueNode) and not self.rhs.value.strip():
    return ""
```

## Tests

`test_explanation.py::test_explain_empty_mana_value`, parametrized over `mana:""` and `devotion:""`
and both parsers — confirmed to fail on the pre-fix code (`'devotion contains '` instead of `''`)
before the fix landed.
