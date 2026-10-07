"""Reject malformed or cost-amplifying endpoint/spec requests before provider calls."""

import pytest
from pydantic import ValidationError

from app.models.api_models import EndpointCreate, EndpointUpdate, SpecCreate, SpecUpdate
from app.models.schemas import Provider
from app.services.diff_engine import check_json_schema


@pytest.mark.parametrize("field,value", [
    ("temperature", -1), ("temperature", float("nan")),
    ("max_tokens", -1), ("max_tokens", 500000),
    ("extra_params", {"model": "unapproved-expensive-model"}),
    ("extra_params", {"extra_headers": {"x-leak": "test"}}),
])
def test_rejects_unsafe_endpoint_params(field, value):
    with pytest.raises(ValidationError):
        EndpointCreate(name="example", provider=Provider.openai, model="gpt", **{field: value})
    with pytest.raises(ValidationError):
        EndpointUpdate(**{field: value})


@pytest.mark.parametrize("field,value", [
    ("min_length", -1), ("semantic_threshold", 1.5),
    ("expected_json_schema", {"required": 1}),
    ("expected_json_schema", {"required": [1]}),
    ("input_text", "x" * 20001),
])
def test_rejects_unsafe_spec_params(field, value):
    data = {"name": "example", "input_text": "hello", field: value}
    with pytest.raises(ValidationError):
        SpecCreate(**data)
    with pytest.raises(ValidationError):
        SpecUpdate(**{field: value})


def test_existing_malformed_schema_fails_closed():
    assert check_json_schema("{}", {"required": 1}) is False
    assert check_json_schema("{}", {"required": [1]}) is False
