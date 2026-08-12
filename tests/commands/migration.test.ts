import { describe, expect, test } from "bun:test";
import { migrationDryRun } from "../../src/commands/migration.ts";

describe("migration command", () => {
  test("returns proposals without an apply path", async () => {
    const result = await migrationDryRun(
      { sourceRoot: "/tmp/legacy" },
      {
        buildMigrationReport: () =>
          ({
            schemaVersion: 1,
            mode: "dry-run",
            sourceRoot: "/tmp/legacy",
            proposals: [{ kind: "durable_send" }],
            orphanIdentities: [],
            warnings: [],
            assertions: { passed: true },
          }) as never,
      },
    );
    expect(result).toMatchObject({
      command: "migration dry-run",
      proposalOnly: true,
      legacyWrites: 0,
      proposalCount: 1,
    });
  });
});
