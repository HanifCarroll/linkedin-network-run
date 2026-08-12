import { CliError } from "../core/errors.ts";
import { buildMigrationReport } from "../migration/index.ts";
import type { MigrationReport } from "../migration/types.ts";
import type { MigrationDryRunInput } from "./types.ts";

export type MigrationDependencies = {
  readonly buildMigrationReport: (sourceRoot: string) => MigrationReport;
};

const defaultDependencies: MigrationDependencies = { buildMigrationReport };

export async function migrationDryRun(
  input: MigrationDryRunInput,
  dependencies: MigrationDependencies = defaultDependencies,
): Promise<unknown> {
  try {
    const report = dependencies.buildMigrationReport(input.sourceRoot);
    return {
      command: "migration dry-run",
      mode: "dry-run",
      proposalOnly: true,
      legacyWrites: 0,
      sourceRoot: input.sourceRoot,
      assertionsPassed: report.assertions.passed,
      proposalCount: report.proposals.length,
      report,
    };
  } catch (error) {
    throw new CliError(
      "MIGRATION_DRY_RUN_FAILED",
      error instanceof Error ? error.message : String(error),
      { details: { sourceRoot: input.sourceRoot }, exitCode: 6 },
    );
  }
}
