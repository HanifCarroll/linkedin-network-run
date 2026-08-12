import { existsSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { buildIdentity } from "./identity.ts";
import { type JsonObject, objectArray, objectMap, readLegacyJson } from "./json-reader.ts";
import { readLegacySqlite } from "./sqlite-reader.ts";
import type {
  Evidence,
  MigrationProposal,
  MigrationReport,
  SnapshotCounts,
  SnapshotExpectation,
} from "./types.ts";

export const AUGUST_2_RUN = "61a78b0a-1ee5-4072-a004-4f86500a1d1c";
export const ALLISON_INCIDENT = "2e9d5b71-1ee8-41df-a338-de00accfbf53";

// Snapshot established by the read-only 2026-08-03 legacy-state audit. Each total is tied to
// its source contract so drift cannot silently broaden or reduce the migration proposal.
export const LEGACY_SNAPSHOT: SnapshotExpectation = {
  snapshotId: "network-automation-2026-08-03",
  documentedAt: "2026-08-03T10:54:00-03:00",
  durable: 1_558,
  connected: 199,
  aliases: 826,
  unresolved: 59,
  august2Unresolved: 25,
  crossWorkflowSuppressions: 6,
  sourceDocuments: [
    {
      fields: ["durable", "unresolved", "august2Unresolved"],
      source: "network.sqlite:send_ledger_entries",
      contract: "latest row per attempt_key ordered by attempted_at then rowid",
    },
    {
      fields: ["connected"],
      source: "network.sqlite:acceptance_invitations",
      contract: "accepted or first-degree latest/current relationship status",
    },
    {
      fields: ["aliases", "crossWorkflowSuppressions"],
      source: "lead-ledger.json:leads",
      contract: "explicit stable identifiers and exact sent-message suppression evidence",
    },
  ],
};

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function evidence(path: string, key: string): Evidence[] {
  return [{ path, key }];
}

function identityFrom(value: JsonObject, leadKey?: string) {
  return buildIdentity({
    profileUrl: value.profile_url,
    publicProfileUrl: value.public_profile_url,
    salesProfileUrn: value.sales_profile_urn,
    leadKey: value.lead_key ?? leadKey,
  });
}

function allowedProposal(proposal: MigrationProposal): boolean {
  switch (proposal.kind) {
    case "durable_send":
      return ["pending", "accepted", "audit-top-up"].includes(proposal.status);
    case "unresolved_send":
      return proposal.status === "audit_required" && proposal.possibleSend;
    case "connected":
      return proposal.status === "connected";
    case "stable_alias":
      return proposal.status === "alias";
    case "cross_workflow_message_sent":
      return proposal.status === "cross_workflow_message_sent";
  }
}

function isWithinRoot(path: string, root: string): boolean {
  const relation = relative(resolve(root), resolve(path));
  return relation === "" || (!relation.startsWith("..") && !relation.startsWith("/"));
}

export function buildMigrationReport(
  root: string,
  expected: SnapshotExpectation = LEGACY_SNAPSHOT,
): MigrationReport {
  const databasePath = join(root, "network.sqlite");
  const leadPath = join(root, "lead-ledger.json");
  const activePath = join(root, "active.json");
  const sql = readLegacySqlite(databasePath);
  const json = readLegacyJson(root);
  const proposals: MigrationProposal[] = [];
  const orphanIdentities: MigrationReport["orphanIdentities"] = [];
  const warnings: string[] = [];
  const validEvidence = new Map<string, Set<string>>([[databasePath, sql.evidenceKeys]]);

  for (const row of sql.latestAttempts) {
    const allowedDurable =
      row.durable === 1 && ["pending", "accepted", "audit-top-up"].includes(row.status);
    const unresolved = row.status === "pending-provisional";
    if (!allowedDurable && !unresolved) continue;
    const identity = buildIdentity({
      profileUrl: row.profile_url,
      publicProfileUrl: row.public_profile_url,
    });
    if (!identity) {
      orphanIdentities.push({
        evidence: { path: databasePath, key: `send_ledger_entries:${row.entry_id}` },
        reason: "missing_stable_identity",
      });
      continue;
    }
    proposals.push({
      kind: unresolved ? "unresolved_send" : "durable_send",
      identity,
      status: unresolved ? "audit_required" : row.status,
      runId: row.run_id,
      possibleSend: unresolved,
      evidence: evidence(databasePath, `send_ledger_entries:${row.entry_id}`),
    });
  }

  for (const row of sql.connected) {
    const identity = buildIdentity({
      profileUrl: row.profile_url,
      publicProfileUrl: row.public_profile_url,
    });
    if (!identity) {
      orphanIdentities.push({
        evidence: { path: databasePath, key: `acceptance_invitations:${row.key}` },
        reason: "missing_stable_identity",
      });
      continue;
    }
    proposals.push({
      kind: "connected",
      identity,
      status: "connected",
      runId: null,
      possibleSend: false,
      evidence: evidence(databasePath, `acceptance_invitations:${row.key}`),
    });
  }

  const leadEvidenceKeys = new Set<string>();
  validEvidence.set(leadPath, leadEvidenceKeys);
  for (const [key, lead] of objectMap(json.leadLedger.leads)) {
    leadEvidenceKeys.add(`leads.${key}`);
    const identity = identityFrom(lead, key);
    if (!identity) continue;
    const status = text(lead.status);
    const reason = text(lead.status_reason) ?? "";
    if (identity.salesNavigatorId && identity.publicProfileUrl) {
      proposals.push({
        kind: "stable_alias",
        identity,
        status: "alias",
        runId: null,
        possibleSend: false,
        evidence: evidence(leadPath, `leads.${key}`),
      });
    }
    if (
      status === "skipped" &&
      /^cross-workflow suppression: outreach message already sent to .+ \(sent\);/i.test(reason)
    ) {
      proposals.push({
        kind: "cross_workflow_message_sent",
        identity,
        status: "cross_workflow_message_sent",
        runId: null,
        possibleSend: false,
        evidence: evidence(leadPath, `leads.${key}`),
      });
    }
  }

  const runDocuments = [{ path: activePath, value: json.active }, ...json.parked];
  const allIncidents = runDocuments.flatMap(({ path, value }) => {
    const keys = new Set<string>();
    validEvidence.set(path, keys);
    return objectArray(value.browser_incidents).map((incident, index) => {
      const id = text(incident.id) ?? "missing-id";
      const key = `browser_incidents.${index}:${id}`;
      keys.add(key);
      return { path, incident, key };
    });
  });
  const allison = allIncidents.find(({ incident }) => incident.id === ALLISON_INCIDENT);
  const allisonPossibleSend = Boolean(
    allison?.incident.possible_send === true && allison.incident.status === "audit_required",
  );
  let allisonLinked = false;
  if (!allison) {
    warnings.push("Allison incident was not present in active or parked run documents");
  } else {
    const incidentIdentity = identityFrom(allison.incident);
    const proposal = incidentIdentity
      ? proposals.find(
          (candidate) =>
            candidate.kind === "unresolved_send" &&
            candidate.runId === AUGUST_2_RUN &&
            candidate.identity.canonicalKey === incidentIdentity.canonicalKey,
        )
      : null;
    if (proposal) {
      proposal.evidence.push({ path: allison.path, key: allison.key });
      allisonLinked =
        proposal.possibleSend &&
        proposal.status === "audit_required" &&
        proposal.evidence.some((item) => item.path === databasePath) &&
        proposal.evidence.some((item) => item.path === allison.path && item.key === allison.key);
    } else {
      warnings.push("Allison incident could not be linked to its August 2 unresolved identity");
    }
  }

  const counts: SnapshotCounts = {
    durable: proposals.filter((proposal) => proposal.kind === "durable_send").length,
    connected: proposals.filter((proposal) => proposal.kind === "connected").length,
    aliases: proposals.filter((proposal) => proposal.kind === "stable_alias").length,
    unresolved: proposals.filter((proposal) => proposal.kind === "unresolved_send").length,
    august2Unresolved: proposals.filter(
      (proposal) => proposal.kind === "unresolved_send" && proposal.runId === AUGUST_2_RUN,
    ).length,
    crossWorkflowSuppressions: proposals.filter(
      (proposal) => proposal.kind === "cross_workflow_message_sent",
    ).length,
  };
  const forbiddenCategories = proposals.filter((proposal) => !allowedProposal(proposal)).length;
  const invalidEvidenceReferences = proposals
    .flatMap((proposal) => proposal.evidence)
    .filter(
      (item) =>
        !isWithinRoot(item.path, root) ||
        !existsSync(item.path) ||
        !validEvidence.get(item.path)?.has(item.key),
    ).length;

  proposals.sort(
    (a, b) =>
      a.kind.localeCompare(b.kind) ||
      a.identity.canonicalKey.localeCompare(b.identity.canonicalKey) ||
      (a.runId ?? "").localeCompare(b.runId ?? ""),
  );
  for (const proposal of proposals) {
    proposal.evidence.sort((a, b) => a.path.localeCompare(b.path) || a.key.localeCompare(b.key));
  }
  orphanIdentities.sort(
    (a, b) =>
      a.evidence.path.localeCompare(b.evidence.path) ||
      a.evidence.key.localeCompare(b.evidence.key),
  );
  warnings.sort();

  const failures: string[] = [];
  for (const field of [
    "durable",
    "connected",
    "aliases",
    "unresolved",
    "august2Unresolved",
    "crossWorkflowSuppressions",
  ] as const) {
    if (counts[field] !== expected[field]) {
      failures.push(`Expected ${expected[field]} ${field}; observed ${counts[field]}`);
    }
  }
  if (!allisonPossibleSend)
    failures.push("Allison incident is not preserved as audit_required possible-send");
  if (!allisonLinked)
    failures.push("Allison incident is not linked to the exact August 2 unresolved proposal");
  if (!sql.wal.walRowsVisible)
    failures.push("Active WAL frames were not visible through the read-only SQLite connection");
  if (warnings.length !== 0) failures.push(`Expected zero warnings; observed ${warnings.length}`);
  if (orphanIdentities.length !== 0)
    failures.push(`Expected zero orphan identities; observed ${orphanIdentities.length}`);
  if (invalidEvidenceReferences !== 0)
    failures.push(`Invalid evidence references observed: ${invalidEvidenceReferences}`);
  if (forbiddenCategories !== 0)
    failures.push(`Forbidden proposal categories observed: ${forbiddenCategories}`);

  return {
    schemaVersion: 1,
    mode: "dry-run",
    sourceRoot: root,
    proposals,
    orphanIdentities,
    warnings,
    assertions: {
      expected,
      observed: {
        ...counts,
        allisonPossibleSend,
        allisonLinked,
        wal: sql.wal,
        warnings: warnings.length,
        orphanIdentities: orphanIdentities.length,
        invalidEvidenceReferences,
        forbiddenCategories,
      },
      passed: failures.length === 0,
      failures,
    },
  };
}
