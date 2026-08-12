import { createHash } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import {
  chmod,
  lstat,
  mkdir,
  open,
  readdir,
  readFile,
  realpath,
  rmdir,
  truncate,
  unlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { isAbsolute, join, normalize, parse, relative, resolve } from "node:path";
import {
  assertNoActiveIncident,
  IncidentDetectedError,
  maybeOpenIncidentFromFailure,
} from "../browser/incident.ts";
import { SHA256_HEX_RE } from "../core/evidence-contract.ts";
import { detectBlocker } from "./blockers.ts";
import { isControlledCompiledScript, materializeCompiledScript } from "./scripts.ts";
import { deepFreeze, parseSendPreparationReceiptId } from "./send.ts";
import {
  type CompiledScriptDescriptor,
  type InvocationConfig,
  type InvocationReceipt,
  type InvocationRequest,
  type InvocationResult,
  PLAYWRITER_DEFAULT_EXECUTABLE,
  PLAYWRITER_EXECUTABLE_ENV,
  type ProgressEvent,
  type SessionInfo,
  type TypedBlocker,
} from "./types.ts";
import {
  assertCandidateIdentity,
  assertCommitSendResultData,
  assertInvocationEvidence,
  assertSendPreparationBinding,
  assertStdoutResult,
  commandNeedsCandidate,
  immutableSendPreparationReceipt,
  immutableSourceCaptureResultData,
  parseProgress,
} from "./validation.ts";
import { commandTimeoutMs } from "./types.ts";

export interface PlaywriterClientOptions {
  readonly executable?: string;
  readonly executableArgs?: readonly string[];
  readonly env?: Readonly<Record<string, string | undefined>>;
  readonly invocationRoot?: string;
  readonly now?: () => Date;
  readonly createInvocationId?: () => string;
  readonly createHandoffNonce?: () => string;
  readonly beforeHandoffValidation?: (handoff: {
    readonly directory: string;
    readonly progressPath: string;
    readonly nonce: string;
  }) => void | Promise<void>;
  readonly crashAfterPhase?: (phase: ProgressEvent["state"]) => void;
  /** When set, every spawn is gated on <stateDir>/linkedin-incident.json. */
  readonly stateDir?: string;
}
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
function parseSessionId(s: string) {
  const bare = /^\s*([1-9]\d*)\s*$/.exec(s);
  const verbose =
    /^Session ([1-9]\d*) created(?: \([^\r\n)]+\))?\. Use with: playwriter -s ([1-9]\d*) -e "\.\.\."\s*$/m.exec(
      s,
    );
  if (!bare && (!verbose || verbose[1] !== verbose[2]))
    throw new Error("Playwriter returned an invalid session ID");
  const id = Number(bare?.[1] ?? verbose?.[1]);
  if (!Number.isSafeInteger(id) || id < 1)
    throw new Error("Playwriter returned an invalid session ID");
  return id;
}
function parseSessionList(s: string): SessionInfo[] {
  if (s.trim() === "No active sessions") return [];
  const lines = s.split(/\r?\n/).filter((line) => line.trim().length > 0);
  const headerIndex = lines.findIndex(
    (line) => line.startsWith("ID") && line.includes("STATE KEYS"),
  );
  if (headerIndex < 0) throw new Error("Playwriter returned an invalid session list");
  const header = lines[headerIndex] ?? "";
  const starts = {
    id: header.indexOf("ID"),
    browser: header.indexOf("BROWSER"),
    profile: header.indexOf("PROFILE"),
    extension: header.indexOf("EXT"),
    cwd: header.indexOf("CWD"),
    state: header.indexOf("STATE KEYS"),
  };
  if (
    starts.id !== 0 ||
    !(
      starts.browser < starts.profile &&
      starts.profile < starts.extension &&
      starts.extension < starts.cwd &&
      starts.cwd < starts.state
    )
  )
    throw new Error("Playwriter returned an invalid session list");
  const separator = lines[headerIndex + 1];
  if (separator === undefined || !/^-+$/.test(separator))
    throw new Error("Playwriter returned an invalid session list");
  const out: SessionInfo[] = [];
  for (const line of lines.slice(headerIndex + 2)) {
    const id = Number(line.slice(starts.id, starts.browser).trim());
    const browser = line.slice(starts.browser, starts.profile).trim();
    const profile = line.slice(starts.profile, starts.extension).trim();
    const extensionId = line.slice(starts.extension, starts.cwd).trim();
    const cwd = line.slice(starts.cwd, starts.state).trim();
    const stateKeysText = line.slice(starts.state).trim();
    if (!Number.isSafeInteger(id) || id < 1 || !browser || !stateKeysText)
      throw new Error("Playwriter returned an invalid session list");
    out.push({
      id,
      browser,
      profile: profile === "-" ? null : profile,
      extensionId: extensionId === "-" ? null : extensionId,
      cwd: cwd === "-" ? null : cwd,
      stateKeys:
        stateKeysText.trim() === "-"
          ? []
          : stateKeysText
              .split(",")
              .map((x) => x.trim())
              .filter(Boolean),
    });
  }
  return out;
}
function assertDescriptor(d: CompiledScriptDescriptor) {
  if (!isControlledCompiledScript(d) || !Object.isFrozen(d) || !Object.isFrozen(d.phases))
    throw new TypeError("descriptor must come from the controlled compiler");
}
type ControlPointer = {
  readonly bytes: number;
  readonly sha256: string;
};

