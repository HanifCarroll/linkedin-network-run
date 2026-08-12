import { Database } from "bun:sqlite";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { type MigrationResult, runMigrations } from "./migrations.ts";

export type DatabaseBootstrap = MigrationResult & {
  path: string;
};

export type OpenDatabase = {
  database: Database;
  migrations: MigrationResult;
  path: string;
};

export function defaultDatabasePath(): string {
  const configuredPath = process.env.LINKEDIN_TOOLS_DB;
  if (configuredPath === ":memory:") return configuredPath;
  return resolve(configuredPath ?? "var/linkedin-tools.db");
}

export function bootstrapDatabase(path = defaultDatabasePath()): DatabaseBootstrap {
  const opened = openDatabase(path);
  try {
    return { path, ...opened.migrations };
  } finally {
    opened.database.close();
  }
}

export function openDatabase(path = defaultDatabasePath()): OpenDatabase {
  if (path !== ":memory:") mkdirSync(dirname(path), { recursive: true });
  const database = new Database(path, { create: true });
  try {
    database.exec("PRAGMA journal_mode = WAL;");
    database.exec("PRAGMA foreign_keys = ON;");
    database.exec("PRAGMA busy_timeout = 5000;");
    return { database, migrations: runMigrations(database), path };
  } catch (error) {
    database.close();
    throw error;
  }
}

export function inTransaction<T>(database: Database, operation: () => T): T {
  return database.transaction(operation).immediate();
}
