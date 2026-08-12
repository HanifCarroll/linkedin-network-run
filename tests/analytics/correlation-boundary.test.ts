import { describe, expect, test } from "bun:test";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

describe("analytics correlation boundary", () => {
  test("documents unavoidable indistinguishable concurrent-export ambiguity", async () => {
    const source = await readFile(
      join(import.meta.dir, "../../src/analytics/CORRELATION_BOUNDARY.md"),
      "utf8",
    );
    expect(source).toContain("bounded temporal association");
    expect(source).toContain("not proof of causality");
    expect(source).toMatch(/same account and same\s+period/);
    expect(source).toContain("ambiguity is unavoidable");
    expect(source).toContain("sticky `needs_reconciliation`");
    expect(source).not.toContain("exclusive_temporal_window");
  });
});
