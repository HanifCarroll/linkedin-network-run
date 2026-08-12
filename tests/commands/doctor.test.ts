import { describe, expect, test } from "bun:test";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { doctor } from "../../src/commands/doctor.ts";
import { bootstrapDatabase } from "../../src/db/database.ts";

describe("doctor command", () => {
  test("checks local prerequisites without opening a browser", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-doctor-"));
    const calls: string[][] = [];
    const result = await doctor(
      {
        stateDir,
        playwriterBin: process.execPath,
        networkSessionId: 7,
        analyticsSessionId: 8,
      },
      {
        bootstrapDatabase,
        createProbeId: () => "test",
        run: async (command) => {
          calls.push([...command]);
          if (command.at(-2) === "session") {
            return { exitCode: 0, stdout: "7  network\n8  analytics\n", stderr: "" };
          }
          return { exitCode: 0, stdout: "playwriter/0.4.0\nUsage: ignored\n", stderr: "" };
        },
      },
    );
    expect(result.ready).toBe(true);
    expect(result.browser).toMatchObject({
      boundary: "playwriter",
      noDirectPlaywright: true,
      noDirectCdp: true,
      noLease: true,
      sessions: {
        network: { configured: true, active: true },
        analytics: { configured: true, active: true },
      },
    });
    expect(result.browser.binary.version).toBe("playwriter/0.4.0");
    expect(result.sqlite.ok).toBe(true);
    expect(calls).toEqual([
      [process.execPath, "--version"],
      [process.execPath, "session", "list"],
    ]);
  });

  test("succeeds diagnostically but is not ready when sessions are unconfigured", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-doctor-missing-"));
    const result = await doctor(
      { stateDir, playwriterBin: process.execPath },
      {
        bootstrapDatabase,
        createProbeId: () => "test",
        run: async () => ({ exitCode: 0, stdout: "playwriter/0.4.0\n", stderr: "" }),
      },
    );
    expect(result.ready).toBe(false);
    expect(result.browser.sessions.listingChecked).toBe(false);
    expect(result.browser.sessions.network.active).toBeNull();
  });

  test("reports source drift when the automation prompt names stale sources", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-doctor-drift-"));
    const promptPath = join(stateDir, "automation.toml");
    await writeFile(
      promptPath,
      `Objective: 15 from Consulting - Marketing Agency Owners and 15 from Consulting - Fractional COOs.\n`,
      "utf8",
    );
    const result = await doctor(
      { stateDir, playwriterBin: process.execPath, automationPromptPath: promptPath },
      {
        bootstrapDatabase,
        createProbeId: () => "test",
        run: async () => ({ exitCode: 0, stdout: "playwriter/0.4.0\n", stderr: "" }),
      },
    );
    expect(result.contract).toMatchObject({
      automationPromptPath: promptPath,
      promptFound: true,
      drift: true,
      configuredSources: ["Consulting - HubSpot Agency Ops", "Consulting - HubSpot B2B RevOps"],
      promptSources: [],
    });
  });

  test("reports no drift when the automation prompt names the configured sources", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-doctor-aligned-"));
    const promptPath = join(stateDir, "automation.toml");
    await writeFile(
      promptPath,
      `Objective: 15 from Consulting - HubSpot Agency Ops and 15 from Consulting - HubSpot B2B RevOps.\n`,
      "utf8",
    );
    const result = await doctor(
      { stateDir, playwriterBin: process.execPath, automationPromptPath: promptPath },
      {
        bootstrapDatabase,
        createProbeId: () => "test",
        run: async () => ({ exitCode: 0, stdout: "playwriter/0.4.0\n", stderr: "" }),
      },
    );
    expect(result.contract).toMatchObject({
      promptFound: true,
      drift: false,
      promptSources: ["Consulting - HubSpot Agency Ops", "Consulting - HubSpot B2B RevOps"],
    });
  });
});
