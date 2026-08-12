import { analyticsExport } from "./analytics.ts";
import { doctor } from "./doctor.ts";
import { networkIncidentClear, networkIncidentStatus } from "./incident.ts";
import { migrationDryRun } from "./migration.ts";
import {
  networkReconcile,
  networkReport,
  networkRunEnd,
  networkStatus,
  networkTick,
} from "./network.ts";
import { networkSessionReset } from "./session-reset.ts";
import type { CliOperations } from "./types.ts";

export function createDefaultOperations(): CliOperations {
  return {
    doctor,
    networkStatus,
    networkReport,
    networkTick,
    networkReconcile,
    networkRunEnd,
    networkSessionReset,
    networkIncidentStatus,
    networkIncidentClear,
    analyticsExport,
    migrationDryRun,
  };
}
