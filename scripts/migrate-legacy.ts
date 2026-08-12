#!/usr/bin/env bun
import { resolve } from "node:path";
import { buildMigrationReport } from "../src/migration/index.ts";

const args = process.argv.slice(2);
const rootIndex = args.indexOf("--source-root");
const configuredRoot = rootIndex >= 0 ? args.at(rootIndex + 1) : undefined;
const root = resolve(
  configuredRoot ??
    `${process.env.HOME}/Library/Application Support/linkedin-tools/network-automation`,
);

try {
  const report = buildMigrationReport(root);
  console.log(JSON.stringify(report, null, 2));
  if (!report.assertions.passed) process.exitCode = 1;
} catch (error) {
  console.error(
    JSON.stringify(
      {
        ok: false,
        error: {
          code: "MIGRATION_DRY_RUN_FAILED",
          message: error instanceof Error ? error.message : String(error),
        },
      },
      null,
      2,
    ),
  );
  process.exitCode = 1;
}
