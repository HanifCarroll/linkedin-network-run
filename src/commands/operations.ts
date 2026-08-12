import { analyticsExport } from "./analytics.ts";
import { doctor } from "./doctor.ts";
import { networkIncidentClear, networkIncidentStatus } from "./incident.ts";
import { jobsDraft, jobsFavorite, jobsList, jobsSearch, jobsSend } from "./jobs.ts";
import { migrationDryRun } from "./migration.ts";
import {
  networkReconcile,
  networkReport,
  networkRunEnd,
  networkStatus,
  networkTick,
} from "./network.ts";
import { networkOpen } from "./network-open.ts";
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
    networkOpen,
    networkIncidentStatus,
    networkIncidentClear,
    analyticsExport,
    migrationDryRun,
    jobsSearch,
    jobsList,
    jobsFavorite,
    jobsDraft,
    jobsSend,
  };
}