function stdoutControlPointer(stdout: string, invocationId: string, nonce: string): ControlPointer {
  const text = stdout.trim();
  const lines = text.split(/\r?\n/);
  const marker = "__LINKEDIN_TOOLS_CONTROL_V1__";
  const controls = lines.flatMap((line) => {
    for (const prefix of ["[return value] ", "[log] "]) {
      if (line.startsWith(`${prefix}${marker}`)) return [line.slice(prefix.length + marker.length)];
    }
    return [];
  });
  if (controls.length !== 1) throw new TypeError("stdout must contain one control sentinel");
  const value = JSON.parse(controls[0] ?? "") as Record<string, unknown>;
  const keys = Object.keys(value).sort();
  if (
    keys.join(",") !== "bytes,invocationId,kind,nonce,schemaVersion,sha256" ||
    value.schemaVersion !== 1 ||
    value.kind !== "playwriter_control_pointer" ||
    value.invocationId !== invocationId ||
    value.nonce !== nonce ||
    !Number.isSafeInteger(value.bytes) ||
    (value.bytes as number) < 1 ||
    (value.bytes as number) > 262_144 ||
    typeof value.sha256 !== "string" ||
    !SHA256_HEX_RE.test(value.sha256)
  ) {
    throw new TypeError("stdout control sentinel is invalid");
  }
  return { bytes: value.bytes as number, sha256: value.sha256 };
}

