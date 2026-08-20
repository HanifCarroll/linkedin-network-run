import { analyticsExport } from "./analytics.ts";
import { doctor } from "./doctor.ts";
import { networkIncidentClear, networkIncidentStatus } from "./incident.ts";
import {
  jobsApplied,
  jobsCaptureFinish,
  jobsCaptureIngest,
  jobsCaptureStart,
  jobsCheck,
  jobsClassify,
  jobsDraft,
  jobsDraftNext,
  jobsEnrichNext,
  jobsEnrichRecord,
  jobsFavorite,
  jobsFilter,
  jobsHubSpotNext,
  jobsHubSpotRecord,
  jobsList,
  jobsNormalize,
  jobsRemove,
  jobsSend,
  jobsTriageNext,
  jobsTriageRecord,
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
import { salesnav } from "./salesnav.ts";
import { networkSessionReset } from "./session-reset.ts";
import type { CliOperations } from "./types.ts";

export function createDefaultOperations(): CliOperations {
  return {
    salesnav,
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
    jobsHubSpotNext,
    jobsHubSpotRecord,
    jobsEnrichNext,
    jobsEnrichRecord,
    jobsList,
    jobsCheck,
    jobsFavorite,
    jobsRemove,
    jobsDraft,
    jobsDraftNext,
    jobsApplied,
    jobsSend,
    jobsClassify,
    jobsTriageNext,
    jobsTriageRecord,
  };
}
