import { describe, expect, test } from "bun:test";
import { mkdtemp, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { changedDownloads, snapshotDownloads } from "../../src/analytics/downloads.ts";

describe("analytics download snapshots", () => {
  test("reports zero, one, and multiple changed workbooks", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-downloads-"));
    const before = await snapshotDownloads([root]);
    expect(changedDownloads(before, await snapshotDownloads([root]))).toHaveLength(0);
    await writeFile(join(root, "AggregateAnalytics_A_2026-07-20_2026-07-26.xlsx"), "one");
    expect(changedDownloads(before, await snapshotDownloads([root]))).toHaveLength(1);
    await writeFile(join(root, "AggregateAnalytics_B_2026-07-20_2026-07-26 (1).xlsx"), "two");
    expect(changedDownloads(before, await snapshotDownloads([root]))).toHaveLength(2);
  });

  test("rejects symlink workbook candidates", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-symlink-"));
    const outside = join(await mkdtemp(join(tmpdir(), "analytics-outside-")), "real.xlsx");
    await writeFile(outside, "outside");
    await symlink(outside, join(root, "AggregateAnalytics_Hanif_2026-07-20_2026-07-26.xlsx"));
    await expect(snapshotDownloads([root])).rejects.toThrow("must not be a symlink");
  });
});
