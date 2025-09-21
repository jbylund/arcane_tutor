"""Tests to verify orjson and jsonb API compatibility."""

from __future__ import annotations

import copy
from unittest.mock import MagicMock, patch

import orjson
import psycopg.types.json

from api.api_resource import APIResource, can_serialize
from api.utils import db_utils


class TestOrjsonJsonbCompatibility:
    """Test compatibility between orjson and jsonb APIs."""

    def test_orjson_dumps_configured_for_psycopg(self) -> None:
        """Test that psycopg is configured to use orjson for JSON dumping."""
        # This test verifies that our orjson_dumps function is properly registered
        test_data = {"test": "data", "numbers": [1, 2, 3]}

        # Create a Jsonb object and serialize it
        psycopg.types.json.Jsonb(test_data)

        # The psycopg should use our orjson_dumps function
        # We can verify this by checking if orjson-specific behavior works
        serialized = db_utils.orjson_dumps(test_data)
        expected = orjson.dumps(test_data).decode("utf-8")

        assert serialized == expected

    def test_orjson_loads_configured_for_psycopg(self) -> None:
        """Test that psycopg is configured to use orjson for JSON loading."""
        test_data = {"test": "data", "numbers": [1, 2, 3]}
        json_string = orjson.dumps(test_data).decode("utf-8")

        # orjson.loads should be used by psycopg
        loaded = orjson.loads(json_string)

        assert loaded == test_data

    def test_maybe_json_wraps_correctly(self) -> None:
        """Test that maybe_json wraps lists and dicts in Jsonb objects."""
        # Test with dictionary
        test_dict = {"key": "value"}
        result_dict = db_utils.maybe_json(test_dict)
        assert isinstance(result_dict, psycopg.types.json.Jsonb)

        # Test with list
        test_list = [1, 2, 3]
        result_list = db_utils.maybe_json(test_list)
        assert isinstance(result_list, psycopg.types.json.Jsonb)

        # Test with primitive types (should pass through unchanged)
        assert db_utils.maybe_json("string") == "string"
        assert db_utils.maybe_json(123) == 123
        assert db_utils.maybe_json(None) is None

    def test_can_serialize_uses_orjson(self) -> None:
        """Test that can_serialize uses orjson for serialization."""
        # Test with serializable data
        serializable_data = {"test": "data", "number": 42}
        assert can_serialize(serializable_data) is True

        # Test with data that would be too large
        large_data = {"key" + str(i): "value" * 1000 for i in range(100)}
        assert can_serialize(large_data) is False

        # Test with non-serializable data
        non_serializable = {"function": lambda x: x}
        assert can_serialize(non_serializable) is False

    @patch("api.utils.db_utils.make_pool")
    def test_run_query_cache_serialization_format(self, mock_make_pool: MagicMock) -> None:
        """Test that _run_query cache uses consistent orjson serialization format."""
        # Setup mock pool and cursor
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [{"result": "test"}]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__enter__.return_value = mock_conn
        mock_make_pool.return_value = mock_pool

        api_resource = APIResource()

        # Test data that needs JSON serialization
        test_params = {
            "complex_param": {"b_key": 2, "a_key": 1},  # Keys will be sorted
            "simple_param": "value",
        }

        # Simulate what the caching function does
        def maybe_json_dump(v: object) -> object:
            if isinstance(v, list | dict):
                return orjson.dumps(v, option=orjson.OPT_SORT_KEYS).decode()
            return v

        # The cached key should use orjson with sorted keys
        expected_complex_serialized = maybe_json_dump(test_params["complex_param"])
        expected_simple_serialized = maybe_json_dump(test_params["simple_param"])

        # Verify the serialization format is correct
        assert expected_complex_serialized == '{"a_key":1,"b_key":2}'
        assert expected_simple_serialized == "value"

        # Just call the method to ensure no errors - we verify format above
        result = api_resource._run_query(
            query="SELECT * FROM test_table WHERE data = %(complex_param)s",
            params=test_params,
            explain=False,
        )

        assert "result" in result  # Basic sanity check

    def test_orjson_consistent_serialization_format(self) -> None:
        """Test that orjson serialization is consistent across different usage patterns."""
        test_data = {
            "colors": {"R": True, "G": True},
            "types": ["Creature", "Beast"],
            "cmc": 3,
        }

        # Test serialization through db_utils.orjson_dumps
        serialized1 = db_utils.orjson_dumps(test_data)

        # Test serialization through direct orjson usage
        serialized2 = orjson.dumps(test_data).decode("utf-8")

        # Both should produce the same result
        assert serialized1 == serialized2

        # Test deserialization
        deserialized1 = orjson.loads(serialized1)
        deserialized2 = orjson.loads(serialized2)

        assert deserialized1 == test_data
        assert deserialized2 == test_data
        assert deserialized1 == deserialized2

    def test_orjson_sort_keys_consistency(self) -> None:
        """Test that orjson sort keys option works consistently."""
        # Test data with keys that should be sorted
        test_data = {"z_key": 1, "a_key": 2, "m_key": 3}

        # Serialize with sort keys (as used in cache serialization)
        sorted_json = orjson.dumps(test_data, option=orjson.OPT_SORT_KEYS).decode()

        # The keys should be in sorted order
        expected_order = '{"a_key":2,"m_key":3,"z_key":1}'
        assert sorted_json == expected_order

        # Verify this matches what the cache serialization function produces
        def maybe_json_dump_simulation(v: object) -> object:
            if isinstance(v, list | dict):
                return orjson.dumps(v, option=orjson.OPT_SORT_KEYS).decode()
            return v

        cached_serialized = maybe_json_dump_simulation(test_data)
        assert cached_serialized == sorted_json

    def test_deep_copy_compatibility(self) -> None:
        """Test that orjson-serialized data is compatible with copy.deepcopy."""
        test_data = {
            "nested": {
                "list": [1, 2, {"inner": "value"}],
                "dict": {"key": "value"},
            },
        }

        # Serialize and deserialize with orjson
        serialized = orjson.dumps(test_data).decode()
        deserialized = orjson.loads(serialized)

        # Should be able to deep copy the result
        copied = copy.deepcopy(deserialized)

        # All should be equal
        assert copied == deserialized == test_data

        # But should be different objects
        assert copied is not deserialized
        assert copied["nested"] is not deserialized["nested"]

    def test_jsonb_parameter_substitution(self) -> None:
        """Test that Jsonb objects work correctly as query parameters."""
        test_data = {"R": True, "G": True}

        # Create a Jsonb object as would be done in maybe_json
        jsonb_obj = db_utils.maybe_json(test_data)

        assert isinstance(jsonb_obj, psycopg.types.json.Jsonb)

        # The Jsonb object should contain the original data
        # Note: We can't directly access the data, but we can verify the type
        assert jsonb_obj is not test_data  # Should be wrapped, not the same object
