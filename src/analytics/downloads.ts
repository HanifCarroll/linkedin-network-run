import { lstat, readdir, realpath } from "node:fs/promises";
import { isAbsolute, join, relative, resolve } from "node:path";
import type { DownloadSnapshotEntry } from "./types.ts";

const CANDIDATE_PATTERN =
  /^AggregateAnalytics_.+_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}(?: \(\d+\))?\.xlsx$/;

export async function snapshotDownloads(
  roots: readonly string[],
): Promise<ReadonlyMap<string, DownloadSnapshotEntry>> {
  const entries = new Map<string, DownloadSnapshotEntry>();
  for (const root of roots) {
    let names: string[];
    let rootRealPath: string;
    try {
      rootRealPath = await realpath(root);
      names = await readdir(rootRealPath);
    } catch (error) {
      if (isMissing(error)) continue;
      throw error;
    }
    for (const name of names) {
      if (!CANDIDATE_PATTERN.test(name)) continue;
      const path = join(rootRealPath, name);
      const linkInfo = await lstat(path);
      if (linkInfo.isSymbolicLink())
        throw new Error(`analytics download candidate must not be a symlink: ${path}`);
      if (!linkInfo.isFile()) continue;
      const realPath = await realpath(path);
      if (!isContainedPath(rootRealPath, realPath))
        throw new Error(`analytics download candidate escaped its root: ${path}`);
      entries.set(
        path,
        Object.freeze({
          path,
          realPath,
          rootRealPath,
          device: linkInfo.dev,
          inode: linkInfo.ino,
          birthtimeMs: linkInfo.birthtimeMs,
          size: linkInfo.size,
          modifiedAtMs: linkInfo.mtimeMs,
        }),
      );
    }
  }
  return entries;
}

export function changedDownloads(
  before: ReadonlyMap<string, DownloadSnapshotEntry>,
  after: ReadonlyMap<string, DownloadSnapshotEntry>,
): DownloadSnapshotEntry[] {
  return [...after.values()]
    .filter((entry) => {
      const previous = before.get(entry.path);
      return (
        previous === undefined ||
        previous.device !== entry.device ||
        previous.inode !== entry.inode ||
        previous.size !== entry.size ||
        previous.modifiedAtMs !== entry.modifiedAtMs
      );
    })
    .sort((left, right) => left.path.localeCompare(right.path));
}

function isMissing(error: unknown): boolean {
  return error instanceof Error && "code" in error && error.code === "ENOENT";
}

export function isContainedPath(root: string, candidate: string): boolean {
  const child = relative(resolve(root), resolve(candidate));
  return child === "" || (!child.startsWith("..") && !isAbsolute(child));
}
