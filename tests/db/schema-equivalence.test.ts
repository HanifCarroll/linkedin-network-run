import { Database } from "bun:sqlite";
import { describe, expect, test } from "bun:test";
import { runMigrations as runNewMigrations } from "../../src/db/migrations.ts";
import { runMigrations as runOldMigrations } from "../fixtures/pre-collapse-migrations.ts";

type ColumnShape = {
  name: string;
  type: string;
  notnull: number;
  pk: number;
};

type SchemaShape = {
  tables: Record<string, ColumnShape[]>;
  indexes: string[];
  triggers: string[];
};

function shapeOf(database: Database): SchemaShape {
  const tableNames = database
    .query<{ name: string }, []>(
      `SELECT name FROM sqlite_master
       WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
       ORDER BY name`,
    )
    .all()
    .map((row) => row.name);

  const tables: SchemaShape["tables"] = {};
  for (const name of tableNames) {
    tables[name] = database
      .query<{ name: string; type: string; notnull: number; pk: number }, []>(
        `PRAGMA table_info(${JSON.stringify(name)})`,
      )
      .all()
      .map((column) => ({
        name: column.name,
        type: column.type,
        notnull: column.notnull,
        pk: column.pk,
      }))
      .sort((left, right) => left.name.localeCompare(right.name));
  }

  const indexes = database
    .query<{ name: string }, []>(
      `SELECT name FROM sqlite_master
       WHERE type = 'index' AND sql IS NOT NULL
       ORDER BY name`,
    )
    .all()
    .map((row) => row.name);

  const triggers = database
    .query<{ name: string }, []>(
      `SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name`,
    )
    .all()
    .map((row) => row.name);

  return { tables, indexes, triggers };
}

describe("collapsed schema equivalence", () => {
  test("new schema matches pre-collapse tables, columns, and indexes (triggers excluded)", () => {
    const oldDb = new Database(":memory:");
    oldDb.exec("PRAGMA foreign_keys = ON");
    const oldResult = runOldMigrations(oldDb);
    expect(oldResult.currentVersion).toBe(11);
    const oldShape = shapeOf(oldDb);
    oldDb.close();

    const newDb = new Database(":memory:");
    newDb.exec("PRAGMA foreign_keys = ON");
    const newResult = runNewMigrations(newDb, 1);
    expect(newResult).toEqual({ applied: ["initial"], currentVersion: 1 });
    const newShape = shapeOf(newDb);
    newDb.close();

    expect(newShape.triggers).toEqual([]);
    expect(Object.keys(newShape.tables).sort()).toEqual(Object.keys(oldShape.tables).sort());
    expect(newShape.indexes).toEqual(oldShape.indexes);

    for (const tableName of Object.keys(oldShape.tables)) {
      expect(newShape.tables[tableName]).toEqual(oldShape.tables[tableName]);
    }
  });
});
