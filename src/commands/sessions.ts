import { chmod, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { CliError } from "../core/errors.ts";
import { PlaywriterClient } from "../playwriter/client.ts";
import type { SessionInfo } from "../playwriter/types.ts";
import type { PlaywriterSessionSelection } from "./types.ts";

export type SessionWorkflow = "network" | "analytics";

export type SessionClient = {
  readonly listSessions: () => Promise<readonly SessionInfo[]>;
  readonly createSession: () => Promise<number>;
};

export type SessionResolutionRequest = {
  readonly workflow: SessionWorkflow;
  readonly selection: PlaywriterSessionSelection;
  readonly stateDir: string;
  readonly playwriterBin: string;
};

export type SessionResolutionDependencies = {
  readonly createClient?: (request: SessionResolutionRequest) => SessionClient;
  readonly createId?: () => string;
};

type SessionBinding = {
  readonly schemaVersion: 1;
  readonly kind: "playwriter_session_binding";
  readonly workflow: SessionWorkflow;
  readonly sessionId: number;
};

const defaultCreateClient = (request: SessionResolutionRequest): SessionClient =>
  new PlaywriterClient({
    executable: request.playwriterBin,
    invocationRoot: join(request.stateDir, "receipts", "playwriter", request.workflow),
    stateDir: request.stateDir,
  });

export async function resolvePlaywriterSession(
  request: SessionResolutionRequest,
  dependencies: SessionResolutionDependencies = {},
): Promise<number> {
  if (request.selection !== "auto") return request.selection;

  try {
    const binding = await readBinding(request.stateDir, request.workflow);
    const otherBinding = await readBinding(request.stateDir, otherWorkflow(request.workflow));
    const client = (dependencies.createClient ?? defaultCreateClient)(request);
    const active = await client.listSessions();

    if (binding !== null && active.some((session) => session.id === binding.sessionId)) {
      assertDedicated(binding.sessionId, request.workflow, otherBinding);
      return binding.sessionId;
    }

    const sessionId = await client.createSession();
    assertSessionId(sessionId);
    assertDedicated(sessionId, request.workflow, otherBinding);
    const verified = await client.listSessions();
    if (!verified.some((session) => session.id === sessionId)) {
      throw new TypeError("new Playwriter session was not present in the active session list");
    }

    await writeBinding(
      request.stateDir,
      {
        schemaVersion: 1,
        kind: "playwriter_session_binding",
        workflow: request.workflow,
        sessionId,
      },
      dependencies.createId ?? (() => crypto.randomUUID()),
    );
    return sessionId;
  } catch (error) {
    if (error instanceof CliError) throw error;
    throw new CliError(
      "PLAYWRITER_SESSION_RESOLUTION_FAILED",
      error instanceof Error ? error.message : String(error),
      { details: { workflow: request.workflow }, exitCode: request.workflow === "network" ? 4 : 5 },
    );
  }
}

function bindingPath(stateDir: string, workflow: SessionWorkflow): string {
  return join(stateDir, "sessions", `${workflow}.json`);
}

async function readBinding(
  stateDir: string,
  workflow: SessionWorkflow,
): Promise<SessionBinding | null> {
  let text: string;
  try {
    text = await readFile(bindingPath(stateDir, workflow), "utf8");
  } catch (error) {
    if (hasCode(error, "ENOENT")) return null;
    throw error;
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new TypeError(`${workflow} Playwriter session binding is not valid JSON`);
  }
  if (!isRecord(value)) {
    throw new TypeError(`${workflow} Playwriter session binding must be an object`);
  }
  const keys = Object.keys(value).sort();
  if (
    JSON.stringify(keys) !==
      JSON.stringify(["kind", "schemaVersion", "sessionId", "workflow"].sort()) ||
    value.schemaVersion !== 1 ||
    value.kind !== "playwriter_session_binding" ||
    value.workflow !== workflow
  ) {
    throw new TypeError(`${workflow} Playwriter session binding violates its exact contract`);
  }
  assertSessionId(value.sessionId);
  return {
    schemaVersion: 1,
    kind: "playwriter_session_binding",
    workflow,
    sessionId: value.sessionId,
  };
}

export async function readSessionBinding(
  stateDir: string,
  workflow: SessionWorkflow,
): Promise<{ readonly sessionId: number } | null> {
  const binding = await readBinding(stateDir, workflow);
  return binding === null ? null : { sessionId: binding.sessionId };
}

async function writeBinding(
  stateDir: string,
  binding: SessionBinding,
  createId: () => string,
): Promise<void> {
  const directory = join(stateDir, "sessions");
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const destination = bindingPath(stateDir, binding.workflow);
  const temporary = join(directory, `.${binding.workflow}.${createId()}.tmp`);
  try {
    await writeFile(temporary, `${JSON.stringify(binding)}\n`, {
      encoding: "utf8",
      flag: "wx",
      mode: 0o600,
    });
    await rename(temporary, destination);
    await chmod(destination, 0o600);
  } catch (error) {
    await rm(temporary, { force: true });
    throw error;
  }
}

function assertDedicated(
  sessionId: number,
  workflow: SessionWorkflow,
  otherBinding: SessionBinding | null,
): void {
  if (otherBinding?.sessionId === sessionId) {
    throw new TypeError(
      `${workflow} Playwriter session ${sessionId} is already bound to ${otherBinding.workflow}`,
    );
  }
}

function assertSessionId(value: unknown): asserts value is number {
  if (!Number.isSafeInteger(value) || typeof value !== "number" || value < 1) {
    throw new TypeError("Playwriter session binding contains an invalid session ID");
  }
}

function otherWorkflow(workflow: SessionWorkflow): SessionWorkflow {
  return workflow === "network" ? "analytics" : "network";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasCode(error: unknown, code: string): boolean {
  return isRecord(error) && error.code === code;
}
