"""Tests for error monitoring sanitization, Honeybadger configuration, and error handler."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import MagicMock, patch

from honeybadger.config import Configuration
from honeybadger.core import Honeybadger

from api.utils import error_monitoring
from api.utils.error_monitoring import (
    FILTERED_VALUE,
    PARAMS_FILTERS,
    error_handler,
    honeybadger_config,
    is_sensitive_key,
    sanitize_data,
    sanitize_notice,
)


def test_is_sensitive_key_authorization_variants() -> None:
    """Test is_sensitive_key matches various authorization key spellings."""
    sensitive_keys = [
        "authorization",
        "Authorization",
        "AUTHORIZATION",
        "http_authorization",
        "Http-Authorization",
        "HTTP_AUTHORIZATION",
        "http-authorization",
        "proxy_authorization",
        "Proxy-Authorization",
        "PROXY_AUTHORIZATION",
        "custom_authorization",
    ]
    for key in sensitive_keys:
        assert is_sensitive_key(key) is True, f"Expected {key} to be recognized as sensitive"


def test_is_sensitive_key_cookie_variants() -> None:
    """Test is_sensitive_key matches various cookie key spellings."""
    sensitive_keys = [
        "cookie",
        "Cookie",
        "COOKIE",
        "cookies",
        "Cookies",
        "set_cookie",
        "Set-Cookie",
        "SET_COOKIE",
        "set-cookie",
        "http_cookie",
        "HTTP_COOKIE",
        "http-cookie",
        "csrf_cookie",
        "CSRF_COOKIE",
        "csrf-cookie",
        "session_cookie",
    ]
    for key in sensitive_keys:
        assert is_sensitive_key(key) is True, f"Expected {key} to be recognized as sensitive"


def test_is_sensitive_key_password_and_admin_password_variants() -> None:
    """Test is_sensitive_key matches password and admin_password variants."""
    sensitive_keys = [
        "admin_password",
        "Admin_Password",
        "ADMIN_PASSWORD",
        "admin-password",
        "Admin-Password",
        "adminPassword",
        "AdminPassword",
        "password",
        "Password",
        "PASSWORD",
        "password_confirmation",
        "PasswordConfirmation",
        "user_password",
        "db_password",
        "credit_card",
        "Credit-Card",
        "creditCard",
    ]
    for key in sensitive_keys:
        assert is_sensitive_key(key) is True, f"Expected {key} to be recognized as sensitive"


def test_is_sensitive_key_honeybadger_api_key_variants() -> None:
    """Test is_sensitive_key matches honeybadger_api_key variants."""
    sensitive_keys = [
        "honeybadger_api_key",
        "Honeybadger_Api_Key",
        "HONEYBADGER_API_KEY",
        "honeybadger-api-key",
        "Honeybadger-Api-Key",
        "honeybadgerApiKey",
        "HoneybadgerApiKey",
        "honeybadger_key",
        "HONEYBADGER_KEY",
        "honeybadger_apikey",
        "honeybadger_token",
    ]
    for key in sensitive_keys:
        assert is_sensitive_key(key) is True, f"Expected {key} to be recognized as sensitive"


def test_is_sensitive_key_preserves_safe_keys() -> None:
    """Test is_sensitive_key does not falsely match safe application keys."""
    safe_keys = [
        "card_name",
        "q",
        "query",
        "limit",
        "offset",
        "path",
        "method",
        "headers",
        "params",
        "author",
        "authors",
        "authority",
        "user_id",
        "status",
        "page",
        "order",
        "unique",
        "format",
        "count",
        "is_active",
    ]
    for key in safe_keys:
        assert is_sensitive_key(key) is False, f"Expected {key} to be considered safe"


def test_sanitize_data_nested_structures() -> None:
    """Test sanitize_data recursively redacts sensitive keys across nested structures."""
    payload: dict[str, Any] = {
        "user": {
            "name": "Alice",
            "adminPassword": "super_secret_password",
            "metadata": {
                "tags": ["admin", "staff"],
                "HONEYBADGER_API_KEY": "hb_secret_12345",
            },
        },
        "sessions": [
            {"session_id": "123", "Cookie": "session=abc456"},
            {"session_id": "789", "safe_token_name": "ok"},
        ],
        "headers_tuple": (
            {"Authorization": "Bearer secret_jwt"},
            {"Accept": "application/json"},
        ),
        "safe_number": 42,
    }

    sanitized = sanitize_data(payload)

    assert isinstance(sanitized, dict)
    assert sanitized["user"]["name"] == "Alice"
    assert sanitized["user"]["adminPassword"] == FILTERED_VALUE
    assert sanitized["user"]["metadata"]["tags"] == ["admin", "staff"]
    assert sanitized["user"]["metadata"]["HONEYBADGER_API_KEY"] == FILTERED_VALUE
    assert sanitized["sessions"][0]["session_id"] == "123"
    assert sanitized["sessions"][0]["Cookie"] == FILTERED_VALUE
    assert sanitized["sessions"][1]["session_id"] == "789"
    assert sanitized["sessions"][1]["safe_token_name"] == "ok"
    assert sanitized["headers_tuple"][0]["Authorization"] == FILTERED_VALUE
    assert sanitized["headers_tuple"][1]["Accept"] == "application/json"
    assert sanitized["safe_number"] == 42


def test_sanitize_data_does_not_mutate_original() -> None:
    """Test sanitize_data does not mutate the original data structures."""
    original: dict[str, Any] = {
        "headers": {
            "Authorization": "Bearer secret",
            "User-Agent": "Mozilla/5.0",
        },
        "params": {
            "q": "lightning bolt",
            "admin_password": "secret_password",
        },
        "nested_list": [
            {"Cookie": "sid=123", "keep": "yes"},
        ],
    }

    original_snapshot = copy.deepcopy(original)
    sanitized = sanitize_data(original)

    # Original should be completely unmodified
    assert original == original_snapshot
    assert original["headers"]["Authorization"] == "Bearer secret"
    assert original["params"]["admin_password"] == "secret_password"
    assert original["nested_list"][0]["Cookie"] == "sid=123"

    # Sanitized result contains replacements
    assert isinstance(sanitized, dict)
    assert sanitized["headers"]["Authorization"] == FILTERED_VALUE
    assert sanitized["headers"]["User-Agent"] == "Mozilla/5.0"
    assert sanitized["params"]["admin_password"] == FILTERED_VALUE
    assert sanitized["params"]["q"] == "lightning bolt"
    assert sanitized["nested_list"][0]["Cookie"] == FILTERED_VALUE
    assert sanitized["nested_list"][0]["keep"] == "yes"


def test_sanitize_data_handles_circular_references() -> None:
    """Test sanitize_data handles circular references without infinite recursion."""
    circular_dict: dict[str, Any] = {"safe": "value", "password": "secret"}
    circular_dict["self"] = circular_dict

    sanitized = sanitize_data(circular_dict)
    assert isinstance(sanitized, dict)
    assert sanitized["password"] == FILTERED_VALUE
    assert sanitized["safe"] == "value"
    assert sanitized["self"] == "[CIRCULAR]"


def test_sanitize_notice_redacts_context_and_locals() -> None:
    """Test sanitize_notice redacts sensitive fields in both context and local_variables."""
    mock_notice = MagicMock()
    mock_notice.context = {
        "headers": {
            "Authorization": "Bearer token",
            "User-Agent": "TestClient",
        },
        "path": "/search",
    }
    payload_dict = {
        "request": {
            "context": {
                "headers": {
                    "Authorization": "Bearer token",
                    "User-Agent": "TestClient",
                },
                "path": "/search",
            },
            "local_variables": {
                "admin_password": "sensitive_local",
                "HoneybadgerApiKey": "hb_secret",
                "card_name": "Counterspell",
                "count": 10,
            },
        },
    }
    mock_notice.payload = payload_dict

    result = sanitize_notice(mock_notice)
    assert result is mock_notice

    # Context verification
    assert mock_notice.context["headers"]["Authorization"] == FILTERED_VALUE
    assert mock_notice.context["headers"]["User-Agent"] == "TestClient"

    # Payload request context verification
    req_payload = mock_notice.payload["request"]
    assert req_payload["context"]["headers"]["Authorization"] == FILTERED_VALUE
    assert req_payload["context"]["headers"]["User-Agent"] == "TestClient"

    # Retained local variables verification
    assert req_payload["local_variables"]["admin_password"] == FILTERED_VALUE
    assert req_payload["local_variables"]["HoneybadgerApiKey"] == FILTERED_VALUE
    assert req_payload["local_variables"]["card_name"] == "Counterspell"
    assert req_payload["local_variables"]["count"] == 10


def test_sanitize_notice_dictionary_input() -> None:
    """Test sanitize_notice when input is a raw dictionary payload."""
    payload = {
        "request": {
            "context": {
                "Cookie": "session=secret",
                "method": "POST",
            },
            "local_variables": {
                "admin_password": "pwd",
                "safe_var": 123,
            },
        },
    }
    sanitized = sanitize_notice(payload)
    assert isinstance(sanitized, dict)
    assert sanitized["request"]["context"]["Cookie"] == FILTERED_VALUE
    assert sanitized["request"]["context"]["method"] == "POST"
    assert sanitized["request"]["local_variables"]["admin_password"] == FILTERED_VALUE
    assert sanitized["request"]["local_variables"]["safe_var"] == 123


def test_honeybadger_config_parameters() -> None:
    """Test honeybadger_config specifies required params_filters and before_notify."""
    assert honeybadger_config["report_local_variables"] is True
    assert honeybadger_config["force_report_data"] is True
    assert honeybadger_config["before_notify"] == sanitize_notice

    filters = honeybadger_config["params_filters"]
    assert "authorization" in filters
    assert "cookie" in filters
    assert "admin_password" in filters
    assert "honeybadger_api_key" in filters
    assert "password" in filters
    assert "credit_card" in filters
    assert set(PARAMS_FILTERS).issubset(set(filters))


def test_error_handler_removes_query_string_and_uri_and_sanitizes_context() -> None:
    """Test error_handler excludes raw query_string/uri and sanitizes copied headers/params."""
    mock_req = MagicMock()
    mock_req.headers = {
        "Authorization": "Bearer secret-token",
        "Cookie": "session=abc123xyz",
        "User-Agent": "SylvanTest/1.0",
        "Accept": "application/json",
    }
    mock_req.params = {
        "q": "sol ring",
        "admin_password": "super_secret_pw",
        "limit": "20",
    }
    mock_req.method = "GET"
    mock_req.path = "/search"
    mock_req.query_string = "q=sol+ring&admin_password=super_secret_pw"
    mock_req.uri = "/search?q=sol+ring&admin_password=super_secret_pw"

    headers_before = copy.deepcopy(mock_req.headers)
    params_before = copy.deepcopy(mock_req.params)

    test_exception = RuntimeError("Test error")

    with (
        patch.object(error_monitoring, "api_key", "test-api-key"),
        patch.object(error_monitoring, "honeybadger") as mock_honeybadger,
    ):
        error_handler(mock_req, test_exception)

        mock_honeybadger.notify.assert_called_once()
        _, kwargs = mock_honeybadger.notify.call_args

        assert kwargs["exception"] is test_exception
        context = kwargs["context"]

        # Raw query_string and uri must be removed
        assert "query_string" not in context
        assert "uri" not in context

        # Retained fields
        assert context["method"] == "GET"
        assert context["path"] == "/search"

        # Headers sanitized
        assert context["headers"]["Authorization"] == FILTERED_VALUE
        assert context["headers"]["Cookie"] == FILTERED_VALUE
        assert context["headers"]["User-Agent"] == "SylvanTest/1.0"
        assert context["headers"]["Accept"] == "application/json"

        # Params sanitized
        assert context["params"]["admin_password"] == FILTERED_VALUE
        assert context["params"]["q"] == "sol ring"
        assert context["params"]["limit"] == "20"

    # Ensure original request headers and params were not mutated
    assert mock_req.headers == headers_before
    assert mock_req.params == params_before


def test_error_handler_without_api_key_does_not_notify() -> None:
    """Test error_handler does not call honeybadger.notify when api_key is None."""
    mock_req = MagicMock()
    test_exception = ValueError("Fallback error")

    with (
        patch.object(error_monitoring, "api_key", None),
        patch.object(error_monitoring, "honeybadger") as mock_honeybadger,
    ):
        error_handler(mock_req, test_exception)
        mock_honeybadger.notify.assert_not_called()


def test_honeybadger_end_to_end_local_variable_sanitization() -> None:
    """Test full Honeybadger notification pipeline redacts sensitive local variables."""
    test_config = Configuration()
    test_config.api_key = "test-api-key"
    test_config.report_local_variables = True
    test_config.params_filters = list(PARAMS_FILTERS)
    test_config.before_notify = sanitize_notice

    hb = Honeybadger()
    hb.config = test_config

    captured_payload: dict[str, Any] = {}

    class MockConnection:
        def send_notice(self, _config: Any, notice: Any) -> str:
            nonlocal captured_payload
            captured_payload = notice.payload
            return "test-notice-id"

    hb._connection = MockConnection

    def frame_with_sensitive_locals() -> None:
        admin_password = "super_secret_admin_pass"  # noqa: F841
        HoneybadgerApiKey = "secret_hb_key"  # noqa: F841, N806
        safe_var = "retained_value"  # noqa: F841
        err_msg = "Error inside frame with sensitive variables"
        raise ValueError(err_msg)

    try:
        frame_with_sensitive_locals()
    except ValueError as exc:
        req_context = {
            "headers": {"Authorization": "Bearer tok", "User-Agent": "agent"},
            "params": {"q": "lotus", "admin_password": "pass"},
            "method": "GET",
            "path": "/search",
        }
        hb.notify(exception=exc, context=req_context)

    req_payload = captured_payload.get("request", {})
    context_payload = req_payload.get("context", {})
    locals_payload = req_payload.get("local_variables", {})

    assert context_payload["headers"]["Authorization"] == FILTERED_VALUE
    assert context_payload["headers"]["User-Agent"] == "agent"
    assert context_payload["params"]["admin_password"] == FILTERED_VALUE
    assert context_payload["params"]["q"] == "lotus"

    assert locals_payload["admin_password"] == FILTERED_VALUE
    assert locals_payload["HoneybadgerApiKey"] == FILTERED_VALUE
    assert locals_payload["safe_var"] == "retained_value"
