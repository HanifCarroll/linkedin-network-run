import { describe, expect, test } from "bun:test";
import { mkdtemp, readFile, truncate, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  assertInclusiveSevenDayRange,
  assertRequestedWorkbook,
  MAX_AGGREGATE_COMPRESSION_RATIO,
  MAX_CENTRAL_DIRECTORY_BYTES,
  MAX_TOTAL_UNCOMPRESSED_BYTES,
  MAX_WORKBOOK_BYTES,
  parseWorkbookFilename,
  validateWorkbookZip,
} from "../../src/analytics/workbook.ts";
import { centralDirectoryOffsets, localEntryOffsets, writeWorkbook } from "./helpers.ts";

describe("analytics workbook", () => {
  test("parses account and dates including duplicate suffix", () => {
    expect(
      parseWorkbookFilename("/tmp/AggregateAnalytics_Hanif Carroll_2026-07-20_2026-07-26 (2).xlsx"),
    ).toEqual({
      filename: "AggregateAnalytics_Hanif Carroll_2026-07-20_2026-07-26 (2).xlsx",
      account: "Hanif Carroll",
      startDate: "2026-07-20",
      endDate: "2026-07-26",
    });
  });

  test("rejects malformed filename and impossible dates", () => {
    expect(() => parseWorkbookFilename("analytics.xlsx")).toThrow(
      "invalid analytics workbook filename",
    );
    expect(() => parseWorkbookFilename("AggregateAnalytics_Me_2026-02-30_2026-03-08.xlsx")).toThrow(
      "invalid analytics workbook date",
    );
  });

  test("requires the exact account, exact period, and seven inclusive days", () => {
    const identity = parseWorkbookFilename("AggregateAnalytics_Hanif_2026-07-20_2026-07-26.xlsx");
    expect(() =>
      assertRequestedWorkbook(identity, {
        account: "Other",
        startDate: "2026-07-20",
        endDate: "2026-07-26",
      }),
    ).toThrow("account mismatch");
    expect(() =>
      assertRequestedWorkbook(identity, {
        account: "Hanif",
        startDate: "2026-07-21",
        endDate: "2026-07-27",
      }),
    ).toThrow("period mismatch");
    expect(() => assertInclusiveSevenDayRange("2026-07-20", "2026-07-27")).toThrow(
      "seven inclusive days",
    );
    expect(() => assertInclusiveSevenDayRange("2026-07-20", "2026-07-26")).not.toThrow();
  });

  test("validates ZIP signature and required members", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-zip-"));
    const valid = join(root, "valid.xlsx");
    await writeWorkbook(valid);
    await expect(validateWorkbookZip(valid)).resolves.toBeUndefined();
    const invalid = join(root, "invalid.xlsx");
    await writeFile(invalid, "not a zip");
    await expect(validateWorkbookZip(invalid)).rejects.toThrow("not a ZIP");
    const missing = join(root, "missing.xlsx");
    await writeWorkbook(missing, ["[Content_Types].xml"]);
    await expect(validateWorkbookZip(missing)).rejects.toThrow("xl/workbook.xml");
  });

  test("performs unzip-equivalent payload validation for stored and deflated members", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-payload-validation-"));
    const stored = join(root, "stored.xlsx");
    await writeWorkbook(stored);
    await expect(validateWorkbookZip(stored)).resolves.toBeUndefined();

    const deflated = join(root, "deflated.xlsx");
    await writeWorkbook(deflated, undefined, { method: "deflate" });
    await expect(validateWorkbookZip(deflated)).resolves.toBeUndefined();

    const badCrc = join(root, "bad-crc.xlsx");
    await writeWorkbook(badCrc);
    const crcBytes = await readFile(badCrc);
    const crcLocal = localEntryOffsets(crcBytes)[0];
    const crcCentral = centralDirectoryOffsets(crcBytes)[0];
    if (crcLocal === undefined || crcCentral === undefined)
      throw new Error("test ZIP has no first member");
    const dishonestCrc = (crcBytes.readUInt32LE(crcLocal + 14) + 1) >>> 0;
    crcBytes.writeUInt32LE(dishonestCrc, crcLocal + 14);
    crcBytes.writeUInt32LE(dishonestCrc, crcCentral + 16);
    await writeFile(badCrc, crcBytes);
    await expect(validateWorkbookZip(badCrc)).rejects.toThrow("CRC32 mismatch");

    const badDeflate = join(root, "bad-deflate.xlsx");
    await writeWorkbook(badDeflate, undefined, { method: "deflate" });
    const deflateBytes = await readFile(badDeflate);
    const deflateLocal = localEntryOffsets(deflateBytes)[0];
    if (deflateLocal === undefined) throw new Error("test ZIP has no local member");
    const deflateDataOffset =
      deflateLocal +
      30 +
      deflateBytes.readUInt16LE(deflateLocal + 26) +
      deflateBytes.readUInt16LE(deflateLocal + 28);
    deflateBytes[deflateDataOffset] = 0x07;
    await writeFile(badDeflate, deflateBytes);
    await expect(validateWorkbookZip(badDeflate)).rejects.toThrow("deflate payload is invalid");

    const dishonestSize = join(root, "dishonest-size.xlsx");
    await writeWorkbook(dishonestSize, undefined, { method: "deflate" });
    const sizeBytes = await readFile(dishonestSize);
    const sizeLocal = localEntryOffsets(sizeBytes)[0];
    const sizeCentral = centralDirectoryOffsets(sizeBytes)[0];
    if (sizeLocal === undefined || sizeCentral === undefined)
      throw new Error("test ZIP has no first member");
    const declared = sizeBytes.readUInt32LE(sizeLocal + 22) + 1;
    sizeBytes.writeUInt32LE(declared, sizeLocal + 22);
    sizeBytes.writeUInt32LE(declared, sizeCentral + 24);
    await writeFile(dishonestSize, sizeBytes);
    await expect(validateWorkbookZip(dishonestSize)).rejects.toThrow("uncompressed size mismatch");
  });

  test("rejects an orphan local record omitted from the central directory", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-orphan-local-"));
    const orphan = join(root, "orphan-local.xlsx");
    await writeWorkbook(orphan, undefined, {
      orphanLocalMembers: ["xl/orphan.xml"],
    });
    await expect(validateWorkbookZip(orphan)).rejects.toThrow("unreferenced trailing bytes");
  });

  test("rejects a valid deflate stream with a trailing declared payload byte", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-trailing-deflate-"));
    const trailing = join(root, "trailing-deflate.xlsx");
    await writeWorkbook(trailing, undefined, {
      method: "deflate",
      deflateTrailingBytes: Buffer.from([0]),
    });
    await expect(validateWorkbookZip(trailing)).rejects.toThrow("decoder consumed");
  });

  test("rejects ZIP64 extra fields, encryption, and unsupported compression methods", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-unsupported-zip-"));
    const zip64Extra = Buffer.from([0x01, 0x00, 0x00, 0x00]);
    for (const [name, extras] of [
      ["central", { centralExtra: zip64Extra }],
      ["local", { localExtra: zip64Extra }],
    ] as const) {
      const path = join(root, `zip64-${name}.xlsx`);
      await writeWorkbook(path, undefined, extras);
      await expect(validateWorkbookZip(path)).rejects.toThrow("ZIP64 extra field");
    }

    const encrypted = join(root, "encrypted.xlsx");
    await writeWorkbook(encrypted);
    const encryptedBytes = await readFile(encrypted);
    const encryptedLocal = localEntryOffsets(encryptedBytes)[0];
    const encryptedCentral = centralDirectoryOffsets(encryptedBytes)[0];
    if (encryptedLocal === undefined || encryptedCentral === undefined)
      throw new Error("test ZIP has no first member");
    encryptedBytes.writeUInt16LE(1, encryptedLocal + 6);
    encryptedBytes.writeUInt16LE(1, encryptedCentral + 8);
    await writeFile(encrypted, encryptedBytes);
    await expect(validateWorkbookZip(encrypted)).rejects.toThrow("encryption is not supported");

    const unsupported = join(root, "unsupported-method.xlsx");
    await writeWorkbook(unsupported);
    const unsupportedBytes = await readFile(unsupported);
    const unsupportedLocal = localEntryOffsets(unsupportedBytes)[0];
    const unsupportedCentral = centralDirectoryOffsets(unsupportedBytes)[0];
    if (unsupportedLocal === undefined || unsupportedCentral === undefined)
      throw new Error("test ZIP has no first member");
    unsupportedBytes.writeUInt16LE(99, unsupportedLocal + 8);
    unsupportedBytes.writeUInt16LE(99, unsupportedCentral + 10);
    await writeFile(unsupported, unsupportedBytes);
    await expect(validateWorkbookZip(unsupported)).rejects.toThrow(
      "compression method is unsupported",
    );
  });

  test("rejects oversized, unterminated, ZIP64, and multi-disk containers", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-malformed-zip-"));
    const oversized = join(root, "oversized.xlsx");
    await writeFile(oversized, "PK\u0003\u0004");
    await truncate(oversized, MAX_WORKBOOK_BYTES + 1);
    await expect(validateWorkbookZip(oversized)).rejects.toThrow("exceeds");

    const unterminated = join(root, "unterminated.xlsx");
    await writeWorkbook(unterminated);
    await writeFile(
      unterminated,
      Buffer.concat([await readFile(unterminated), Buffer.from("trailing")]),
    );
    await expect(validateWorkbookZip(unterminated)).rejects.toThrow("directory is missing");

    const zip64 = join(root, "zip64.xlsx");
    await writeWorkbook(zip64);
    const zip64Bytes = await readFile(zip64);
    zip64Bytes.writeUInt16LE(0xffff, zip64Bytes.length - 12);
    zip64Bytes.writeUInt16LE(0xffff, zip64Bytes.length - 14);
    await writeFile(zip64, zip64Bytes);
    await expect(validateWorkbookZip(zip64)).rejects.toThrow("ZIP64");

    const multiDisk = join(root, "multi-disk.xlsx");
    await writeWorkbook(multiDisk);
    const multiDiskBytes = await readFile(multiDisk);
    multiDiskBytes.writeUInt16LE(1, multiDiskBytes.length - 18);
    await writeFile(multiDisk, multiDiskBytes);
    await expect(validateWorkbookZip(multiDisk)).rejects.toThrow("single-disk");

    const centralTooLarge = join(root, "central-too-large.xlsx");
    await writeWorkbook(centralTooLarge);
    const centralBytes = await readFile(centralTooLarge);
    centralBytes.writeUInt32LE(MAX_CENTRAL_DIRECTORY_BYTES + 1, centralBytes.length - 10);
    await writeFile(centralTooLarge, centralBytes);
    await expect(validateWorkbookZip(centralTooLarge)).rejects.toThrow("central directory exceeds");
  });

  test("rejects duplicate names and aggregate ZIP bombs", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-zip-bomb-"));
    const duplicate = join(root, "duplicate.xlsx");
    await writeWorkbook(duplicate, ["[Content_Types].xml", "xl/workbook.xml", "xl/workbook.xml"]);
    await expect(validateWorkbookZip(duplicate)).rejects.toThrow("duplicate member");

    const aggregate = join(root, "aggregate.xlsx");
    await writeWorkbook(aggregate, [
      "[Content_Types].xml",
      "xl/workbook.xml",
      "xl/worksheets/sheet1.xml",
    ]);
    const aggregateBytes = await readFile(aggregate);
    const aggregateOffsets = centralDirectoryOffsets(aggregateBytes);
    const perEntry = Math.floor(MAX_TOTAL_UNCOMPRESSED_BYTES / 3) + 1;
    for (const offset of aggregateOffsets) aggregateBytes.writeUInt32LE(perEntry, offset + 24);
    await writeFile(aggregate, aggregateBytes);
    await expect(validateWorkbookZip(aggregate)).rejects.toThrow("aggregate uncompressed bytes");

    const ratio = join(root, "ratio.xlsx");
    await writeWorkbook(ratio);
    const ratioBytes = await readFile(ratio);
    for (const offset of centralDirectoryOffsets(ratioBytes))
      ratioBytes.writeUInt32LE(1_000_000, offset + 24);
    await writeFile(ratio, ratioBytes);
    await expect(validateWorkbookZip(ratio)).rejects.toThrow(
      `compression ratio ${MAX_AGGREGATE_COMPRESSION_RATIO}:1`,
    );
  });

  test("validates every central member against its local header", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-local-header-"));

    const method = join(root, "method.xlsx");
    await writeWorkbook(method);
    const methodBytes = await readFile(method);
    const methodCentral = centralDirectoryOffsets(methodBytes)[0];
    if (methodCentral === undefined) throw new Error("test ZIP has no central directory");
    methodBytes.writeUInt16LE(8, methodCentral + 10);
    await writeFile(method, methodBytes);
    await expect(validateWorkbookZip(method)).rejects.toThrow("flags or method mismatch");

    const name = join(root, "name.xlsx");
    await writeWorkbook(name);
    const nameBytes = await readFile(name);
    nameBytes[30] = (nameBytes[30] ?? 0) ^ 1;
    await writeFile(name, nameBytes);
    await expect(validateWorkbookZip(name)).rejects.toThrow("central/local name mismatch");

    const size = join(root, "size.xlsx");
    await writeWorkbook(size);
    const sizeBytes = await readFile(size);
    const sizeCentral = centralDirectoryOffsets(sizeBytes)[0];
    if (sizeCentral === undefined) throw new Error("test ZIP has no central directory");
    sizeBytes.writeUInt32LE(sizeBytes.readUInt32LE(sizeCentral + 20) + 1, sizeCentral + 20);
    await writeFile(size, sizeBytes);
    await expect(validateWorkbookZip(size)).rejects.toThrow("size or CRC mismatch");

    const reusedOffset = join(root, "reused-offset.xlsx");
    await writeWorkbook(reusedOffset);
    const reusedBytes = await readFile(reusedOffset);
    const reusedCentral = centralDirectoryOffsets(reusedBytes);
    const first = reusedCentral[0];
    const second = reusedCentral[1];
    if (first === undefined || second === undefined)
      throw new Error("test ZIP has too few central members");
    reusedBytes.writeUInt32LE(reusedBytes.readUInt32LE(first + 42), second + 42);
    await writeFile(reusedOffset, reusedBytes);
    await expect(validateWorkbookZip(reusedOffset)).rejects.toThrow("reuses local entry offset");

    const descriptorFlag = join(root, "descriptor-flag.xlsx");
    await writeWorkbook(descriptorFlag);
    const descriptorBytes = await readFile(descriptorFlag);
    const descriptorCentral = centralDirectoryOffsets(descriptorBytes)[0];
    if (descriptorCentral === undefined) throw new Error("test ZIP has no central directory");
    descriptorBytes.writeUInt16LE(0x8, descriptorCentral + 8);
    descriptorBytes.writeUInt16LE(0x8, 6);
    await writeFile(descriptorFlag, descriptorBytes);
    await expect(validateWorkbookZip(descriptorFlag)).rejects.toThrow("flags are unsupported");
  });
});
