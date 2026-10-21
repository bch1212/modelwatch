import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { DEFAULT_ENDPOINT, INGEST_PATH, ingestHeaders, ingestUrl } from "./reporter.ts";

describe("reporter target", () => {
  it("posts to the ModelWatch API with a bearer token", () => {
    assert.equal(DEFAULT_ENDPOINT, "https://api.modelwatch.app");
    assert.equal(DEFAULT_ENDPOINT.includes("toolpulse"), false);
    assert.equal(ingestUrl(DEFAULT_ENDPOINT), "https://api.modelwatch.app/api/tools/ingest");
    assert.equal(INGEST_PATH, "/api/tools/ingest");
    assert.equal(ingestUrl(""), null);
    assert.equal(ingestUrl("ftp://api.modelwatch.app"), null);
    assert.deepEqual(ingestHeaders("mw_test"), {
      "Content-Type": "application/json",
      Authorization: "Bearer mw_test",
    });
  });
});
