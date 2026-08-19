import { analyticsExport } from "./analytics.ts";
import { doctor } from "./doctor.ts";
import { networkIncidentClear, networkIncidentStatus } from "./incident.ts";
import {
  jobsCaptureFinish,
  jobsCaptureIngest,
  jobsCaptureStart,
  jobsCheck,
  jobsClassify,
  jobsDetail,
  jobsDraft,
  jobsEnrich,
  jobsFavorite,
  jobsFilter,
  jobsList,
  jobsNormalize,
  jobsRemove,
  jobsSend,
} from "./jobs.ts";
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
    jobsCaptureStart,
    jobsCaptureIngest,
    jobsCaptureFinish,
    jobsNormalize,
    jobsFilter,
    jobsEnrich,
    jobsDetail,
    jobsList,
    jobsCheck,
    jobsFavorite,
    jobsRemove,
    jobsDraft,
    jobsSend,
    jobsClassify,
  };
}
