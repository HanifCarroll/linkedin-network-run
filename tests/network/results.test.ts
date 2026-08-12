import { describe, expect, test } from "bun:test";
import { NETWORK_SOURCES } from "../../src/network/controller.ts";
import {
  type CommitSendEvidence,
  type NetworkCandidate,
  NetworkResultError,
  networkSourceContractFingerprint,
  networkTerminalFingerprint,
  parseCommitSendEvidence,
  parsePrepareSendReceipt,
  parseSentList,
  parseSourceCapture,
  parseSourceRows,
  type SendPreparationReceipt,
  type SourceCapturePayload,
} from "../../src/network/results.ts";

const candidate: NetworkCandidate = {
  sourceName: "Consulting - HubSpot Agency Ops",
  savedSearchId: "1980844577",
  searchUrl: "https://www.linkedin.com/sales/search/people?savedSearchId=1980844577",
  salesLeadUrl: "https://www.linkedin.com/sales/lead/abc",
  salesLeadId: "abc",
  name: "Ada",
  rowIdentity: "urn:abc",
};

const receiptId =
  "pwprep:prepare_12345678:0123456789abcdef0123456789abcdef:" +
  "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

function terminalCapture(
  source: (typeof NETWORK_SOURCES)[number],
  invocationId = "capture_12345678",
): SourceCapturePayload {
  const capturedAt = "2026-08-03T12:00:00.000Z";
  const navigationInvocationId = "navigate_12345678";
  const items = [
    {
      rowIdentity: "urn:li:fs_salesProfile:abc",
      name: "Ada",
      salesLeadUrl: "https://www.linkedin.com/sales/lead/abc",
    },
  ] as const;
  const cursorIdentity = "Page 1";
  const pageIdentity = `salesnav-saved-search:${source.savedSearchId}:${cursorIdentity}`;
  const sourceContractFingerprint = networkSourceContractFingerprint(source);
  return {
    schemaVersion: 1,
    kind: "network_source_capture",
    captureInvocationId: invocationId,
    capturedAt,
    sourceContract: {
      schemaVersion: 1,
      kind: "network_source_contract",
      contractVersion: 1,
      sourceId: source.id,
      sourceName: source.name,
      savedSearchId: source.savedSearchId,
      searchUrl: source.url,
      contractFingerprint: sourceContractFingerprint,
    },
    url: source.url,
    items,
    reload: {
      navigationInvocationId,
      reloadIdentity: `${navigationInvocationId}:reload`,
      reloadGeneration: 1,
      navigatedAt: capturedAt,
    },
    page: {
      stateKey: "networkCandidateResultsPage",
      url: source.url,
      resultsContainerCount: 1,
      resultsContainerVisible: true,
      ariaBusy: "false",
      progressbarCount: 0,
      alertCount: 0,
      dialogCount: 0,
      fullyLoaded: true,
      blockerFree: true,
      cursorIdentity,
      pageIdentity,
    },
    pagination: {
      navigationCount: 1,
      currentPageCount: 1,
      nextControlCount: 1,
      nextDisabled: true,
    },
    terminalEvidence: {
      schemaVersion: 1,
      kind: "network_source_terminal_observation",
      captureInvocationId: invocationId,
      observedAt: capturedAt,
      sourceId: source.id,
      sourceName: source.name,
      savedSearchId: source.savedSearchId,
      searchUrl: source.url,
      sourceContractVersion: 1,
      sourceContractFingerprint,
      terminalFingerprint: networkTerminalFingerprint({
        sourceContractFingerprint,
        searchUrl: source.url,
        pageIdentity,
        cursorIdentity,
        stableRowIds: items.map((item) => item.rowIdentity),
        rowCount: items.length,
        nextControl: "disabled",
      }),
      pageIdentity,
      cursorIdentity,
      stableRowIds: items.map((item) => item.rowIdentity),
      rowCount: items.length,
      nextControl: "disabled",
      navigationInvocationId,
      reloadIdentity: `${navigationInvocationId}:reload`,
      reloadGeneration: 1,
      navigatedAt: capturedAt,
    },
  };
}

