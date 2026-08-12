import { constants as fsConstants } from "node:fs";
import { open } from "node:fs/promises";
import { basename } from "node:path";
import { inflateRawSync } from "node:zlib";
import type { WorkbookIdentity } from "./types.ts";

const WORKBOOK_PATTERN =
  /^AggregateAnalytics_(.+)_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})(?: \(\d+\))?\.xlsx$/;
const REQUIRED_MEMBERS = new Set(["[Content_Types].xml", "xl/workbook.xml"]);
export const MAX_WORKBOOK_BYTES = 64 * 1024 * 1024;
export const MAX_CENTRAL_DIRECTORY_BYTES = 8 * 1024 * 1024;
export const MAX_ZIP_ENTRIES = 10_000;
export const MAX_UNCOMPRESSED_ENTRY_BYTES = 128 * 1024 * 1024;
export const MAX_TOTAL_UNCOMPRESSED_BYTES = 256 * 1024 * 1024;
export const MAX_AGGREGATE_COMPRESSION_RATIO = 200;
const MAX_MEMBER_NAME_BYTES = 4_096;

export function parseWorkbookFilename(path: string): WorkbookIdentity {
  const filename = basename(path);
  const match = WORKBOOK_PATTERN.exec(filename);
  if (!match?.[1] || !match[2] || !match[3])
    throw new Error(`invalid analytics workbook filename: ${filename}`);
  if (!isIsoDate(match[2]) || !isIsoDate(match[3]))
    throw new Error(`invalid analytics workbook date: ${filename}`);
  return Object.freeze({ filename, account: match[1], startDate: match[2], endDate: match[3] });
}

export function assertRequestedWorkbook(
  identity: WorkbookIdentity,
  expected: { readonly account: string; readonly startDate: string; readonly endDate: string },
): void {
  if (identity.account !== expected.account)
    throw new Error(
      `analytics workbook account mismatch: expected ${expected.account}, found ${identity.account}`,
    );
  if (identity.startDate !== expected.startDate || identity.endDate !== expected.endDate) {
    throw new Error(
      `analytics workbook period mismatch: expected ${expected.startDate}..${expected.endDate}, found ${identity.startDate}..${identity.endDate}`,
    );
  }
  assertInclusiveSevenDayRange(identity.startDate, identity.endDate);
}

export function assertInclusiveSevenDayRange(startDate: string, endDate: string): void {
  if (!isIsoDate(startDate) || !isIsoDate(endDate))
    throw new Error("analytics requested period contains an invalid date");
  const milliseconds = Date.parse(`${endDate}T00:00:00Z`) - Date.parse(`${startDate}T00:00:00Z`);
  if (milliseconds !== 6 * 24 * 60 * 60 * 1_000)
    throw new Error("analytics requested period must be exactly seven inclusive days");
}

