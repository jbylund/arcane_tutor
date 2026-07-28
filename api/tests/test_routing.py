"""Tests for marking methods as HTTP routes and collecting the marked ones off a class."""

from __future__ import annotations

import pytest

from api.utils.routing import RouteSpec, iter_marked_routes, route

method_testcases = {
    "get_implies_head": {"declared": ("GET",), "expected": {"GET", "HEAD"}},
    "head_not_implied_without_get": {"declared": ("POST",), "expected": {"POST"}},
    "lowercase_is_normalized": {"declared": ("post", "put"), "expected": {"POST", "PUT"}},
    "get_alongside_others_still_implies_head": {"declared": ("GET", "POST"), "expected": {"GET", "HEAD", "POST"}},
}


class TestMarking:
    """The decorator attaches a spec and leaves the function alone."""

    def test_returns_the_function_unchanged(self) -> None:
        """Test @route marks rather than wraps.

        Wrapping would put the request-facing coercer between internal callers and the handler, and
        would add a frame to every traceback.
        """

        def handler() -> None: ...

        assert route()(handler) is handler

    def test_default_path_is_the_function_name(self) -> None:
        """Test a route declares no path in the common case."""

        @route()
        def search() -> None: ...

        assert search._route_spec.paths == ("search",)

    def test_explicit_paths_replace_the_default(self) -> None:
        """Test a handler reachable under a static/ alias declares both of its paths."""

        @route(paths=("app_js", "static/app_js"))
        def app_js() -> None: ...

        assert app_js._route_spec.paths == ("app_js", "static/app_js")

    def test_flags_default_to_advertised_and_lenient(self) -> None:
        """Test the not-yet-enforced flags carry the defaults the plan calls for."""

        @route()
        def handler() -> None: ...

        assert handler._route_spec.advertise is True
        assert handler._route_spec.ignore_unknown_params is False


class TestDeclaredMethods:
    """GET implies HEAD; nothing else is inferred."""

    @pytest.mark.parametrize(
        argnames=sorted(next(iter(method_testcases.values()))),
        argvalues=[[v for _, v in sorted(method_testcases[name].items())] for name in sorted(method_testcases)],
        ids=sorted(method_testcases),
    )
    def test_declared_methods_resolve_to_accepted_set(self, declared: tuple[str, ...], expected: set[str]) -> None:
        """Test each declared-method shape resolves to the set the route accepts."""

        @route(methods=declared)
        def handler() -> None: ...

        assert handler._route_spec.methods == frozenset(expected)


class TestCollection:
    """Registration scans the class for markers, not the instance for callables."""

    def test_collects_only_marked_methods(self) -> None:
        """Test an unmarked public method is not a route, which is the fail-closed default."""

        class Resource:
            @route()
            def marked(self) -> None: ...

            def unmarked(self) -> None: ...

        assert set(dict(iter_marked_routes(Resource))) == {"marked"}

    def test_underscore_no_longer_means_unroutable(self) -> None:
        """Test a marked private method is a route.

        The point of marking: `_` goes back to meaning only "private in Python", so `setup_schema`
        can stop being HTTP-reachable without being renamed, and `_root` can be a route.
        """

        class Resource:
            @route()
            def _root(self) -> None: ...

            def _helper(self) -> None: ...

        assert set(dict(iter_marked_routes(Resource))) == {"_root"}

    def test_instance_attributes_cannot_become_routes(self) -> None:
        """Test a marked callable assigned in __init__ is not registered.

        Scanning `dir(self)` meant any new public attribute could change the route table; a child
        resource escaped only because it had no __call__.
        """

        @route()
        def escaped() -> None: ...

        class Resource:
            def __init__(self) -> None:
                self.child = escaped

        assert dict(iter_marked_routes(type(Resource()))) == {}

    def test_inherited_routes_are_collected(self) -> None:
        """Test a subclass answers its base's routes, which is how a child resource will mount."""

        class Base:
            @route()
            def inherited(self) -> None: ...

        class Child(Base):
            @route()
            def own(self) -> None: ...

        assert set(dict(iter_marked_routes(Child))) == {"inherited", "own"}

    def test_yields_specs_alongside_names(self) -> None:
        """Test the collector hands back the spec, so registration needs no second lookup."""

        class Resource:
            @route(paths=("a", "b"), methods=("POST",))
            def handler(self) -> None: ...

        collected = list(iter_marked_routes(Resource))
        assert len(collected) == 1
        name, spec = collected[0]
        assert name == "handler"
        assert isinstance(spec, RouteSpec)
        assert spec.paths == ("a", "b")
        assert spec.methods == frozenset({"POST"})
