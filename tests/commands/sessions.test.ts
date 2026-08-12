import { describe, expect, test } from "bun:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  resolvePlaywriterSession,
  type SessionClient,
  type SessionResolutionRequest,
} from "../../src/commands/sessions.ts";
import type { SessionInfo } from "../../src/playwriter/types.ts";

describe("Playwriter command session resolution", () => {
  test("creates, verifies, binds, and then reuses one dedicated session per workflow", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-command-sessions-"));
    const active = new Set<number>();
    let nextSessionId = 40;
    let createCalls = 0;
    const createClient = (_request: SessionResolutionRequest): SessionClient => ({
      listSessions: async () => [...active].map(session),
      createSession: async () => {
        createCalls += 1;
        nextSessionId += 1;
        active.add(nextSessionId);
        return nextSessionId;
      },
    });

    try {
      const network = await resolvePlaywriterSession(autoRequest(stateDir, "network"), {
        createClient,
        createId: () => "network-binding",
      });
      const analytics = await resolvePlaywriterSession(autoRequest(stateDir, "analytics"), {
        createClient,
        createId: () => "analytics-binding",
      });
      const networkRestart = await resolvePlaywriterSession(autoRequest(stateDir, "network"), {
        createClient,
      });

      expect({ network, analytics, networkRestart, createCalls }).toEqual({
        network: 41,
        analytics: 42,
        networkRestart: 41,
        createCalls: 2,
      });
      expect(
        JSON.parse(await readFile(join(stateDir, "sessions", "network.json"), "utf8")),
      ).toEqual({
        schemaVersion: 1,
        kind: "playwriter_session_binding",
        workflow: "network",
        sessionId: 41,
      });
      expect(
        JSON.parse(await readFile(join(stateDir, "sessions", "analytics.json"), "utf8")),
      ).toEqual({
        schemaVersion: 1,
        kind: "playwriter_session_binding",
        workflow: "analytics",
        sessionId: 42,
      });
    } finally {
      await rm(stateDir, { recursive: true, force: true });
    }
  });

  test("replaces a stale binding but never adopts an arbitrary active session", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-command-stale-session-"));
    const active = new Set<number>();
    let next = 50;
    const createClient = (): SessionClient => ({
      listSessions: async () => [...active].map(session),
      createSession: async () => {
        next += 1;
        active.add(next);
        return next;
      },
    });

    try {
      const first = await resolvePlaywriterSession(autoRequest(stateDir, "network"), {
        createClient,
        createId: () => "first",
      });
      active.delete(first);
      active.add(999);
      const replacement = await resolvePlaywriterSession(autoRequest(stateDir, "network"), {
        createClient,
        createId: () => "replacement",
      });

      expect(first).toBe(51);
      expect(replacement).toBe(52);
      expect(replacement).not.toBe(999);
    } finally {
      await rm(stateDir, { recursive: true, force: true });
    }
  });

  test("passes explicit IDs through without constructing a Playwriter client", async () => {
    let clientCreated = false;
    const result = await resolvePlaywriterSession(
      {
        workflow: "network",
        selection: 17,
        stateDir: "/tmp/unused-linkedin-state",
        playwriterBin: "/tmp/unused-playwriter",
      },
      {
        createClient: () => {
          clientCreated = true;
          throw new Error("must not construct client");
        },
      },
    );
    expect(result).toBe(17);
    expect(clientCreated).toBe(false);
  });
});

function autoRequest(
  stateDir: string,
  workflow: "network" | "analytics",
): SessionResolutionRequest {
  return {
    workflow,
    selection: "auto",
    stateDir,
    playwriterBin: "/tmp/fake-playwriter",
  };
}

function session(id: number): SessionInfo {
  return {
    id,
    browser: "Chrome",
    profile: null,
    extensionId: null,
    cwd: null,
    stateKeys: [],
  };
}
