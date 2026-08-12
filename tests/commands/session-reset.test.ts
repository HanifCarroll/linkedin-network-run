import { describe, expect, test } from "bun:test";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openDatabase } from "../../src/db/database.ts";
import { NetworkEngine } from "../../src/network/index.ts";
import { networkSessionReset } from "../../src/commands/session-reset.ts";

const NOW = "2026-08-09T12:00:00.000Z";

async function bindNetworkSession(stateDir: string, sessionId = 6): Promise<void> {
  await mkdir(join(stateDir, "sessions"), { recursive: true });
  await writeFile(
    join(stateDir, "sessions", "network.json"),
    `${JSON.stringify({
      schemaVersion: 1,
      kind: "playwriter_session_binding",
      workflow: "network",
      sessionId,
    })}\n`,
    "utf8",
  );
}

async function seedRun(stateDir: string): Promise<string> {
  const opened = openDatabase(join(stateDir, "linkedin-tools.db"));
  try {
    const engine = new NetworkEngine(opened.database);
    return engine.openDailyRun("2026-08-09", NOW, "run-test").id;
  } finally {
    opened.database.close();
  }
}

describe("network session-reset", () => {
  test("resets the bound session and reports workflow context", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-session-reset-"));
    try {
      await bindNetworkSession(stateDir, 6);
      const runId = await seedRun(stateDir);
      const commands: string[][] = [];
      const result = await networkSessionReset(
        { stateDir, playwriterBin: "/tmp/fake-playwriter" },
        {
          openDatabase,
          run: async (command) => {
            commands.push([...command]);
            return {
              exitCode: 0,
              stdout: "Connection reset successfully. 3 page(s) available.",
              stderr: "",
            };
          },
        },
      );
      expect(commands).toEqual([["/tmp/fake-playwriter", "session", "reset", "6"]]);
      expect(result).toMatchObject({
        command: "network session-reset",
        workflow: "network",
        sessionId: 6,
        reset: {
          ok: true,
          output: "Connection reset successfully. 3 page(s) available.",
        },
        context: {
          bindingPath: join(stateDir, "sessions", "network.json"),
          run: {
            run: {
              id: runId,
              localDate: "2026-08-09",
              status: "active",
            },
            durable: 0,
            planned: 0,
            provisional: 0,
            remainingCapacity: 30,
          },
        },
      });
    } finally {
      await rm(stateDir, { recursive: true, force: true });
    }
  });

  test("fails clearly when no network session is bound", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-session-reset-unbound-"));
    try {
      let ran = false;
      await expect(
        networkSessionReset(
          { stateDir, playwriterBin: "/tmp/fake-playwriter" },
          {
            openDatabase,
            run: async () => {
              ran = true;
              return { exitCode: 0, stdout: "", stderr: "" };
            },
          },
        ),
      ).rejects.toMatchObject({
        code: "NETWORK_SESSION_NOT_BOUND",
        details: { nextAction: "network tick --session auto" },
      });
      expect(ran).toBe(false);
    } finally {
      await rm(stateDir, { recursive: true, force: true });
    }
  });

  test("surfaces a failed reset with session id and stderr", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-session-reset-fail-"));
    try {
      await bindNetworkSession(stateDir, 12);
      await expect(
        networkSessionReset(
          { stateDir, playwriterBin: "/tmp/fake-playwriter" },
          {
            openDatabase,
            run: async () => ({ exitCode: 3, stdout: "", stderr: "session not found" }),
          },
        ),
      ).rejects.toMatchObject({
        code: "PLAYWRITER_SESSION_RESET_FAILED",
        details: { sessionId: 12, stderr: "session not found" },
      });
    } finally {
      await rm(stateDir, { recursive: true, force: true });
    }
  });
});
