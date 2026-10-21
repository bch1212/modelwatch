import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { monitor } from "./monitor.ts";

describe("monitor", () => {
  it("returns sync function results unchanged", () => {
    const add = monitor((a: number, b: number) => a + b);
    assert.equal(add(2, 3), 5);
  });

  it("propagates sync exceptions", () => {
    const boom = monitor(() => {
      throw new Error("expected");
    });
    assert.throws(() => boom(), /expected/);
  });

  it("returns async function results unchanged", async () => {
    const addAsync = monitor(async (a: number, b: number) => a + b);
    assert.equal(await addAsync(2, 3), 5);
  });

  it("propagates async rejections", async () => {
    const boomAsync = monitor(async () => {
      throw new Error("expected");
    });
    await assert.rejects(boomAsync(), /expected/);
  });
});
