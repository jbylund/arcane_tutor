"""Post-parse pipeline — the single AST seam every front-end parser must use.

String parsing has two front ends (hand-rolled production parser, legacy pyparsing
oracle for parity tests). They produce different pre-rewrite trees but must not
diverge on rewrites, regex limits, or compound normalization. ``finalize_query``
is that convergence point: raw ``Query`` in, production-ready ``Query`` out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.parsing.regex_budget import validate_regex_patterns
from api.parsing.rewrite import flatten_and_deduplicate_compounds, rewrite_query

if TYPE_CHECKING:
    from api.parsing.nodes import Query


def finalize_query(query: Query) -> Query:
    """Apply semantic rewrites, regex static limits, then flatten+dedupe normalization."""
    query = rewrite_query(query)
    validate_regex_patterns(query)
    return flatten_and_deduplicate_compounds(query)