async function ensureNoFollowDirectoryRoot(path: string): Promise<void> {
  if (!isAbsolute(path) || normalize(path) !== path || resolve(path) !== path) {
    throw new Error(`Playwriter invocation root must be a normalized absolute path: ${path}`);
  }
  const filesystemRoot = parse(path).root;
  if (path === filesystemRoot) {
    throw new Error("Playwriter invocation root must not be the filesystem root");
  }
  const rootInfo = await lstat(filesystemRoot);
  if (rootInfo.isSymbolicLink() || !rootInfo.isDirectory()) {
    throw new Error(`Playwriter filesystem root must be a real directory: ${filesystemRoot}`);
  }
  const parts = relative(filesystemRoot, path).split("/").filter(Boolean);
  let current = filesystemRoot;

  for (const [index, part] of parts.entries()) {
    current = join(current, part);
    let created = false;
    try {
      const info = await lstat(current);
      if (info.isSymbolicLink() || !info.isDirectory()) {
        throw new Error(
          `Playwriter invocation root component must be a real directory: ${current}`,
        );
      }
    } catch (error) {
      if (!(error instanceof Error && "code" in error && error.code === "ENOENT")) throw error;
      try {
        await mkdir(current, { recursive: false, mode: 0o700 });
        created = true;
      } catch (mkdirError) {
        if (
          !(mkdirError instanceof Error && "code" in mkdirError && mkdirError.code === "EEXIST")
        ) {
          throw mkdirError;
        }
      }
      const info = await lstat(current);
      if (info.isSymbolicLink() || !info.isDirectory()) {
        throw new Error(`Playwriter invocation root component changed during creation: ${current}`);
      }
    }

    const handle = await open(current, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
    try {
      const info = await handle.stat();
      if (!info.isDirectory()) {
        throw new Error(`Playwriter invocation root component is not a directory: ${current}`);
      }
      if (created || index === parts.length - 1) await handle.chmod(0o700);
      const verified = await handle.stat();
      if ((created || index === parts.length - 1) && (verified.mode & 0o777) !== 0o700) {
        throw new Error(`Playwriter invocation root permissions are not 0700: ${current}`);
      }
    } finally {
      await handle.close();
    }
  }

  current = filesystemRoot;
  for (const part of parts) {
    current = join(current, part);
    const info = await lstat(current);
    if (info.isSymbolicLink() || !info.isDirectory()) {
      throw new Error(`Playwriter invocation root component changed after validation: ${current}`);
    }
  }
  if ((await realpath(path)) !== path) {
    throw new Error(`Playwriter invocation root must not traverse aliases: ${path}`);
  }
}

const MAX_HANDOFF_BYTES = 512 * 1024;
const MAX_DIAGNOSTIC_BYTES = 128 * 1024;
type EvidenceHandoff = {
  readonly lexicalTempRoot: string;
  readonly canonicalTempRoot: string;
  readonly directory: string;
  readonly canonicalDirectory: string;
  readonly progressPath: string;
  readonly canonicalProgressPath: string;
  readonly nonce: string;
  readonly directoryDevice: number;
  readonly directoryInode: number;
  readonly directoryOwner: number;
  readonly device: number;
  readonly inode: number;
  readonly owner: number;
};

async function ensureLexicalTempDirectory(
  path: string,
  lexicalTempRoot: string,
  canonicalTempRoot: string,
): Promise<void> {
  if (
    !isAbsolute(path) ||
    normalize(path) !== path ||
    resolve(path) !== path ||
    resolve(lexicalTempRoot) !== lexicalTempRoot ||
    (await realpath(lexicalTempRoot)) !== canonicalTempRoot
  ) {
    throw new Error(`Playwriter temp path contract is invalid: ${path}`);
  }
  const child = relative(lexicalTempRoot, path);
  if (!child || child.startsWith("..") || isAbsolute(child)) {
    throw new Error(`Playwriter temp path escapes its lexical root: ${path}`);
  }
  const rootLexicalInfo = await open(
    lexicalTempRoot,
    fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW,
  );
  const rootCanonicalInfo = await open(
    canonicalTempRoot,
    fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW,
  );
  try {
    const [lexicalInfo, canonicalInfo] = await Promise.all([
      rootLexicalInfo.stat(),
      rootCanonicalInfo.stat(),
    ]);
    if (
      !lexicalInfo.isDirectory() ||
      !canonicalInfo.isDirectory() ||
      lexicalInfo.dev !== canonicalInfo.dev ||
      lexicalInfo.ino !== canonicalInfo.ino ||
      lexicalInfo.uid !== canonicalInfo.uid
    ) {
      throw new Error("Playwriter lexical and canonical temp roots differ");
    }
  } finally {
    await Promise.all([rootLexicalInfo.close(), rootCanonicalInfo.close()]);
  }
  const parts = child.split("/").filter(Boolean);
  let lexical = lexicalTempRoot;
  let canonical = canonicalTempRoot;
  for (const [index, part] of parts.entries()) {
    lexical = join(lexical, part);
    canonical = join(canonical, part);
    let created = false;
    try {
      const info = await lstat(lexical);
      if (info.isSymbolicLink() || !info.isDirectory()) {
        throw new Error(`Playwriter temp component must be a real directory: ${lexical}`);
      }
    } catch (error) {
      if (!(error instanceof Error && "code" in error && error.code === "ENOENT")) throw error;
      try {
        await mkdir(lexical, { recursive: false, mode: 0o700 });
        created = true;
      } catch (mkdirError) {
        if (
          !(mkdirError instanceof Error && "code" in mkdirError && mkdirError.code === "EEXIST")
        ) {
          throw mkdirError;
        }
      }
    }
    const info = await lstat(lexical);
    if (info.isSymbolicLink() || !info.isDirectory()) {
      throw new Error(`Playwriter temp component changed during creation: ${lexical}`);
    }
    const handle = await open(lexical, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
    try {
      if (created || index === parts.length - 1) {
        await handle.chmod(0o700);
      }
      const verified = await handle.stat();
      if (
        !verified.isDirectory() ||
        ((created || index === parts.length - 1) && (verified.mode & 0o777) !== 0o700)
      ) {
        throw new Error(`Playwriter temp component permissions are unsafe: ${lexical}`);
      }
    } finally {
      await handle.close();
    }
    if ((await realpath(lexical)) !== canonical) {
      throw new Error(`Playwriter temp lexical/canonical identity mismatch: ${lexical}`);
    }
  }
}

async function createEvidenceHandoff(
  invocationId: string,
  nonce: string,
): Promise<EvidenceHandoff> {
  if (!/^[a-f0-9]{32}$/.test(nonce)) throw new Error("invalid evidence handoff nonce");
  const lexicalTempRoot = resolve(tmpdir());
  const canonicalTempRoot = await realpath(lexicalTempRoot);
  const ownerRoot = join(lexicalTempRoot, "linkedin-tools-next-playwriter");
  await ensureLexicalTempDirectory(ownerRoot, lexicalTempRoot, canonicalTempRoot);
  const directory = join(ownerRoot, `${invocationId}_${nonce}`);
  await mkdir(directory, { recursive: false, mode: 0o700 });
  await ensureLexicalTempDirectory(directory, lexicalTempRoot, canonicalTempRoot);
  const canonicalDirectory = await realpath(directory);
  const directoryInfo = await lstat(directory);
  const canonicalDirectoryInfo = await lstat(canonicalDirectory);
  if (
    directoryInfo.isSymbolicLink() ||
    !directoryInfo.isDirectory() ||
    (directoryInfo.mode & 0o777) !== 0o700 ||
    directoryInfo.dev !== canonicalDirectoryInfo.dev ||
    directoryInfo.ino !== canonicalDirectoryInfo.ino ||
    directoryInfo.uid !== canonicalDirectoryInfo.uid ||
    (typeof process.getuid === "function" && directoryInfo.uid !== process.getuid())
  ) {
    throw new Error(`Playwriter evidence handoff directory is unsafe: ${directory}`);
  }
  const progressPath = join(directory, "progress.jsonl");
  const file = await open(
    progressPath,
    fsConstants.O_WRONLY | fsConstants.O_CREAT | fsConstants.O_EXCL | fsConstants.O_NOFOLLOW,
    0o600,
  );
  let device = -1;
  let inode = -1;
  let owner = -1;
  try {
    await file.chmod(0o600);
    const info = await file.stat();
    if (!info.isFile() || (info.mode & 0o777) !== 0o600) {
      throw new Error(`Playwriter evidence handoff permissions are not 0600: ${progressPath}`);
    }
    device = info.dev;
    inode = info.ino;
    owner = info.uid;
  } finally {
    await file.close();
  }
  const canonicalProgressPath = await realpath(progressPath);
  const canonicalProgressInfo = await lstat(canonicalProgressPath);
  if (
    canonicalProgressPath !== join(canonicalDirectory, "progress.jsonl") ||
    canonicalProgressInfo.dev !== device ||
    canonicalProgressInfo.ino !== inode ||
    canonicalProgressInfo.uid !== owner
  ) {
    throw new Error(`Playwriter evidence lexical/canonical identity mismatch: ${progressPath}`);
  }
  return {
    lexicalTempRoot,
    canonicalTempRoot,
    directory,
    canonicalDirectory,
    progressPath,
    canonicalProgressPath,
    nonce,
    directoryDevice: directoryInfo.dev,
    directoryInode: directoryInfo.ino,
    directoryOwner: directoryInfo.uid,
    device,
    inode,
    owner,
  };
}

async function readValidatedHandoff(
  handoff: EvidenceHandoff,
  config: InvocationConfig,
  requireComplete: boolean,
): Promise<{
  readonly events: readonly ProgressEvent[];
  readonly control: Record<string, unknown> | null;
  readonly controlJson: string | null;
  readonly diagnostics: Record<string, unknown> | null;
  readonly diagnosticsJson: string | null;
}> {
  await ensureLexicalTempDirectory(
    handoff.directory,
    handoff.lexicalTempRoot,
    handoff.canonicalTempRoot,
  );
  if ((await realpath(handoff.directory)) !== handoff.canonicalDirectory) {
    throw new Error(`evidence handoff canonical directory changed: ${handoff.directory}`);
  }
  const directoryInfo = await lstat(handoff.directory);
  if (
    directoryInfo.dev !== handoff.directoryDevice ||
    directoryInfo.ino !== handoff.directoryInode ||
    directoryInfo.uid !== handoff.directoryOwner
  ) {
    throw new Error(`evidence handoff directory identity changed: ${handoff.directory}`);
  }
  const entries = await readdir(handoff.directory);
  if (entries.length !== 1 || entries[0] !== "progress.jsonl") {
    throw new Error(`unexpected evidence handoff files: ${handoff.directory}`);
  }
  const pathInfo = await lstat(handoff.progressPath);
  if (pathInfo.isSymbolicLink() || !pathInfo.isFile()) {
    throw new Error(`evidence handoff must be a regular non-symlink file: ${handoff.progressPath}`);
  }
  if ((pathInfo.mode & 0o777) !== 0o600) {
    throw new Error(`evidence handoff permissions are not 0600: ${handoff.progressPath}`);
  }
  if (typeof process.getuid === "function" && pathInfo.uid !== process.getuid()) {
    throw new Error(`evidence handoff owner mismatch: ${handoff.progressPath}`);
  }
  if (
    pathInfo.dev !== handoff.device ||
    pathInfo.ino !== handoff.inode ||
    pathInfo.uid !== handoff.owner
  ) {
    throw new Error(`evidence handoff identity changed: ${handoff.progressPath}`);
  }
  if ((await realpath(handoff.progressPath)) !== handoff.canonicalProgressPath) {
    throw new Error(`evidence handoff canonical file changed: ${handoff.progressPath}`);
  }
  if (pathInfo.size <= 0 || pathInfo.size > MAX_HANDOFF_BYTES) {
    throw new Error(`evidence handoff size is invalid: ${handoff.progressPath}`);
  }
  const file = await open(handoff.progressPath, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
  let raw: string;
  try {
    const before = await file.stat();
    raw = await file.readFile("utf8");
    const after = await file.stat();
    if (
      before.dev !== after.dev ||
      before.ino !== after.ino ||
      before.size !== after.size ||
      before.mtimeMs !== after.mtimeMs
    ) {
      throw new Error(`evidence handoff changed while reading: ${handoff.progressPath}`);
    }
  } finally {
    await file.close();
  }
  const lines = raw.split("\n");
  if (lines.at(-1) !== "") throw new Error("evidence handoff must end with a newline");
  lines.pop();
  const sanitized: string[] = [];
  let control: Record<string, unknown> | null = null;
  let diagnostics: Record<string, unknown> | null = null;
  for (const [index, line] of lines.entries()) {
    if (!line) throw new Error(`empty evidence handoff line ${index + 1}`);
    const value = JSON.parse(line) as Record<string, unknown>;
    if (value.kind === "playwriter_control_record") {
      if (
        control !== null ||
        Object.keys(value).sort().join(",") !== "invocationId,kind,nonce,result,schemaVersion" ||
        value.schemaVersion !== 1 ||
        value.invocationId !== config.invocationId ||
        value.nonce !== handoff.nonce ||
        typeof value.result !== "object" ||
        value.result === null ||
        Array.isArray(value.result)
      ) {
        throw new Error(`invalid control record at line ${index + 1}`);
      }
      control = value.result as Record<string, unknown>;
      continue;
    }
    if (value.kind === "playwriter_diagnostic_selection") {
      if (
        diagnostics !== null ||
        Object.keys(value).sort().join(",") !==
          "genericNetErrFailedCount,genericNetErrFailedSample,invocationId,kind,nonce,otherCount,otherSample,relevant,relevantCount,sampleLimit,schemaVersion,selectionStep,sourceCount" ||
        value.schemaVersion !== 1 ||
        value.invocationId !== config.invocationId ||
        value.nonce !== handoff.nonce ||
        value.selectionStep !== "terminal-preserving-diagnostic-selection-v2" ||
        !Number.isSafeInteger(value.sourceCount) ||
        !Number.isSafeInteger(value.relevantCount) ||
        !Number.isSafeInteger(value.otherCount) ||
        !Number.isSafeInteger(value.genericNetErrFailedCount) ||
        value.sampleLimit !== 32 ||
        !Array.isArray(value.relevant) ||
        !Array.isArray(value.otherSample) ||
        !Array.isArray(value.genericNetErrFailedSample)
      ) {
        throw new Error(`invalid diagnostic selection at line ${index + 1}`);
      }
      for (const [groupName, entries, includesText] of [
        ["relevant", value.relevant, true],
        ["otherSample", value.otherSample, false],
        ["genericNetErrFailedSample", value.genericNetErrFailedSample, false],
      ] as const) {
        for (const entryValue of entries as unknown[]) {
          if (typeof entryValue !== "object" || entryValue === null || Array.isArray(entryValue)) {
            throw new Error(`invalid ${groupName} diagnostic entry`);
          }
          const entry = entryValue as Record<string, unknown>;
          if (
            Object.keys(entry).sort().join(",") !==
              (includesText ? "bytes,count,sha256,text" : "bytes,count,sha256") ||
            !Number.isSafeInteger(entry.count) ||
            (entry.count as number) < 1 ||
            !Number.isSafeInteger(entry.bytes) ||
            (entry.bytes as number) < 0 ||
            typeof entry.sha256 !== "string" ||
            !SHA256_HEX_RE.test(entry.sha256) ||
            (includesText &&
              (typeof entry.text !== "string" ||
                Buffer.byteLength(entry.text) !== entry.bytes ||
                createHash("sha256").update(entry.text).digest("hex") !== entry.sha256))
          ) {
            throw new Error(`invalid ${groupName} diagnostic entry`);
          }
        }
      }
      const count = (entries: unknown[]): number =>
        entries.reduce<number>(
          (total, entry) => total + ((entry as Record<string, unknown>).count as number),
          0,
        );
      const sourceCount = value.sourceCount as number;
      const relevantCount = value.relevantCount as number;
      const otherCount = value.otherCount as number;
      const genericNetErrFailedCount = value.genericNetErrFailedCount as number;
      if (
        (value.relevant as unknown[]).length > sourceCount ||
        (value.otherSample as unknown[]).length > 32 ||
        (value.genericNetErrFailedSample as unknown[]).length > 32 ||
        count(value.relevant as unknown[]) !== relevantCount ||
        otherCount < count(value.otherSample as unknown[]) ||
        genericNetErrFailedCount < count(value.genericNetErrFailedSample as unknown[]) ||
        relevantCount + otherCount + genericNetErrFailedCount !== sourceCount
      ) {
        throw new Error("diagnostic selection count mismatch");
      }
      const diagnosticJson = JSON.stringify(value);
      if (Buffer.byteLength(diagnosticJson) > MAX_DIAGNOSTIC_BYTES) {
        throw new Error("diagnostic artifact exceeds its maximum size");
      }
      diagnostics = value;
      continue;
    }
    const keys = Object.keys(value).sort();
    const allowed = [
      "candidate",
      "command",
      "detail",
      "invocationId",
      "nonce",
      "state",
      "timestamp",
    ];
    if (
      !["command", "invocationId", "nonce", "state", "timestamp"].every((key) =>
        keys.includes(key),
      ) ||
      keys.some((key) => !allowed.includes(key)) ||
      value.nonce !== handoff.nonce
    ) {
      throw new Error(`invalid evidence handoff schema at line ${index + 1}`);
    }
    const { nonce: _nonce, ...event } = value;
    sanitized.push(JSON.stringify(event));
  }
  const parsed = parseProgress(`${sanitized.join("\n")}\n`);
  if (parsed.corrupt) throw new Error(`invalid evidence handoff JSONL: ${parsed.corrupt}`);
  const states = parsed.events.map((event) => event.state);
  if (
    parsed.events.some(
      (event) =>
        event.invocationId !== config.invocationId ||
        event.command !== config.command ||
        !same(event.candidate, config.candidate),
    ) ||
    states.some((state, index) => state !== config.phaseContract[index]) ||
    states.length > config.phaseContract.length ||
    (requireComplete && states.length !== config.phaseContract.length)
  ) {
    throw new Error("evidence handoff identity or phase order mismatch");
  }
  if (parsed.events.length === 0) throw new Error("evidence handoff is empty");
  if (requireComplete && (control === null || diagnostics === null)) {
    throw new Error("complete evidence handoff is missing control or diagnostics");
  }
  let timestamp = -Infinity;
  for (const event of parsed.events) {
    const current = Date.parse(event.timestamp);
    if (current < timestamp) throw new Error("evidence handoff timestamps are not monotonic");
    timestamp = current;
  }
  await ensureLexicalTempDirectory(
    handoff.directory,
    handoff.lexicalTempRoot,
    handoff.canonicalTempRoot,
  );
  const controlJson = control === null ? null : JSON.stringify(control);
  const diagnosticsJson = diagnostics === null ? null : JSON.stringify(diagnostics);
  return { events: parsed.events, control, controlJson, diagnostics, diagnosticsJson };
}

async function cleanupValidatedHandoff(handoff: EvidenceHandoff): Promise<void> {
  await ensureLexicalTempDirectory(
    handoff.directory,
    handoff.lexicalTempRoot,
    handoff.canonicalTempRoot,
  );
  const directoryInfo = await lstat(handoff.directory);
  if (
    directoryInfo.dev !== handoff.directoryDevice ||
    directoryInfo.ino !== handoff.directoryInode ||
    directoryInfo.uid !== handoff.directoryOwner
  ) {
    throw new Error(`refusing to clean substituted evidence directory: ${handoff.directory}`);
  }
  const entries = await readdir(handoff.directory);
  if (entries.length !== 1 || entries[0] !== "progress.jsonl") {
    throw new Error(`refusing to clean unexpected evidence handoff: ${handoff.directory}`);
  }
  const info = await lstat(handoff.progressPath);
  if (
    info.isSymbolicLink() ||
    !info.isFile() ||
    info.dev !== handoff.device ||
    info.ino !== handoff.inode ||
    info.uid !== handoff.owner
  ) {
    throw new Error(`refusing to clean substituted evidence handoff: ${handoff.progressPath}`);
  }
  await unlink(handoff.progressPath);
  await rmdir(handoff.directory);
}

export class PlaywriterClient {
  readonly executable: string;
  readonly invocationRoot: string;
  private readonly executableArgs: readonly string[];
  private readonly environment: Record<string, string | undefined>;
  private readonly now: () => Date;
  private readonly createInvocationId: () => string;
  private readonly createHandoffNonce: () => string;
  private readonly beforeHandoffValidation?: PlaywriterClientOptions["beforeHandoffValidation"];
  private readonly crashAfterPhase?: PlaywriterClientOptions["crashAfterPhase"];
  private readonly stateDir: string | undefined;
  constructor(o: PlaywriterClientOptions = {}) {
    this.environment = { ...process.env, ...o.env };
    this.executable =
      o.executable ?? this.environment[PLAYWRITER_EXECUTABLE_ENV] ?? PLAYWRITER_DEFAULT_EXECUTABLE;
    this.executableArgs = o.executableArgs ?? [];
    this.invocationRoot =
      o.invocationRoot ?? join(process.cwd(), "var", "playwriter", "invocations");
    this.now = o.now ?? (() => new Date());
    this.createInvocationId =
      o.createInvocationId ?? (() => `pw_${crypto.randomUUID().replaceAll("-", "")}`);
    this.createHandoffNonce =
      o.createHandoffNonce ?? (() => crypto.randomUUID().replaceAll("-", ""));
    this.beforeHandoffValidation = o.beforeHandoffValidation;
    this.crashAfterPhase = o.crashAfterPhase;
    this.stateDir = o.stateDir;
  }
  async createSession() {
    return parseSessionId((await this.run(["session", "new"])).stdout);
  }
  async listSessions() {
    return parseSessionList((await this.run(["session", "list"])).stdout);
  }
  async resetSession(id: number) {
    if (!Number.isSafeInteger(id) || id < 1) throw new TypeError("invalid sessionId");
    await this.run(["session", "reset", String(id)]);
  }

  async invoke(request: InvocationRequest): Promise<InvocationResult> {
    await this.guardIncident();
    if (!Number.isSafeInteger(request.sessionId) || request.sessionId < 1)
      throw new TypeError("invalid sessionId");
    assertDescriptor(request.descriptor);
    const embedded = request.descriptor.candidate;
    if (request.candidate !== undefined) {
      assertCandidateIdentity(request.candidate);
      if (!same(request.candidate, embedded)) throw new TypeError("candidate identity mismatch");
    }
    if (commandNeedsCandidate(request.descriptor.command) && !embedded)
      throw new TypeError("descriptor candidate missing");
    if (!commandNeedsCandidate(request.descriptor.command) && embedded)
      throw new TypeError("descriptor candidate forbidden");
    const embeddedPreparation = request.descriptor.sendPreparation;
    const embeddedSourceContract = request.descriptor.sourceContract;
    const sourceCommand =
      request.descriptor.command === "navigate-candidate-results" ||
      request.descriptor.command === "capture-candidate-results" ||
      request.descriptor.command === "walk-list";
    if (sourceCommand && embeddedSourceContract === undefined)
      throw new TypeError("source contract missing from descriptor");
    if (!sourceCommand && embeddedSourceContract !== undefined)
      throw new TypeError("source contract forbidden for descriptor");
    const isCommitSend = request.descriptor.command === "commit-send";
    if (isCommitSend) {
      if (embeddedPreparation === undefined || request.sendPreparation === undefined)
        throw new TypeError("commit-send requires send preparation receipt");
      assertSendPreparationBinding(embeddedPreparation, request.sessionId);
      assertSendPreparationBinding(request.sendPreparation, request.sessionId);
      if (!same(embeddedPreparation, request.sendPreparation))
        throw new TypeError("send preparation identity mismatch");
      if (!same(embeddedPreparation.candidate, embedded))
        throw new TypeError("send preparation candidate mismatch");
    } else if (embeddedPreparation !== undefined || request.sendPreparation !== undefined) {
      throw new TypeError("send preparation is only valid for commit-send");
    }
    const invocationId = this.createInvocationId(),
      createdAt = this.timestamp(),
      directory = join(this.invocationRoot, invocationId);
    await ensureNoFollowDirectoryRoot(this.invocationRoot);
    await mkdir(directory, { recursive: false, mode: 0o700 });
    await ensureNoFollowDirectoryRoot(this.invocationRoot);
    const invocationDirectory = await lstat(directory);
    if (invocationDirectory.isSymbolicLink() || !invocationDirectory.isDirectory()) {
      throw new Error(`Playwriter invocation directory must be a real directory: ${directory}`);
    }
    const invocationHandle = await open(directory, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
    try {
      const verified = await invocationHandle.stat();
      if (!verified.isDirectory() || (verified.mode & 0o777) !== 0o700) {
        throw new Error(`Playwriter invocation directory permissions are not 0700: ${directory}`);
      }
    } finally {
      await invocationHandle.close();
    }
    const handoff = await createEvidenceHandoff(invocationId, this.createHandoffNonce());
    const paths = {
      config: join(directory, "config.json"),
      progress: join(directory, "progress.jsonl"),
      stdout: join(directory, "stdout.log"),
      stderr: join(directory, "stderr.log"),
      control: join(directory, "control.json"),
      diagnostics: join(directory, "diagnostics.json"),
      receipt: join(directory, "receipt.json"),
    };
    const config: InvocationConfig = {
      schemaVersion: 1,
      invocationId,
      command: request.descriptor.command,
      definitionId: request.descriptor.definitionId,
      action: request.descriptor.action,
      phaseContract: request.descriptor.phases,
      createdAt,
      sessionId: request.sessionId,
      ...(embedded ? { candidate: embedded } : {}),
      ...(embeddedPreparation ? { sendPreparation: embeddedPreparation } : {}),
      ...(embeddedSourceContract ? { sourceContract: embeddedSourceContract } : {}),
      input: { ...(request.input ?? {}) },
    };
    let stdout = "",
      stderr = "",
      exitCode = -1,
      result: Record<string, unknown> | null = null;
    const startedAt = this.timestamp();
    let progress: ProgressEvent[] = [];
    let evidenceProblem: TypedBlocker | undefined;
    let diagnosticText = "";
    let transferredControl: Record<string, unknown> | null = null;
    const sealedFiles = [paths.config, paths.progress, paths.stdout, paths.stderr];
    const appendFile = async (path: string, data: string) => {
      const h = await open(path, "a");
      try {
        await h.write(data);
        await h.sync();
      } finally {
        await h.close();
      }
    };
    const event = async (state: ProgressEvent["state"]) =>
      appendFile(
        paths.progress,
        `${JSON.stringify({ invocationId, command: config.command, state, timestamp: this.timestamp(), ...(embedded ? { candidate: embedded } : {}) })}\n`,
      );
    try {
      await writeFile(paths.config, `${JSON.stringify(config, null, 2)}\n`, { flag: "wx" });
      await Promise.all(
        [paths.progress, paths.stdout, paths.stderr].map((p) => writeFile(p, "", { flag: "wx" })),
      );
      await event("invocation_created");
      await event("process_started");
      try {
        const child = Bun.spawn(
          [
            this.executable,
            ...this.executableArgs,
            ...(commandTimeoutMs(request.descriptor.command) === null
              ? []
              : ["--timeout", String(commandTimeoutMs(request.descriptor.command))]),
            "-s",
            String(request.sessionId),
            "-e",
            materializeCompiledScript(
              request.descriptor,
              invocationId,
              request.sessionId,
              handoff.progressPath,
              handoff.nonce,
            ),
          ],
          {
            cwd: directory,
            env: this.environment,
            stdin: "ignore",
            stdout: "pipe",
            stderr: "pipe",
          },
        );
        const settled = await Promise.all([
          new Response(child.stdout).text(),
          new Response(child.stderr).text(),
          child.exited,
        ]);
        [stdout, stderr, exitCode] = settled;
      } catch (error) {
        stderr += `${stderr && !stderr.endsWith("\n") ? "\n" : ""}${error instanceof Error ? error.message : String(error)}\n`;
        exitCode = -1;
      }
      await appendFile(paths.stdout, stdout);
      await appendFile(paths.stderr, stderr);
      try {
        await this.beforeHandoffValidation?.(handoff);
        const transferred = await readValidatedHandoff(handoff, config, exitCode === 0);
        for (const browserEvent of transferred.events) {
          await appendFile(paths.progress, `${JSON.stringify(browserEvent)}\n`);
        }
        if (exitCode === 0) {
          if (
            transferred.control === null ||
            transferred.controlJson === null ||
            transferred.diagnostics === null ||
            transferred.diagnosticsJson === null
          ) {
            throw new Error("successful process is missing transferred artifacts");
          }
          const pointer = stdoutControlPointer(stdout, invocationId, handoff.nonce);
          if (
            pointer.bytes !== Buffer.byteLength(transferred.controlJson) ||
            pointer.sha256 !== createHash("sha256").update(transferred.controlJson).digest("hex")
          ) {
            throw new Error("stdout control pointer does not match transferred control data");
          }
          assertStdoutResult(transferred.control, config.command);
          const summary = transferred.control.logs as Record<string, unknown>;
          const relevant = transferred.diagnostics.relevant as Array<Record<string, unknown>>;
          if (
            summary.sha256 !==
              createHash("sha256").update(transferred.diagnosticsJson).digest("hex") ||
            summary.sourceCount !== transferred.diagnostics.sourceCount ||
            summary.relevantCount !== transferred.diagnostics.relevantCount ||
            summary.otherCount !== transferred.diagnostics.otherCount ||
            summary.genericNetErrFailedCount !== transferred.diagnostics.genericNetErrFailedCount
          ) {
            throw new Error("diagnostic summary does not match its artifact");
          }
          await writeFile(paths.control, `${JSON.stringify(transferred.control, null, 2)}\n`, {
            flag: "wx",
            mode: 0o600,
          });
          sealedFiles.push(paths.control);
          await writeFile(
            paths.diagnostics,
            `${JSON.stringify(transferred.diagnostics, null, 2)}\n`,
            { flag: "wx", mode: 0o600 },
          );
          sealedFiles.push(paths.diagnostics);
          diagnosticText = JSON.stringify(relevant);
          transferredControl = transferred.control;
        }
        await cleanupValidatedHandoff(handoff);
      } catch (error) {
        evidenceProblem = {
          kind: "evidence_corrupt",
          evidence: `EVIDENCE_HANDOFF ${error instanceof Error ? error.message : String(error)}; preserved at ${handoff.directory}`,
          retryability: "safe_retry",
        };
        stderr += `${evidenceProblem.evidence}\n`;
        await appendFile(paths.stderr, `${evidenceProblem.evidence}\n`);
      }
      const raw = await readFile(paths.progress, "utf8");
      const parsed = parseProgress(raw);
      progress = parsed.events;
      if (parsed.corrupt) {
        const valid = progress.map((e) => `${JSON.stringify(e)}\n`).join("");
        await truncate(paths.progress, 0);
        await writeFile(paths.progress, valid);
        evidenceProblem = {
          kind: "evidence_corrupt",
          evidence: `EVIDENCE_CORRUPT ${parsed.corrupt}`,
          retryability: "safe_retry",
        };
        stderr += `${evidenceProblem.evidence}\n`;
        await appendFile(paths.stderr, `${evidenceProblem.evidence}\n`);
      }
      if (exitCode === 0 && !evidenceProblem) {
        try {
          if (transferredControl === null) throw new TypeError("transferred control is missing");
          const parsedResult = transferredControl;
          if (config.command === "prepare-send") {
            assertSendPreparationBinding(parsedResult.data, config.sessionId);
            const prepared = immutableSendPreparationReceipt(parsedResult.data);
            const preparedId = parseSendPreparationReceiptId(prepared.receiptId);
            if (
              preparedId.prepareInvocationId !== invocationId ||
              prepared.attemptId !== config.input.attemptId ||
              !same(prepared.candidate, config.candidate)
            )
              throw new TypeError("prepare result identity mismatch");
            parsedResult.data = prepared;
          }
          if (config.command === "commit-send") {
            if (embeddedPreparation === undefined)
              throw new TypeError("commit preparation missing from descriptor");
            assertCommitSendResultData(parsedResult.data, embeddedPreparation);
          }
          if (config.command === "capture-candidate-results") {
            if (embeddedSourceContract === undefined)
              throw new TypeError("capture source contract missing from descriptor");
            parsedResult.data = immutableSourceCaptureResultData(parsedResult.data, {
              invocationId,
              sourceContract: embeddedSourceContract,
            });
          }
          result = deepFreeze(parsedResult) as Record<string, unknown>;
        } catch (error) {
          evidenceProblem = {
            kind: "evidence_corrupt",
            evidence: `EVIDENCE_CORRUPT stdout: ${error instanceof Error ? error.message : String(error)}`,
            retryability: "safe_retry",
          };
          await appendFile(paths.stderr, `${evidenceProblem.evidence}\n`);
        }
      }
      const boundary =
        config.command === "commit-send" ||
        progress.some((e) => e.state === "analytics_confirm_started");
      const diagnosticBlocker = detectBlocker(diagnosticText);
      const success = exitCode === 0 && result !== null && !evidenceProblem && !diagnosticBlocker;
      await event(success ? "process_succeeded" : "process_failed");
      progress = parseProgress(await readFile(paths.progress, "utf8")).events;
      const stderrBlocker = detectBlocker(stdout, stderr);
      let blocker = diagnosticBlocker ?? stderrBlocker ?? evidenceProblem;
      if (!success && config.command === "commit-send") {
        blocker = blocker
          ? { ...blocker, retryability: "possible_send" }
          : {
              kind: "commit_uncertainty",
              evidence: "COMMIT_SEND_UNCERTAIN",
              retryability: "possible_send",
            };
      } else if (!success && boundary && blocker) {
        blocker = { ...blocker, retryability: "possible_send" };
      }
      const receipt: InvocationReceipt = {
        schemaVersion: 1,
        invocationId,
        command: config.command,
        definitionId: config.definitionId,
        action: config.action,
        startedAt,
        finishedAt: this.timestamp(),
        exitCode,
        outcome: success ? "succeeded" : boundary ? "critical_uncertainty" : "failed",
        result,
        ...(embedded ? { candidate: embedded } : {}),
        ...(blocker ? { blocker } : {}),
      };
      await writeFile(paths.receipt, `${JSON.stringify(receipt, null, 2)}\n`, { flag: "wx" });
      sealedFiles.push(paths.receipt);
      const reloaded = JSON.parse(await readFile(paths.receipt, "utf8")) as InvocationReceipt;
      assertInvocationEvidence(config, reloaded, progress);
      await this.seal(directory, sealedFiles);
      const crash = progress.find((e) => {
        try {
          this.crashAfterPhase?.(e.state);
          return false;
        } catch {
          return true;
        }
      });
      if (crash) throw new Error(`crash:${crash.state}`);
      if (!success) {
        await this.recordFatalIncident({
          stdout,
          stderr,
          diagnosticText,
          blocker: blocker?.kind,
          evidence: blocker?.evidence,
          exitCode,
          command: config.command,
        });
      }
      return { directory, config, receipt: reloaded, stdout, stderr, progress };
    } catch (error) {
      // If a receipt exists, evidence was already finalized and sealed; preserve it.
      try {
        await readFile(paths.receipt, "utf8");
      } catch {
        throw new Error(
          `EVIDENCE_FINALIZATION: ${error instanceof Error ? error.message : String(error)}; handoff preserved at ${handoff.directory}`,
        );
      }
      throw error;
    }
  }
  private async seal(directory: string, files: readonly string[]) {
    for (const p of files) await chmod(p, 0o444);
    await chmod(directory, 0o555);
  }
  private timestamp() {
    return this.now().toISOString();
  }
  private async run(args: readonly string[]) {
    await this.guardIncident();
    const child = Bun.spawn([this.executable, ...this.executableArgs, ...args], {
      env: this.environment,
      stdout: "pipe",
      stderr: "pipe",
    });
    const [stdout, stderr, code] = await Promise.all([
      new Response(child.stdout).text(),
      new Response(child.stderr).text(),
      child.exited,
    ]);
    if (code !== 0) {
      const message = `Playwriter exited with ${code}: ${stderr.trim()}`;
      await this.recordFatalIncident({ stdout, stderr, exitCode: code, message });
      throw new Error(message);
    }
    return { stdout, stderr };
  }

  private async guardIncident(): Promise<void> {
    if (this.stateDir === undefined) return;
    await assertNoActiveIncident(this.stateDir);
  }

  private async recordFatalIncident(value: unknown): Promise<void> {
    if (this.stateDir === undefined) return;
    const opened = await maybeOpenIncidentFromFailure(this.stateDir, value);
    if (opened !== null) throw new IncidentDetectedError(opened);
  }
}