describe("strict network results", () => {
  const source = NETWORK_SOURCES[0];
  const otherSource = NETWORK_SOURCES[1];
  if (source === undefined || otherSource === undefined) throw new Error("missing sources");

  test("accepts only exact source rows", () => {
    expect(
      parseSourceRows(
        {
          url: source.url,
          items: [
            {
              rowIdentity: "urn:li:fs_salesProfile:abc",
              name: "Ada",
              salesLeadUrl: "https://www.linkedin.com/sales/lead/abc",
            },
          ],
        },
        source,
      )[0],
    ).toMatchObject({ salesLeadId: "abc", rowOrder: 0 });
    expect(() => parseSourceRows({ url: otherSource.url, items: [] }, source)).toThrow(
      NetworkResultError,
    );
    expect(() =>
      parseSourceRows(
        {
          url: source.url,
          items: [
            {
              rowIdentity: "urn:li:fs_salesProfile:abc",
              name: "Ada",
              profileUrl: "https://www.linkedin.com/in/ada",
            },
          ],
        },
        source,
      ),
    ).toThrow("strict contract");
  });

  test("strictly validates authoritative terminal source evidence", () => {
    const capture = terminalCapture(source);
    expect(parseSourceCapture(capture, source)).toMatchObject({
      sourceId: source.id,
      rows: [{ salesLeadId: "abc" }],
      terminal: { reloadGeneration: 1, pageCursor: "Page 1" },
    });
    expect(() =>
      parseSourceCapture(
        {
          ...capture,
          terminalEvidence: { ...capture.terminalEvidence, sourceId: otherSource.id },
        },
        source,
      ),
    ).toThrow("terminal observation");
    expect(() =>
      parseSourceCapture(
        {
          ...capture,
          terminalEvidence: {
            ...capture.terminalEvidence,
            stableRowIds: [],
          },
        },
        source,
      ),
    ).toThrow("captured source page");
  });

  test("rejects partial and duplicate sent-list evidence", () => {
    expect(() =>
      parseSentList({
        peopleCount: 1,
        identities: ["x", "x"],
        names: [],
        complete: true,
        competingSenderAbsent: true,
        contradictoryEvidence: false,
      }),
    ).toThrow("unique");
    expect(() => parseSentList({ peopleCount: 1, names: [] })).toThrow(NetworkResultError);
    expect(() =>
      parseSentList({
        peopleCount: 1,
        identities: ["  padded  "],
        names: ["A"],
        complete: true,
        competingSenderAbsent: true,
        contradictoryEvidence: false,
      }),
    ).toThrow(NetworkResultError);
    expect(() =>
      parseSentList({
        peopleCount: 1,
        identities: ["ok", ""],
        names: ["A"],
        complete: true,
        competingSenderAbsent: true,
        contradictoryEvidence: false,
      }),
    ).toThrow(NetworkResultError);
    expect(() =>
      parseSentList({
        peopleCount: 1,
        identities: Array.from({ length: 2501 }, (_, index) => `id-${index}`),
        names: ["A"],
        complete: true,
        competingSenderAbsent: true,
        contradictoryEvidence: false,
      }),
    ).toThrow(NetworkResultError);
  });

  test("strictly binds prepare receipts to the attempt and exact candidate", () => {
    const receipt: SendPreparationReceipt = {
      schemaVersion: 1,
      kind: "network_send_prepared",
      receiptId,
      attemptId: "attempt-1",
      preparedAt: "2026-08-03T12:00:00Z",
      candidate,
    };
    expect(parsePrepareSendReceipt(receipt, { attemptId: "attempt-1", candidate })).toEqual(
      receipt,
    );
    expect(() =>
      parsePrepareSendReceipt(
        { ...receipt, attemptId: "attempt-2" },
        { attemptId: "attempt-1", candidate },
      ),
    ).toThrow("exact planned attempt");
    expect(() =>
      parsePrepareSendReceipt(
        { ...receipt, inferredProfile: true },
        { attemptId: "attempt-1", candidate },
      ),
    ).toThrow("strict contract");
  });

  test("strictly validates complete commit evidence", () => {
    const receipt: SendPreparationReceipt = {
      schemaVersion: 1,
      kind: "network_send_prepared",
      receiptId,
      attemptId: "attempt-1",
      preparedAt: "2026-08-03T12:00:00Z",
      candidate,
    };
    const evidence: CommitSendEvidence = {
      schemaVersion: 1,
      kind: "network_send_commit",
      receiptId,
      attemptId: "attempt-1",
      candidate,
      clickDispatched: true,
      postClickEvidence: {
        observedUrl: candidate.searchUrl,
        modalCount: 0,
        sendControlCount: 0,
        pendingCount: 1,
        capturedAt: "2026-08-03T12:00:01Z",
      },
    };
    expect(parseCommitSendEvidence(evidence, receipt)).toEqual(evidence);
    expect(() => parseCommitSendEvidence({ ...evidence, clickDispatched: false }, receipt)).toThrow(
      "exact preparation receipt",
    );
    expect(() => parseCommitSendEvidence({ unclear: true }, receipt)).toThrow("strict contract");
  });
});