export async function validateWorkbookZip(path: string): Promise<void> {
  const file = await open(path, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
  try {
    const stat = await file.stat();
    if (stat.size < 22) throw new Error("analytics workbook is not a ZIP file");
    if (stat.size > MAX_WORKBOOK_BYTES)
      throw new Error(`analytics workbook exceeds ${MAX_WORKBOOK_BYTES} bytes`);
    const head = Buffer.alloc(4);
    await file.read(head, 0, 4, 0);
    if (head.readUInt32LE(0) !== 0x04034b50)
      throw new Error("analytics workbook has an invalid ZIP signature");
    const tailLength = Math.min(stat.size, 65_557);
    const tail = Buffer.alloc(tailLength);
    await file.read(tail, 0, tailLength, stat.size - tailLength);
    const eocd = findTerminatingEocd(tail);
    if (eocd < 0 || eocd + 22 > tail.length)
      throw new Error("analytics workbook ZIP directory is missing");
    if (containsSignature(tail, 0x06064b50) || containsSignature(tail, 0x07064b50))
      throw new Error("analytics workbook ZIP64 is not supported");
    const diskNumber = tail.readUInt16LE(eocd + 4);
    const directoryDisk = tail.readUInt16LE(eocd + 6);
    const entriesOnDisk = tail.readUInt16LE(eocd + 8);
    const totalEntries = tail.readUInt16LE(eocd + 10);
    const directorySize = tail.readUInt32LE(eocd + 12);
    const directoryOffset = tail.readUInt32LE(eocd + 16);
    if (diskNumber !== 0 || directoryDisk !== 0 || entriesOnDisk !== totalEntries)
      throw new Error("analytics workbook must be a single-disk ZIP");
    if (totalEntries === 0xffff || directorySize === 0xffffffff || directoryOffset === 0xffffffff)
      throw new Error("analytics workbook ZIP64 is not supported");
    if (totalEntries > MAX_ZIP_ENTRIES)
      throw new Error(`analytics workbook has too many ZIP entries: ${totalEntries}`);
    if (directorySize > MAX_CENTRAL_DIRECTORY_BYTES)
      throw new Error(
        `analytics workbook central directory exceeds ${MAX_CENTRAL_DIRECTORY_BYTES} bytes`,
      );
    const absoluteEocd = stat.size - tailLength + eocd;
    if (directoryOffset + directorySize !== absoluteEocd)
      throw new Error("analytics workbook ZIP directory does not terminate at EOCD");
    const directory = Buffer.alloc(directorySize);
    const directoryRead = await file.read(directory, 0, directorySize, directoryOffset);
    if (directoryRead.bytesRead !== directorySize)
      throw new Error("analytics workbook ZIP directory is truncated");
    const members = readCentralDirectory(directory, totalEntries);
    for (const required of REQUIRED_MEMBERS) {
      if (!members.some((member) => member.name === required))
        throw new Error(`analytics workbook ZIP member is missing: ${required}`);
    }
    await validateAllLocalEntries(file, members, directoryOffset);
  } finally {
    await file.close();
  }
}

interface CentralMember {
  readonly name: string;
  readonly nameBytes: Buffer;
  readonly flags: number;
  readonly method: number;
  readonly crc32: number;
  readonly localOffset: number;
  readonly compressedSize: number;
  readonly uncompressedSize: number;
}

function readCentralDirectory(buffer: Buffer, expectedEntries: number): CentralMember[] {
  const members: CentralMember[] = [];
  const names = new Set<string>();
  const localOffsets = new Set<number>();
  let offset = 0;
  let entries = 0;
  let totalCompressed = 0n;
  let totalUncompressed = 0n;
  while (offset < buffer.length) {
    if (offset + 46 > buffer.length || buffer.readUInt32LE(offset) !== 0x02014b50) {
      throw new Error("analytics workbook ZIP central directory is malformed");
    }
    const nameLength = buffer.readUInt16LE(offset + 28);
    const extraLength = buffer.readUInt16LE(offset + 30);
    const commentLength = buffer.readUInt16LE(offset + 32);
    const diskStart = buffer.readUInt16LE(offset + 34);
    const flags = buffer.readUInt16LE(offset + 8);
    const method = buffer.readUInt16LE(offset + 10);
    const crc32 = buffer.readUInt32LE(offset + 16);
    const compressedSize = buffer.readUInt32LE(offset + 20);
    const uncompressedSize = buffer.readUInt32LE(offset + 24);
    const localOffset = buffer.readUInt32LE(offset + 42);
    if (nameLength === 0 || nameLength > MAX_MEMBER_NAME_BYTES)
      throw new Error("analytics workbook ZIP member name is invalid");
    if (diskStart !== 0) throw new Error("analytics workbook ZIP member uses another disk");
    if ((flags & 0x1) !== 0) throw new Error("analytics workbook ZIP encryption is not supported");
    if ((flags & ~0x800) !== 0)
      throw new Error("analytics workbook ZIP general-purpose flags are unsupported");
    if (!(method === 0 || method === 8))
      throw new Error(`analytics workbook ZIP compression method is unsupported: ${method}`);
    if (
      compressedSize === 0xffffffff ||
      uncompressedSize === 0xffffffff ||
      localOffset === 0xffffffff
    )
      throw new Error("analytics workbook ZIP64 member is not supported");
    if (uncompressedSize > MAX_UNCOMPRESSED_ENTRY_BYTES)
      throw new Error(
        `analytics workbook ZIP member exceeds ${MAX_UNCOMPRESSED_ENTRY_BYTES} uncompressed bytes`,
      );
    const end = offset + 46 + nameLength + extraLength + commentLength;
    if (end > buffer.length)
      throw new Error("analytics workbook ZIP central directory is truncated");
    const nameBytes = Buffer.from(buffer.subarray(offset + 46, offset + 46 + nameLength));
    const extraStart = offset + 46 + nameLength;
    validateExtraFields(buffer.subarray(extraStart, extraStart + extraLength), "central directory");
    const name = nameBytes.toString("utf8");
    if (names.has(name))
      throw new Error(`analytics workbook ZIP contains duplicate member: ${name}`);
    if (localOffsets.has(localOffset))
      throw new Error(`analytics workbook ZIP reuses local entry offset: ${localOffset}`);
    names.add(name);
    localOffsets.add(localOffset);
    totalCompressed += BigInt(compressedSize);
    totalUncompressed += BigInt(uncompressedSize);
    members.push(
      Object.freeze({
        name,
        nameBytes,
        flags,
        method,
        crc32,
        localOffset,
        compressedSize,
        uncompressedSize,
      }),
    );
    offset = end;
    entries += 1;
  }
  if (entries !== expectedEntries)
    throw new Error(
      `analytics workbook ZIP entry count mismatch: expected ${expectedEntries}, found ${entries}`,
    );
  if (totalUncompressed > BigInt(MAX_TOTAL_UNCOMPRESSED_BYTES))
    throw new Error(
      `analytics workbook exceeds ${MAX_TOTAL_UNCOMPRESSED_BYTES} aggregate uncompressed bytes`,
    );
  if (
    totalUncompressed > 0n &&
    (totalCompressed === 0n ||
      totalUncompressed > totalCompressed * BigInt(MAX_AGGREGATE_COMPRESSION_RATIO))
  )
    throw new Error(
      `analytics workbook exceeds aggregate compression ratio ${MAX_AGGREGATE_COMPRESSION_RATIO}:1`,
    );
  return members;
}

async function validateAllLocalEntries(
  file: Awaited<ReturnType<typeof open>>,
  members: readonly CentralMember[],
  directoryOffset: number,
): Promise<void> {
  const ranges: Array<{ readonly start: number; readonly end: number; readonly name: string }> = [];
  let actualCompressedBytes = 0n;
  let actualUncompressedBytes = 0n;
  for (const member of members) {
    if (member.localOffset + 30 > directoryOffset)
      throw new Error(`analytics workbook ZIP local entry offset is invalid: ${member.name}`);
    const header = Buffer.alloc(30);
    const result = await file.read(header, 0, header.length, member.localOffset);
    if (result.bytesRead !== header.length || header.readUInt32LE(0) !== 0x04034b50)
      throw new Error(`analytics workbook ZIP local entry is malformed: ${member.name}`);
    const localFlags = header.readUInt16LE(6);
    const localMethod = header.readUInt16LE(8);
    const localCrc32 = header.readUInt32LE(14);
    const localCompressedSize = header.readUInt32LE(18);
    const localUncompressedSize = header.readUInt32LE(22);
    const nameLength = header.readUInt16LE(26);
    const extraLength = header.readUInt16LE(28);
    if (localFlags !== member.flags || localMethod !== member.method)
      throw new Error(
        `analytics workbook ZIP central/local flags or method mismatch: ${member.name}`,
      );
    if (
      localCrc32 !== member.crc32 ||
      localCompressedSize !== member.compressedSize ||
      localUncompressedSize !== member.uncompressedSize
    )
      throw new Error(`analytics workbook ZIP central/local size or CRC mismatch: ${member.name}`);
    if (nameLength !== member.nameBytes.length)
      throw new Error(`analytics workbook ZIP central/local name length mismatch: ${member.name}`);
    const localName = Buffer.alloc(nameLength);
    const nameResult = await file.read(localName, 0, nameLength, member.localOffset + 30);
    if (nameResult.bytesRead !== nameLength || !localName.equals(member.nameBytes))
      throw new Error(`analytics workbook ZIP central/local name mismatch: ${member.name}`);
    const extra = Buffer.alloc(extraLength);
    const extraResult = await file.read(
      extra,
      0,
      extraLength,
      member.localOffset + 30 + nameLength,
    );
    if (extraResult.bytesRead !== extraLength)
      throw new Error(`analytics workbook ZIP local extra field is truncated: ${member.name}`);
    validateExtraFields(extra, `local member ${member.name}`);
    const dataOffset = member.localOffset + 30 + nameLength + extraLength;
    const dataEnd = dataOffset + member.compressedSize;
    if (dataEnd > directoryOffset)
      throw new Error(
        `analytics workbook ZIP local entry exceeds the data section: ${member.name}`,
      );
    const compressed = Buffer.alloc(member.compressedSize);
    const dataResult = await file.read(compressed, 0, compressed.length, dataOffset);
    if (dataResult.bytesRead !== compressed.length)
      throw new Error(`analytics workbook ZIP payload is truncated: ${member.name}`);
    const uncompressed = decodeMember(member, compressed);
    if (uncompressed.length !== member.uncompressedSize)
      throw new Error(`analytics workbook ZIP uncompressed size mismatch: ${member.name}`);
    if (crc32(uncompressed) !== member.crc32)
      throw new Error(`analytics workbook ZIP CRC32 mismatch: ${member.name}`);
    actualCompressedBytes += BigInt(compressed.length);
    actualUncompressedBytes += BigInt(uncompressed.length);
    assertAggregatePayloadLimits(actualCompressedBytes, actualUncompressedBytes);
    ranges.push(Object.freeze({ start: member.localOffset, end: dataEnd, name: member.name }));
  }
  ranges.sort((left, right) => left.start - right.start);
  let coveredUntil = 0;
  for (const current of ranges) {
    if (current.start < coveredUntil)
      throw new Error(`analytics workbook ZIP local entries overlap before: ${current.name}`);
    if (current.start > coveredUntil)
      throw new Error(
        `analytics workbook ZIP data section contains unreferenced bytes before: ${current.name}`,
      );
    coveredUntil = current.end;
  }
  if (coveredUntil !== directoryOffset)
    throw new Error("analytics workbook ZIP data section contains unreferenced trailing bytes");
}

function decodeMember(member: CentralMember, compressed: Buffer): Buffer {
  if (member.method === 0) {
    if (member.compressedSize !== member.uncompressedSize)
      throw new Error(`analytics workbook ZIP stored size mismatch: ${member.name}`);
    return compressed;
  }
  try {
    const decoded: unknown = inflateRawSync(compressed, {
      info: true,
      maxOutputLength: Math.min(member.uncompressedSize + 1, MAX_UNCOMPRESSED_ENTRY_BYTES + 1),
    });
    if (!isInflateInfoResult(decoded))
      throw new Error("bounded inflater did not return consumption metadata");
    if (decoded.engine.bytesWritten !== compressed.length)
      throw new Error(
        `decoder consumed ${decoded.engine.bytesWritten} of ${compressed.length} declared compressed bytes`,
      );
    return decoded.buffer;
  } catch (error) {
    throw new Error(
      `analytics workbook ZIP deflate payload is invalid: ${member.name}: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

interface InflateInfoResult {
  readonly buffer: Buffer;
  readonly engine: { readonly bytesWritten: number };
}

function isInflateInfoResult(value: unknown): value is InflateInfoResult {
  if (typeof value !== "object" || value === null) return false;
  const result = value as Record<string, unknown>;
  if (
    !Buffer.isBuffer(result.buffer) ||
    typeof result.engine !== "object" ||
    result.engine === null
  )
    return false;
  const engine = result.engine as Record<string, unknown>;
  return Number.isSafeInteger(engine.bytesWritten) && (engine.bytesWritten as number) >= 0;
}

function assertAggregatePayloadLimits(compressed: bigint, uncompressed: bigint): void {
  if (uncompressed > BigInt(MAX_TOTAL_UNCOMPRESSED_BYTES))
    throw new Error(
      `analytics workbook exceeds ${MAX_TOTAL_UNCOMPRESSED_BYTES} decoded aggregate uncompressed bytes`,
    );
  if (
    uncompressed > 0n &&
    (compressed === 0n || uncompressed > compressed * BigInt(MAX_AGGREGATE_COMPRESSION_RATIO))
  )
    throw new Error(
      `analytics workbook exceeds decoded aggregate compression ratio ${MAX_AGGREGATE_COMPRESSION_RATIO}:1`,
    );
}

function validateExtraFields(extra: Buffer, location: string): void {
  let offset = 0;
  while (offset < extra.length) {
    if (offset + 4 > extra.length)
      throw new Error(`analytics workbook ZIP ${location} extra field is malformed`);
    const identifier = extra.readUInt16LE(offset);
    const size = extra.readUInt16LE(offset + 2);
    const end = offset + 4 + size;
    if (end > extra.length)
      throw new Error(`analytics workbook ZIP ${location} extra field is truncated`);
    if (identifier === 0x0001)
      throw new Error(`analytics workbook ZIP64 extra field is not supported: ${location}`);
    offset = end;
  }
}

function crc32(bytes: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function findTerminatingEocd(buffer: Buffer): number {
  for (let index = buffer.length - 22; index >= 0; index -= 1) {
    if (buffer.readUInt32LE(index) !== 0x06054b50) continue;
    const commentLength = buffer.readUInt16LE(index + 20);
    if (index + 22 + commentLength === buffer.length) return index;
  }
  return -1;
}

function containsSignature(buffer: Buffer, signature: number): boolean {
  for (let index = 0; index <= buffer.length - 4; index += 1)
    if (buffer.readUInt32LE(index) === signature) return true;
  return false;
}

function isIsoDate(value: string): boolean {
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.valueOf()) && date.toISOString().slice(0, 10) === value;
}
