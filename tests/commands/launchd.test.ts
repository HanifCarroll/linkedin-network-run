import { describe, expect, test } from "bun:test";

const NETWORK = new URL("../../launchd/com.hanif.linkedin-tools.network.plist", import.meta.url);
const ANALYTICS = new URL(
  "../../launchd/com.hanif.linkedin-tools.analytics.plist",
  import.meta.url,
);

describe("uninstalled launchd templates", () => {
  test("are configuration-complete and use distinct auto-bound sessions", async () => {
    const [network, analytics] = await Promise.all([
      Bun.file(NETWORK).text(),
      Bun.file(ANALYTICS).text(),
    ]);

    for (const text of [network, analytics]) {
      expect(text).not.toMatch(/__[A-Z0-9_]+__/);
      expect(text).not.toContain("KeepAlive");
      expect(text.toLowerCase()).not.toContain("lease");
      expect(programArguments(text)).toContain("auto");
      expect(text).toContain("<key>WorkingDirectory</key>");
      expect(text).toContain("<key>PATH</key>");
    }
    expect(outputPath(network, "StandardOutPath")).not.toBe(
      outputPath(analytics, "StandardOutPath"),
    );
    expect(outputPath(network, "StandardErrorPath")).not.toBe(
      outputPath(analytics, "StandardErrorPath"),
    );
  });

  test("has one daily completion-capable network trigger in a non-overlapping window", async () => {
    const [network, analytics] = await Promise.all([
      Bun.file(NETWORK).text(),
      Bun.file(ANALYTICS).text(),
    ]);
    expect(network.match(/<key>StartCalendarInterval<\/key>/g)).toHaveLength(1);
    expect(calendar(network)).toContain("<key>Hour</key>\n    <integer>9</integer>");
    expect(calendar(network)).toContain("<key>Minute</key>\n    <integer>5</integer>");
    expect(calendar(analytics)).toContain("<key>Weekday</key>\n    <integer>1</integer>");
    expect(calendar(analytics)).toContain("<key>Hour</key>\n    <integer>6</integer>");
    expect(calendar(analytics)).toContain("<key>Minute</key>\n    <integer>15</integer>");

    expect(programArguments(network)).toEqual([
      "/Users/hanifcarroll/.bun/bin/bun",
      "/Users/hanifcarroll/projects/linkedin-tools/src/cli.ts",
      "--json",
      "network",
      "tick",
      "--allow-send",
      "--batch-size",
      "5",
      "--target",
      "30",
      "--max-real-sends",
      "30",
      "--state-dir",
      "/Users/hanifcarroll/Library/Application Support/linkedin-tools-next",
      "--session",
      "auto",
    ]);
    expect(programArguments(analytics).slice(-2)).toEqual(["--session", "auto"]);
  });
});

function programArguments(text: string): readonly string[] {
  const program = text.match(/<key>ProgramArguments<\/key>\s*<array>([\s\S]*?)<\/array>/);
  return [...(program?.[1] ?? "").matchAll(/<string>(.*?)<\/string>/g)].map(
    (match) => match[1] ?? "",
  );
}

function calendar(text: string): string {
  return text.match(/<key>StartCalendarInterval<\/key>\s*<dict>([\s\S]*?)<\/dict>/)?.[1] ?? "";
}

function outputPath(text: string, key: "StandardOutPath" | "StandardErrorPath"): string {
  return text.match(new RegExp(`<key>${key}</key>\\s*<string>(.*?)</string>`))?.[1] ?? "";
}
