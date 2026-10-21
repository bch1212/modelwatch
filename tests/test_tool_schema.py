"""Schema fingerprint tests.

Ported from ToolPulse. A wrong fingerprint causes false schema-drift alerts.
The cases run against the API copy and the customer SDK copy.
"""

import pytest

from app.services import tool_schema as api_schema
from modelwatch import schema as sdk_schema


MODULES = [api_schema, sdk_schema]


@pytest.mark.parametrize("schema", MODULES)
def test_same_shape_same_fingerprint(schema):
    a = {"name": "Alice", "age": 30, "tags": ["admin"]}
    b = {"name": "Bob", "age": 99, "tags": ["user", "beta"]}
    assert schema.fingerprint(a) == schema.fingerprint(b)


@pytest.mark.parametrize("schema", MODULES)
def test_different_keys_different_fingerprint(schema):
    a = {"name": "Alice", "age": 30}
    b = {"name": "Alice", "email": "a@b.com"}
    assert schema.fingerprint(a) != schema.fingerprint(b)


@pytest.mark.parametrize("schema", MODULES)
def test_different_value_types_different_fingerprint(schema):
    a = {"id": 123}
    b = {"id": "abc"}
    assert schema.fingerprint(a) != schema.fingerprint(b)


@pytest.mark.parametrize("schema", MODULES)
def test_key_order_doesnt_matter(schema):
    a = {"a": 1, "b": 2, "c": 3}
    b = {"c": 3, "a": 1, "b": 2}
    assert schema.fingerprint(a) == schema.fingerprint(b)


@pytest.mark.parametrize("schema", MODULES)
def test_nested_structures(schema):
    a = {"users": [{"id": 1, "name": "x"}]}
    b = {"users": [{"id": 99, "name": "y"}, {"id": 100, "name": "z"}]}
    assert schema.fingerprint(a) == schema.fingerprint(b)


@pytest.mark.parametrize("schema", MODULES)
def test_empty_list_distinct_from_populated(schema):
    a = {"items": []}
    b = {"items": [{"id": 1}]}
    assert schema.fingerprint(a) != schema.fingerprint(b)


@pytest.mark.parametrize("schema", MODULES)
def test_none_value(schema):
    a = {"x": None}
    b = {"x": "string"}
    assert schema.fingerprint(a) != schema.fingerprint(b)


@pytest.mark.parametrize("schema", MODULES)
def test_bool_vs_int_distinct(schema):
    assert schema.fingerprint({"x": True}) != schema.fingerprint({"x": 1})


@pytest.mark.parametrize("schema", MODULES)
def test_heterogeneous_list_merging(schema):
    a = [{"id": 1}, {"id": 2, "extra": "x"}]
    b = [{"id": 5, "extra": "y"}, {"id": 6}]
    assert schema.fingerprint(a) == schema.fingerprint(b)


@pytest.mark.parametrize("schema", MODULES)
def test_shape_diff_added_field(schema):
    old = schema.extract_shape({"id": 1})
    new = schema.extract_shape({"id": 1, "name": "x"})
    diff = schema.shape_diff(old, new)
    assert any("name" in item for item in diff["added"])


@pytest.mark.parametrize("schema", MODULES)
def test_shape_diff_removed_field(schema):
    old = schema.extract_shape({"id": 1, "name": "x"})
    new = schema.extract_shape({"id": 1})
    diff = schema.shape_diff(old, new)
    assert any("name" in item for item in diff["removed"])


@pytest.mark.parametrize("schema", MODULES)
def test_shape_diff_type_change(schema):
    old = schema.extract_shape({"id": 1})
    new = schema.extract_shape({"id": "abc"})
    diff = schema.shape_diff(old, new)
    assert any("id" in item for item in diff["changed"])


def test_api_and_sdk_fingerprints_match():
    obj = [{"id": 1, "ok": True}, {"id": 2, "extra": None}]
    assert api_schema.fingerprint(obj) == sdk_schema.fingerprint(obj)
    assert api_schema.extract_shape(obj) == sdk_schema.extract_shape(obj)
    assert api_schema.fingerprint(obj) == api_schema.fingerprint_of_shape(api_schema.extract_shape(obj))


def test_sanitize_shape_tree_drops_values_and_oversized_labels():
    kept = api_schema.sanitize_shape_tree({"id": "int", "name": "str"})
    assert kept == {"id": "int", "name": "str"}
    assert api_schema.sanitize_shape_tree({"note": "x" * 500}) is None
    assert api_schema.sanitize_shape_tree({"secret": 12345}) is None


def test_sanitize_tags_keeps_scalars_only():
    tags = api_schema.sanitize_tags({
        "region": "us-east",
        "attempt": 2,
        "ok": True,
        "nested": {"nope": True},
        "blob": "y" * 200,
    })
    assert tags["region"] == "us-east"
    assert tags["attempt"] == 2
    assert tags["ok"] is True
    assert "nested" not in tags
    assert len(tags["blob"]) == 80
