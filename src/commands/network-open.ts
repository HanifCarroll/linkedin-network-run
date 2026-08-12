import { join } from "node:path";
import { CliError } from "../core/errors.ts";
import { SOURCES } from "../network/config.ts";
import { PlaywriterClient } from "../playwriter/client.ts";
import { invokeNetworkStep } from "../playwriter/network.ts";
import { networkSourceContract } from "../playwriter/source-capture.ts";
import type { InvocationResult } from "../playwriter/types.ts";
import { resolvePlaywriterSession } from "./sessions.ts";
import type { NetworkOpenInput } from "./types.ts";

const SENT_URL = "https://www.linkedin.com/mynetwork/invitation-manager/sent/";

export type NetworkOpenDependencies = {
  readonly resolveSession?: typeof resolvePlaywriterSession;
  readonly createClient?: (input: NetworkOpenInput) => PlaywriterClient;
  readonly invoke?: (
    client: PlaywriterClient,
    command: "navigate-sent-list" | "navigate-candidate-results",
    sessionId: number,
    input: Parameters<typeof invokeNetworkStep>[3],
  ) => Promise<InvocationResult>;
};

const defaultDependencies: Required<NetworkOpenDependencies> = {
  resolveSession: resolvePlaywriterSession,
  createClient: (input) =>
    new PlaywriterClient({
      executable: input.playwriterBin,
      invocationRoot: join(input.stateDir, "receipts", "playwriter", "network"),
      stateDir: input.stateDir,
    }),
  invoke: invokeNetworkStep,
};

export async function networkOpen(
  input: NetworkOpenInput,
  dependencies: NetworkOpenDependencies = {},
): Promise<unknown> {
  const resolveSession = dependencies.resolveSession ?? defaultDependencies.resolveSession;
  const createClient = dependencies.createClient ?? defaultDependencies.createClient;
  const invoke = dependencies.invoke ?? defaultDependencies.invoke;

  const sessionId = await resolveSession({
    workflow: "network",
    selection: input.sessionId,
    stateDir: input.stateDir,
    playwriterBin: input.playwriterBin,
    warn: (message) => console.error(`[network] ${message}`),
  });
  const client = createClient(input);

  if (input.page === "sent") {
    const invocation = await invoke(client, "navigate-sent-list", sessionId, { url: SENT_URL });
    return {
      command: "network open",
      page: "sent" as const,
      sessionId,
      url: SENT_URL,
      invocationId: invocation.receipt.invocationId,
      outcome: invocation.receipt.outcome,
    };
  }

  const sourceId = input.sourceId ?? SOURCES[0].id;
  const source = SOURCES.find((candidate) => candidate.id === sourceId);
  if (source === undefined) {
    throw new CliError("INVALID_ARGUMENT", `unknown network source: ${sourceId}`, {
      exitCode: 2,
      details: { knownSources: SOURCES.map((candidate) => candidate.id) },
    });
  }
  const contract = networkSourceContract(source.id);
  const invocation = await invoke(client, "navigate-candidate-results", sessionId, {
    url: contract.searchUrl,
    sourceContract: contract,
  });
  return {
    command: "network open",
    page: "search" as const,
    sourceId: source.id,
    sessionId,
    url: contract.searchUrl,
    invocationId: invocation.receipt.invocationId,
    outcome: invocation.receipt.outcome,
  };
}
