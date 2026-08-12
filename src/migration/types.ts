export type Evidence = {
  path: string;
  key: string;
};

export type Identity = {
  canonicalKey: string;
  salesNavigatorId: string | null;
  publicProfileUrl: string | null;
  leadKey: string | null;
};

export type MigrationProposal = {
  kind:
    | "durable_send"
    | "unresolved_send"
    | "connected"
    | "stable_alias"
    | "cross_workflow_message_sent";
  identity: Identity;
  status: string;
  runId: string | null;
  possibleSend: boolean;
  evidence: Evidence[];
};

export type SnapshotCounts = {
  durable: number;
  connected: number;
  aliases: number;
  unresolved: number;
  august2Unresolved: number;
  crossWorkflowSuppressions: number;
};

export type SnapshotExpectation = SnapshotCounts & {
  snapshotId: string;
  documentedAt: string;
  sourceDocuments: Array<{
    fields: Array<keyof SnapshotCounts>;
    source: string;
    contract: string;
  }>;
};

export type WalEvidence = {
  journalMode: string;
  walPath: string;
  walFilePresent: boolean;
  walFrameCount: number;
  walRowsVisible: boolean;
};

export type MigrationAssertions = {
  expected: SnapshotExpectation;
  observed: SnapshotCounts & {
    allisonPossibleSend: boolean;
    allisonLinked: boolean;
    wal: WalEvidence;
    warnings: number;
    orphanIdentities: number;
    invalidEvidenceReferences: number;
    forbiddenCategories: number;
  };
  passed: boolean;
  failures: string[];
};

export type MigrationReport = {
  schemaVersion: 1;
  mode: "dry-run";
  sourceRoot: string;
  proposals: MigrationProposal[];
  orphanIdentities: Array<{ evidence: Evidence; reason: string }>;
  warnings: string[];
  assertions: MigrationAssertions;
};
