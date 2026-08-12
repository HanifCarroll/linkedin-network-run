import { Database } from "bun:sqlite";
import { describe, expect, test } from "bun:test";
import { rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openDatabase } from "../../src/db/database.ts";
import { runMigrations } from "../../src/db/migrations.ts";

const EXPECTED_TABLES = [
  "audit_baselines",
  "audit_snapshots",
  "causal_records",
  "daily_runs",
  "events",
  "people",
  "person_aliases",
  "reconciliation_attempts",
  "reconciliations",
  "relationship_facts",
  "reservoir_entries",
  "schema_migrations",
  "send_attempts",
  "source_observations",
] as const;

describe("database domain", () => {
  test("applies the complete schema idempotently", () => {
    const database = new Database(":memory:");
    database.exec("PRAGMA foreign_keys = ON");
    const first = runMigrations(database);
    const second = runMigrations(database);
    expect(first).toEqual({
      applied: ["initial", "baseline_optional"],
      currentVersion: 2,
    });
    expect(second).toEqual({ applied: [], currentVersion: 2 });
    const tables = database
      .query<{ name: string }, []>(
        `SELECT name FROM sqlite_master
         WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
         ORDER BY name`,
      )
      .all()
      .map((row) => row.name);
    expect(tables).toEqual([...EXPECTED_TABLES]);
    expect(tables).not.toContain("source_contracts");

    const triggers = database
      .query<{ count: number }, []>(
        "SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'trigger'",
      )
      .get()?.count;
    expect(triggers).toBe(0);

    const indexes = database
      .query<{ name: string }, []>(
        `SELECT name FROM sqlite_master
         WHERE type = 'index' AND sql IS NOT NULL
         ORDER BY name`,
      )
      .all()
      .map((row) => row.name);
    expect(indexes).toEqual([
      "audit_baselines_one_per_run",
      "events_dedupe_key_unique",
      "one_active_attempt_per_person",
      "relationship_facts_person_idx",
    ]);
    database.close();
  });

  test("persists WAL-backed state across restart", () => {
    const path = join(tmpdir(), `linkedin-tools-next-${crypto.randomUUID()}.sqlite`);
    const first = openDatabase(path);
    first.database
      .query(
        "INSERT INTO events (id, type, payload_json, occurred_at) VALUES ('e1', 'test', '{}', '2026-08-03T00:00:00Z')",
      )
      .run();
    first.database.close();
    const second = openDatabase(path);
    expect(
      second.database.query<{ type: string }, []>("SELECT type FROM events WHERE id = 'e1'").get()
        ?.type,
    ).toBe("test");
    second.database.close();
    for (const suffix of ["", "-wal", "-shm"]) rmSync(`${path}${suffix}`, { force: true });
  });
});
