import { writeFile } from "node:fs/promises";
import { deflateRawSync } from "node:zlib";

interface WorkbookFixtureOptions {
  readonly method?: "stored" | "deflate";
  readonly localExtra?: Buffer;
  readonly centralExtra?: Buffer;
  readonly payload?: (member: string) => Buffer;
  readonly deflateTrailingBytes?: Buffer;
  readonly orphanLocalMembers?: readonly string[];
}

export async function writeWorkbook(
  path: string,
  members = ["[Content_Types].xml", "xl/workbook.xml"],
  options: WorkbookFixtureOptions = {},
): Promise<void> {
  const localParts: Buffer[] = [];
  const directoryParts: Buffer[] = [];
  const method = options.method === "deflate" ? 8 : 0;
  const localExtra = options.localExtra ?? Buffer.alloc(0);
  const centralExtra = options.centralExtra ?? Buffer.alloc(0);
  let offset = 0;
  for (const member of members) {
    const name = Buffer.from(member);
    const data = options.payload?.(member) ?? Buffer.from(`<${member}/>`);
    const compressed =
      method === 8
        ? Buffer.concat([deflateRawSync(data), options.deflateTrailingBytes ?? Buffer.alloc(0)])
        : data;
    const checksum = testCrc32(data);
    const local = Buffer.alloc(30 + name.length + localExtra.length + compressed.length);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(method, 8);
    local.writeUInt32LE(checksum, 14);
    local.writeUInt32LE(compressed.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    local.writeUInt16LE(localExtra.length, 28);
    name.copy(local, 30);
    localExtra.copy(local, 30 + name.length);
    compressed.copy(local, 30 + name.length + localExtra.length);
    localParts.push(local);

    const central = Buffer.alloc(46 + name.length + centralExtra.length);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(method, 10);
    central.writeUInt32LE(checksum, 16);
    central.writeUInt32LE(compressed.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt16LE(centralExtra.length, 30);
    central.writeUInt32LE(offset, 42);
    name.copy(central, 46);
    centralExtra.copy(central, 46 + name.length);
    directoryParts.push(central);
    offset += local.length;
  }
  for (const member of options.orphanLocalMembers ?? []) {
    const name = Buffer.from(member);
    const data = options.payload?.(member) ?? Buffer.from(`<${member}/>`);
    const compressed =
      method === 8
        ? Buffer.concat([deflateRawSync(data), options.deflateTrailingBytes ?? Buffer.alloc(0)])
        : data;
    const local = Buffer.alloc(30 + name.length + localExtra.length + compressed.length);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(method, 8);
    local.writeUInt32LE(testCrc32(data), 14);
    local.writeUInt32LE(compressed.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    local.writeUInt16LE(localExtra.length, 28);
    name.copy(local, 30);
    localExtra.copy(local, 30 + name.length);
    compressed.copy(local, 30 + name.length + localExtra.length);
    localParts.push(local);
    offset += local.length;
  }
  const directory = Buffer.concat(directoryParts);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(members.length, 8);
  end.writeUInt16LE(members.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  await writeFile(path, Buffer.concat([...localParts, directory, end]));
}

export function centralDirectoryOffsets(bytes: Buffer): number[] {
  const offsets: number[] = [];
  for (let offset = 0; offset <= bytes.length - 4; offset += 1)
    if (bytes.readUInt32LE(offset) === 0x02014b50) offsets.push(offset);
  return offsets;
}

export function localEntryOffsets(bytes: Buffer): number[] {
  const offsets: number[] = [];
  for (let offset = 0; offset <= bytes.length - 4; offset += 1)
    if (bytes.readUInt32LE(offset) === 0x04034b50) offsets.push(offset);
  return offsets;
}

export function testCrc32(bytes: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
