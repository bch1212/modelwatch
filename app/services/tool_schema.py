"""Response-shape fingerprinting for tool schema drift.

Hashes the structure of a tool response, not its values. The same algorithm
lives in the customer SDK (`sdk/modelwatch/schema.py`). `tests/test_tool_schema.py`
runs the fingerprint cases against both copies.

A change in fingerprint means the tool's response shape changed — the kind of
silent breakage an agent will otherwise pass downstream.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


_MAX_DEPTH = 16
_MAX_NODES = 400
_MAX_KEYS = 50
_MAX_LABEL = 64
_MAX_LIST = 8


def fingerprint(obj: Any) -> str:
    """16-char stable shape fingerprint of any JSON-serializable object."""
    return fingerprint_of_shape(extract_shape(obj))


def fingerprint_of_shape(shape: Any) -> str:
    """Hash an already-extracted shape tree. Do not run this on raw values."""
    canonical = json.dumps(shape, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def extract_shape(obj: Any) -> Any:
    """Recursively extract the shape of an object as a tree of type names."""
    if isinstance(obj, dict):
        return {k: extract_shape(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        if not obj:
            return ["__empty__"]
        merged = extract_shape(obj[0])
        for item in obj[1:]:
            merged = _merge_shapes(merged, extract_shape(item))
        return [merged]
    if isinstance(obj, bool):
        return "bool"
    if isinstance(obj, int):
        return "int"
    if isinstance(obj, float):
        return "float"
    if isinstance(obj, str):
        return "str"
    if obj is None:
        return "null"
    return type(obj).__name__


def _merge_shapes(a: Any, b: Any) -> Any:
    """Merge two shape trees so optional/heterogeneous fields surface."""
    if a == b:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        merged: dict[str, Any] = {}
        for key in sorted(set(a.keys()) | set(b.keys())):
            if key in a and key in b:
                merged[key] = _merge_shapes(a[key], b[key])
            elif key in a:
                merged[key] = ["optional", a[key]]
            else:
                merged[key] = ["optional", b[key]]
        return merged
    if isinstance(a, list) and isinstance(b, list):
        if not a:
            return b
        if not b:
            return a
        return [_merge_shapes(a[0], b[0])]
    return ["union", sorted([str(a), str(b)])]


def shape_diff(old_shape_obj: Any, new_shape_obj: Any) -> dict[str, list[str]]:
    """Human-readable diff between two extracted shape trees.

    Keys are 'added', 'removed', and 'changed'. Each value is a list of
    dotted-path descriptions.
    """
    diff: dict[str, list[str]] = {"added": [], "removed": [], "changed": []}
    _walk_diff(old_shape_obj, new_shape_obj, "", diff)
    return diff


def _walk_diff(a: Any, b: Any, path: str, diff: dict[str, list[str]]) -> None:
    if a == b:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for key in set(a.keys()) | set(b.keys()):
            sub_path = f"{path}.{key}" if path else key
            if key not in a:
                diff["added"].append(f"{sub_path}: {_describe(b[key])}")
            elif key not in b:
                diff["removed"].append(f"{sub_path}: {_describe(a[key])}")
            else:
                _walk_diff(a[key], b[key], sub_path, diff)
        return
    if isinstance(a, list) and isinstance(b, list) and a and b:
        _walk_diff(a[0], b[0], f"{path}[]", diff)
        return
    diff["changed"].append(f"{path}: {_describe(a)} -> {_describe(b)}")


def _describe(shape: Any) -> str:
    if isinstance(shape, str):
        return shape
    if isinstance(shape, list):
        return f"list<{_describe(shape[0]) if shape else 'unknown'}>"
    if isinstance(shape, dict):
        return f"object({len(shape)} keys)"
    return str(shape)


def sanitize_shape_tree(node: Any) -> Any | None:
    """Keep a client-supplied shape tree only when it is type labels.

    Returns None when the tree is too large or contains values. Callers then
    store the fingerprint alone.
    """
    try:
        return _sanitize(node, 0, [0])
    except ValueError:
        return None


def _sanitize(node: Any, depth: int, counter: list[int]) -> Any:
    if depth > _MAX_DEPTH or counter[0] >= _MAX_NODES:
        raise ValueError("shape too large")
    counter[0] += 1
    if isinstance(node, str):
        if len(node) > _MAX_LABEL:
            raise ValueError("shape label too long")
        return node
    if isinstance(node, list):
        if len(node) > _MAX_LIST:
            raise ValueError("shape list too wide")
        return [_sanitize(item, depth + 1, counter) for item in node]
    if isinstance(node, dict):
        if len(node) > _MAX_KEYS:
            raise ValueError("shape object too wide")
        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValueError("invalid shape key")
            cleaned[key] = _sanitize(value, depth + 1, counter)
        return cleaned
    raise ValueError("shape node must be a type label")


def sanitize_tags(tags: Any) -> dict:
    """Keep a small set of scalar tags. Drop anything else."""
    if not isinstance(tags, dict):
        return {}
    clean: dict[str, Any] = {}
    for key, value in tags.items():
        if len(clean) >= 16:
            break
        if not isinstance(key, str) or not key.strip() or len(key) > 40:
            continue
        if isinstance(value, bool):
            clean[key] = value
        elif isinstance(value, int):
            clean[key] = value
        elif isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                continue
            clean[key] = value
        elif isinstance(value, str):
            clean[key] = value[:80]
        elif value is None:
            clean[key] = None
    return clean
