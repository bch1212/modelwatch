"""Tests for the diff computation engine."""

import pytest
from app.services.diff_engine import (
    detect_format,
    is_refusal,
    cosine_similarity,
    compute_diff,
    check_json_schema,
)


class TestDetectFormat:
    def test_json_object(self):
        assert detect_format('{"key": "value"}') == "json"

    def test_json_array(self):
        assert detect_format('[1, 2, 3]') == "json"

    def test_markdown(self):
        assert detect_format("## Hello\n\nSome text **bold**") == "markdown"

    def test_markdown_code_block(self):
        assert detect_format("Here is code:\n```python\nprint('hi')\n```") == "markdown"

    def test_plain(self):
        assert detect_format("Just some plain text here.") == "plain"

    def test_invalid_json_not_json(self):
        assert detect_format("{not valid json}") != "json"


class TestIsRefusal:
    def test_refusal_cant(self):
        assert is_refusal("I can't help with that request.")

    def test_refusal_as_ai(self):
        assert is_refusal("As an AI language model, I cannot do that.")

    def test_refusal_sorry(self):
        assert is_refusal("I'm sorry, but I'm not able to assist with that.")

    def test_not_refusal(self):
        assert not is_refusal("Here is the answer you requested: 42")

    def test_not_refusal_normal(self):
        assert not is_refusal("The capital of France is Paris.")


class TestCosineSimilarity:
    def test_identical(self):
        v = [1.0, 0.0, 0.5]
        assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-6)

    def test_orthogonal(self):
        assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0, abs=1e-6)

    def test_opposite(self):
        assert cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0, abs=1e-6)

    def test_zero_vector(self):
        assert cosine_similarity([0, 0], [1, 1]) == 0.0


class TestCheckJsonSchema:
    def test_required_keys_present(self):
        assert check_json_schema(
            '{"name": "Alice", "age": 30}',
            {"required": ["name", "age"]},
        )

    def test_required_keys_missing(self):
        assert not check_json_schema(
            '{"name": "Alice"}',
            {"required": ["name", "age"]},
        )

    def test_invalid_json(self):
        assert not check_json_schema("not json", {"required": ["name"]})


class TestComputeDiff:
    def test_no_drift(self):
        baseline = "The capital of France is Paris."
        emb = [0.1] * 128
        result = compute_diff(
            output=baseline,
            baseline_output=baseline,
            baseline_embedding=emb,
            output_embedding=emb,
        )
        assert result.format_match is True
        assert result.length_diff_pct == pytest.approx(0.0)
        assert result.semantic_similarity == pytest.approx(1.0, abs=1e-4)
        assert result.refusal_detected is False
        assert result.drift_score < 0.05

    def test_format_drift(self):
        result = compute_diff(
            output='{"answer": "Paris"}',
            baseline_output="The capital of France is Paris.",
            baseline_embedding=None,
            output_embedding=None,
            expected_format="plain",
        )
        assert result.format_match is False
        assert result.drift_score > 0.0

    def test_refusal_drift(self):
        result = compute_diff(
            output="I can't help with that request.",
            baseline_output="The capital of France is Paris.",
            baseline_embedding=None,
            output_embedding=None,
        )
        assert result.refusal_detected is True
        assert result.drift_score > 0.1

    def test_length_drift(self):
        baseline = "Short answer."
        output = "This is a much much much much much much much much much much much longer answer than before " * 5
        result = compute_diff(
            output=output,
            baseline_output=baseline,
            baseline_embedding=None,
            output_embedding=None,
        )
        assert abs(result.length_diff_pct) > 0.3
        assert result.drift_score > 0.0

    def test_contains_check_fail(self):
        result = compute_diff(
            output="Some output without the keyword.",
            baseline_output="Baseline with magic_word in it.",
            baseline_embedding=None,
            output_embedding=None,
            expected_contains=["magic_word"],
        )
        assert result.contains_pass is False

    def test_not_contains_check_fail(self):
        result = compute_diff(
            output="This output contains forbidden_term here.",
            baseline_output="Baseline output.",
            baseline_embedding=None,
            output_embedding=None,
            expected_not_contains=["forbidden_term"],
        )
        assert result.not_contains_pass is False

    def test_semantic_drift_low_similarity(self):
        # Simulate low similarity with different embeddings
        emb1 = [1.0, 0.0, 0.0] * 10
        emb2 = [0.0, 1.0, 0.0] * 10
        result = compute_diff(
            output="Completely different answer.",
            baseline_output="Original answer.",
            baseline_embedding=emb1,
            output_embedding=emb2,
            semantic_threshold=0.9,
        )
        assert result.semantic_similarity < 0.1
        assert result.drift_score > 0.3

    def test_no_embeddings_graceful(self):
        result = compute_diff(
            output="Some output.",
            baseline_output="Some output.",
            baseline_embedding=None,
            output_embedding=None,
        )
        # Without embeddings, semantic_similarity defaults to 1.0
        assert result.semantic_similarity == 1.0
