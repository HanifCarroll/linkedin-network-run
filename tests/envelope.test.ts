import { describe, expect, test } from "bun:test";
import { failure, success } from "../src/core/envelope.ts";

describe("JSON envelopes", () => {
  test("wraps successful data", () => {
    expect(success({ value: 1 })).toEqual({ ok: true, data: { value: 1 } });
  });

  test("wraps structured errors", () => {
    expect(failure({ code: "NOPE", message: "No." })).toEqual({
      ok: false,
      error: { code: "NOPE", message: "No." },
    });
  });
});
