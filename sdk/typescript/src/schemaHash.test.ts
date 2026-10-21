import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { extractShape, fingerprint } from "./schemaHash.ts";

describe("fingerprint", () => {
  it("matches when shape is identical", () => {
    const a = { name: "Alice", age: 30, tags: ["admin"] };
    const b = { name: "Bob", age: 99, tags: ["user", "beta"] };
    assert.equal(fingerprint(a), fingerprint(b));
  });

  it("differs when keys differ", () => {
    assert.notEqual(fingerprint({ a: 1 }), fingerprint({ b: 1 }));
  });

  it("differs when value types differ", () => {
    assert.notEqual(fingerprint({ id: 1 }), fingerprint({ id: "x" }));
  });

  it("ignores key order", () => {
    assert.equal(fingerprint({ a: 1, b: 2 }), fingerprint({ b: 2, a: 1 }));
  });

  it("treats empty list as distinct from populated", () => {
    assert.notEqual(fingerprint({ x: [] }), fingerprint({ x: [{ id: 1 }] }));
  });

  it("merges heterogeneous list element shapes", () => {
    const a = [{ id: 1 }, { id: 2, extra: "x" }];
    const b = [{ id: 5, extra: "y" }, { id: 6 }];
    assert.equal(fingerprint(a), fingerprint(b));
  });
});

describe("extractShape", () => {
  it("normalizes primitives", () => {
    assert.equal(extractShape("hi"), "str");
    assert.equal(extractShape(42), "int");
    assert.equal(extractShape(3.14), "float");
    assert.equal(extractShape(true), "bool");
    assert.equal(extractShape(null), "null");
  });
});
