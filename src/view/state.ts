import { homedir } from "node:os";
import { join, resolve } from "node:path";

export function viewerStateDir(): string {
  const index = process.argv.indexOf("--state-dir");
  const flag = index >= 0 ? process.argv[index + 1] : undefined;
  return resolve(
    flag ??
      process.env.LINKEDIN_TOOLS_STATE_DIR ??
      join(homedir(), "Library", "Application Support", "linkedin-tools-next"),
  );
}
