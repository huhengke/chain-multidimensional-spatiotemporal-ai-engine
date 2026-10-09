import { createHash, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { mkdir, readFile, rename, stat, unlink, writeFile } from "node:fs/promises";
import { basename, dirname, extname, join } from "node:path";

import { load as loadHtml } from "cheerio";
import * as mammoth from "mammoth";
import * as XLSX from "xlsx";
import WordExtractor from "word-extractor";
import yauzl from "yauzl";

const IMAGE_MAX_BYTES = 20 * 1024 * 1024;
const DOCUMENT_MAX_BYTES = 50 * 1024 * 1024;
const PDF_MAX_PAGES = 200;
const WORKBOOK_MAX_SHEETS = 50;
const WORKBOOK_MAX_CELLS = 200_000;
const MAX_ZIP_ENTRIES = 10_000;
const MAX_ZIP_UNCOMPRESSED = 250 * 1024 * 1024;
const MAX_CHUNK_CHARS = 3_000;
const DRAFT_TTL_MS = 24 * 60 * 60 * 1000;

const TYPE_BY_EXTENSION = Object.freeze({
  ".png": { kind: "image", type: "png", mime: "image/png" },
  ".jpg": { kind: "image", type: "jpeg", mime: "image/jpeg" },
  ".jpeg": { kind: "image", type: "jpeg", mime: "image/jpeg" },
  ".webp": { kind: "image", type: "webp", mime: "image/webp" },
  ".gif": { kind: "image", type: "gif", mime: "image/gif" },
  ".pdf": { kind: "document", type: "pdf", mime: "application/pdf" },
  ".docx": { kind: "document", type: "docx", mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" },
  ".doc": { kind: "document", type: "doc", mime: "application/msword" },
  ".xlsx": { kind: "workbook", type: "xlsx", mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
  ".xls": { kind: "workbook", type: "xls", mime: "application/vnd.ms-excel" },
});

function attachmentError(message, code, status = 422, cause) {
  return Object.assign(new Error(message), { code, status, ...(cause ? { cause } : {}) });
}

function safeName(value) {
  const name = basename(String(value || "attachment")).replace(/[\u0000-\u001f<>:"/\\|?*]/g, "_").trim();
  return (name || "attachment").slice(0, 180);
}

function startsWith(buffer, bytes, offset = 0) {
  return bytes.every((value, index) => buffer[offset + index] === value);
}

function validateMagic(data, type) {
  if (type === "png") return startsWith(data, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  if (type === "jpeg") return startsWith(data, [0xff, 0xd8, 0xff]);
  if (type === "gif") return ["GIF87a", "GIF89a"].includes(data.subarray(0, 6).toString("ascii"));
  if (type === "webp") return data.subarray(0, 4).toString("ascii") === "RIFF" && data.subarray(8, 12).toString("ascii") === "WEBP";
  if (type === "pdf") return data.subarray(0, 1024).includes(Buffer.from("%PDF-"));
  if (type === "docx" || type === "xlsx") return startsWith(data, [0x50, 0x4b, 0x03, 0x04]);
  if (type === "doc" || type === "xls") return startsWith(data, [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]);
  return false;
}

async function writeJsonAtomic(path, value) {
  await mkdir(dirname(path), { recursive: true });
  const temp = `${path}.${randomUUID()}.tmp`;
  await writeFile(temp, JSON.stringify(value, null, 2), "utf8");
  await rename(temp, path);
}

async function publishObject(path, data) {
  try {
    const handle = await stat(path);
    if (handle.size !== data.byteLength) throw attachmentError("附件对象摘要冲突", "ATTACHMENT_CORRUPT", 500);
    return;
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  await mkdir(dirname(path), { recursive: true });
  const temp = `${path}.${randomUUID()}.tmp`;
  await writeFile(temp, data);
  try {
    await rename(temp, path);
  } catch (error) {
    await unlink(temp).catch(() => {});
    if (error.code !== "EEXIST" && error.code !== "EPERM") throw error;
  }
}

function openZip(buffer) {
  return new Promise((resolve, reject) => {
    yauzl.fromBuffer(buffer, { lazyEntries: true, validateEntrySizes: true }, (error, zip) => {
      if (error || !zip) reject(error || new Error("无法打开 OOXML"));
      else resolve(zip);
    });
  });
}

async function validateOoxml(buffer, requiredEntry) {
  const zip = await openZip(buffer).catch((cause) => {
    throw attachmentError("Office 文件结构损坏", "DOCUMENT_PARSE_FAILED", 422, cause);
  });
  return new Promise((resolve, reject) => {
    let entries = 0;
    let total = 0;
    let found = false;
    let encrypted = false;
    let settled = false;
    const fail = (cause) => {
      if (settled) return;
      settled = true;
      zip.close();
      reject(cause);
    };
    zip.on("entry", (entry) => {
      entries += 1;
      total += Number(entry.uncompressedSize || 0);
      if (entry.fileName === requiredEntry) found = true;
      if (/EncryptedPackage|EncryptionInfo/i.test(entry.fileName)) encrypted = true;
      const compressed = Number(entry.compressedSize || 0);
      const ratio = compressed > 0 ? Number(entry.uncompressedSize || 0) / compressed : 0;
      if (entries > MAX_ZIP_ENTRIES || total > MAX_ZIP_UNCOMPRESSED || (ratio > 200 && entry.uncompressedSize > 1024 * 1024)) {
        fail(attachmentError("Office 文件解压规模超出安全限制", "DOCUMENT_ARCHIVE_LIMIT", 422));
        return;
      }
      zip.readEntry();
    });
    zip.on("error", (cause) => fail(attachmentError("Office 文件结构损坏", "DOCUMENT_PARSE_FAILED", 422, cause)));
    zip.on("end", () => {
      if (settled) return;
      settled = true;
      zip.close();
      if (encrypted) reject(attachmentError("不支持加密或受密码保护的 Office 文件", "DOCUMENT_ENCRYPTED", 422));
      else if (!found) reject(attachmentError("文件扩展名与 Office 内容不匹配", "ATTACHMENT_TYPE_MISMATCH", 422));
      else resolve();
    });
    zip.readEntry();
  });
}

function splitText(text, location, heading = "") {
  const normalized = String(text || "").replace(/\r\n?/g, "\n").replace(/[ \t]+\n/g, "\n").trim();
  if (!normalized) return [];
  const chunks = [];
  let remaining = normalized;
  let part = 1;
  while (remaining.length > MAX_CHUNK_CHARS) {
    let at = remaining.lastIndexOf("\n", MAX_CHUNK_CHARS);
    if (at < 1200) at = remaining.lastIndexOf("。", MAX_CHUNK_CHARS) + 1;
    if (at < 1200) at = MAX_CHUNK_CHARS;
    chunks.push({ location, heading, part, text: remaining.slice(0, at).trim() });
    remaining = remaining.slice(at).trim();
    part += 1;
  }
  if (remaining) chunks.push({ location, heading, part, text: remaining });
  return chunks;
}

function finalizeChunks(parts) {
  return parts.map((item, index) => ({
    id: `c${String(index + 1).padStart(4, "0")}`,
    ...item,
    char_count: item.text.length,
  }));
}

async function pdfModule() {
  return import("pdfjs-dist/legacy/build/pdf.mjs");
}

async function parsePdf(buffer) {
  try {
    const pdfjs = await pdfModule();
    const loading = pdfjs.getDocument({ data: new Uint8Array(buffer), disableWorker: true, useSystemFonts: true });
    const document = await loading.promise;
    if (document.numPages > PDF_MAX_PAGES) {
      await loading.destroy();
      throw attachmentError(`PDF 共 ${document.numPages} 页，超过 ${PDF_MAX_PAGES} 页限制`, "PDF_TOO_MANY_PAGES", 422);
    }
    const pages = [];
    const chunkParts = [];
    for (let number = 1; number <= document.numPages; number += 1) {
      const page = await document.getPage(number);
      const content = await page.getTextContent();
      const text = content.items.map((item) => typeof item.str === "string" ? item.str : "").join(" ").replace(/\s+/g, " ").trim();
      const viewport = page.getViewport({ scale: 1 });
      const visionRequired = text.length < 20;
      pages.push({ number, text_length: text.length, width: viewport.width, height: viewport.height, vision_required: visionRequired });
      chunkParts.push(...splitText(text, `第 ${number} 页`, `PDF 第 ${number} 页`));
      page.cleanup();
    }
    await loading.destroy();
    return {
      manifest: {
        page_count: pages.length,
        pages,
        has_extractable_text: pages.some((page) => page.text_length > 0),
        has_vision_pages: pages.some((page) => page.vision_required),
      },
      chunks: finalizeChunks(chunkParts),
    };
  } catch (cause) {
    if (cause.code) throw cause;
    const message = String(cause?.message || cause);
    if (/password/i.test(message)) throw attachmentError("不支持加密或受密码保护的 PDF", "DOCUMENT_ENCRYPTED", 422, cause);
    throw attachmentError("PDF 解析失败", "DOCUMENT_PARSE_FAILED", 422, cause);
  }
}

function tableText($, table) {
  const rows = [];
  $(table).find("tr").each((_rowIndex, row) => {
    const cells = [];
    $(row).find("th,td").each((_cellIndex, cell) => cells.push($(cell).text().replace(/\s+/g, " ").trim()));
    if (cells.some(Boolean)) rows.push(cells.join(" | "));
  });
  return rows.join("\n");
}

async function parseDocx(buffer) {
  try {
    await validateOoxml(buffer, "word/document.xml");
    const converted = await mammoth.convertToHtml({ buffer }, { includeDefaultStyleMap: true });
    const $ = loadHtml(converted.value || "", null, false);
    const parts = [];
    const outline = [];
    let heading = "";
    $("body").children().each((_index, node) => {
      const tag = String(node.tagName || "").toLowerCase();
      if (/^h[1-6]$/.test(tag)) {
        heading = $(node).text().replace(/\s+/g, " ").trim();
        if (heading) outline.push({ level: Number(tag.slice(1)), title: heading });
        return;
      }
      const text = tag === "table" ? tableText($, node) : $(node).text().replace(/\s+/g, " ").trim();
      if (text) parts.push(...splitText(text, tag === "table" ? `表格 ${outline.length + 1}` : `段落 ${parts.length + 1}`, heading));
    });
    if (!parts.length) {
      const raw = await mammoth.extractRawText({ buffer });
      parts.push(...splitText(raw.value, "正文"));
    }
    return {
      manifest: {
        outline,
        paragraph_count: parts.filter((item) => !item.location.startsWith("表格")).length,
        table_count: parts.filter((item) => item.location.startsWith("表格")).length,
        warnings: converted.messages.map((item) => String(item.message || "")).filter(Boolean).slice(0, 20),
        has_extractable_text: parts.length > 0,
      },
      chunks: finalizeChunks(parts),
    };
  } catch (cause) {
    if (cause.code) throw cause;
    throw attachmentError("DOCX 解析失败", "DOCUMENT_PARSE_FAILED", 422, cause);
  }
}

async function parseDoc(buffer) {
  try {
    const extractor = new WordExtractor();
    const document = await extractor.extract(buffer);
    const body = document.getBody();
    const chunks = finalizeChunks(splitText(body, "DOC 正文"));
    return {
      manifest: {
        paragraph_count: chunks.length,
        table_count: 0,
        has_extractable_text: chunks.length > 0,
        legacy_format: true,
      },
      chunks,
    };
  } catch (cause) {
    throw attachmentError("DOC 解析失败；请确认文件未加密且未损坏", "DOCUMENT_PARSE_FAILED", 422, cause);
  }
}

function jsonCellValue(value) {
  if (value instanceof Date) return { kind: "date", value: value.toISOString() };
  if (typeof value === "number") return { kind: "number", value };
  if (typeof value === "boolean") return { kind: "boolean", value };
  if (value === null || value === undefined) return { kind: "empty", value: null };
  return { kind: "text", value: String(value) };
}

function parseWorkbook(buffer, type) {
  try {
    const workbook = XLSX.read(buffer, {
      type: "buffer",
      cellDates: true,
      cellFormula: true,
      cellNF: true,
      cellText: true,
      bookVBA: false,
      WTF: true,
    });
    if (workbook.SheetNames.length > WORKBOOK_MAX_SHEETS) {
      throw attachmentError(`工作簿有 ${workbook.SheetNames.length} 个工作表，超过 ${WORKBOOK_MAX_SHEETS} 个限制`, "WORKBOOK_TOO_MANY_SHEETS", 422);
    }
    let totalCells = 0;
    const sheets = workbook.SheetNames.map((name, index) => {
      const sheet = workbook.Sheets[name];
      const cells = Object.keys(sheet)
        .filter((address) => !address.startsWith("!"))
        .map((address) => {
          const cell = sheet[address];
          const point = XLSX.utils.decode_cell(address);
          const parsed = jsonCellValue(cell.v);
          return {
            address,
            row: point.r,
            column: point.c,
            type: cell.t || parsed.kind,
            value_kind: parsed.kind,
            value: parsed.value,
            display: cell.w ?? (parsed.value === null ? "" : String(parsed.value)),
            ...(cell.f ? { formula: cell.f, cached_value_available: cell.v !== undefined && cell.v !== null } : {}),
            ...(cell.z ? { number_format: cell.z } : {}),
          };
        });
      totalCells += cells.length;
      const visibility = Number(workbook.Workbook?.Sheets?.[index]?.Hidden || 0);
      return {
        name,
        visibility: visibility === 2 ? "very_hidden" : visibility === 1 ? "hidden" : "visible",
        ref: sheet["!ref"] || null,
        non_empty_cells: cells.length,
        formula_cells: cells.filter((cell) => cell.formula).length,
        merges: (sheet["!merges"] || []).map((range) => XLSX.utils.encode_range(range)),
        cells,
      };
    });
    if (totalCells > WORKBOOK_MAX_CELLS) {
      throw attachmentError(`工作簿有 ${totalCells.toLocaleString()} 个非空单元格，超过 ${WORKBOOK_MAX_CELLS.toLocaleString()} 个限制`, "WORKBOOK_TOO_MANY_CELLS", 422);
    }
    return {
      manifest: {
        workbook_type: type,
        sheet_count: sheets.length,
        non_empty_cells: totalCells,
        sheets: sheets.map(({ cells, ...sheet }) => sheet),
      },
      workbook: { sheets },
    };
  } catch (cause) {
    if (cause.code) throw cause;
    throw attachmentError("Excel 工作簿解析失败；请确认文件未加密且未损坏", "DOCUMENT_PARSE_FAILED", 422, cause);
  }
}

function renderPdfPageWithPoppler(buffer, pageNumber) {
  return new Promise((resolve, reject) => {
    const child = spawn("pdftoppm", [
      "-f", String(pageNumber),
      "-l", String(pageNumber),
      "-singlefile",
      "-png",
      "-scale-to", "1800",
      "-",
    ], { windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
    const stdout = [];
    let size = 0;
    let stderr = "";
    const timer = setTimeout(() => child.kill(), 20_000);
    child.stdout.on("data", (chunk) => {
      size += chunk.length;
      if (size > 25 * 1024 * 1024) child.kill();
      else stdout.push(chunk);
    });
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => { stderr = `${stderr}${chunk}`.slice(-4000); });
    child.once("error", (cause) => {
      clearTimeout(timer);
      reject(cause);
    });
    child.once("close", (code) => {
      clearTimeout(timer);
      if (code === 0 && stdout.length) resolve(Buffer.concat(stdout, size));
      else reject(new Error(stderr.trim() || `pdftoppm 退出码 ${code}`));
    });
    child.stdin.end(buffer);
  });
}

async function renderPdfPageWithCanvas(buffer, pageNumber) {
  const pdfjs = await pdfModule();
  const { createCanvas } = await import("@napi-rs/canvas");
  const loading = pdfjs.getDocument({ data: new Uint8Array(buffer), disableWorker: true, useSystemFonts: true });
  const document = await loading.promise;
  try {
    if (!Number.isInteger(pageNumber) || pageNumber < 1 || pageNumber > document.numPages) {
      throw attachmentError("PDF 页码超出范围", "ATTACHMENT_PAGE_INVALID", 422);
    }
    const page = await document.getPage(pageNumber);
    const base = page.getViewport({ scale: 1 });
    const scale = Math.min(2, 1800 / Math.max(base.width, base.height));
    const viewport = page.getViewport({ scale });
    const canvas = createCanvas(Math.ceil(viewport.width), Math.ceil(viewport.height));
    await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
    page.cleanup();
    return canvas.toBuffer("image/png");
  } finally {
    await loading.destroy();
  }
}

async function renderPdfPage(buffer, pageNumber) {
  try {
    return await renderPdfPageWithCanvas(buffer, pageNumber);
  } catch (canvasCause) {
    try {
      return await renderPdfPageWithPoppler(buffer, pageNumber);
    } catch (popplerCause) {
      throw attachmentError(
        "扫描版 PDF 页面渲染不可用，请安装可用的 Canvas 原生包或 Poppler",
        "PDF_RENDERER_UNAVAILABLE",
        503,
        new AggregateError([canvasCause, popplerCause]),
      );
    }
  }
}

export function publicAttachment(meta, name = meta.name) {
  return {
    id: meta.id,
    kind: meta.kind,
    document_type: meta.document_type,
    name,
    mime_type: meta.mime_type,
    size: meta.size,
    sha256: meta.sha256,
    status: meta.status,
    ...(meta.page_count != null ? { page_count: meta.page_count } : {}),
    ...(meta.sheet_count != null ? { sheet_count: meta.sheet_count } : {}),
    ...(meta.non_empty_cells != null ? { non_empty_cells: meta.non_empty_cells } : {}),
    ...(meta.has_extractable_text != null ? { has_extractable_text: meta.has_extractable_text } : {}),
    ...(meta.has_vision_pages != null ? { has_vision_pages: meta.has_vision_pages } : {}),
  };
}

export class AttachmentStore {
  constructor(root) {
    this.root = root;
    this.objectsRoot = join(root, "objects");
    this.metadataRoot = join(root, "metadata");
    this.derivedRoot = join(root, "derived");
    this.draftsRoot = join(root, "drafts");
  }

  async ensure() {
    await Promise.all([
      mkdir(this.objectsRoot, { recursive: true }),
      mkdir(this.metadataRoot, { recursive: true }),
      mkdir(this.derivedRoot, { recursive: true }),
      mkdir(this.draftsRoot, { recursive: true }),
    ]);
  }

  metadataPath(id) { return join(this.metadataRoot, `${id}.json`); }
  derivedPath(id, file) { return join(this.derivedRoot, id, "v1", file); }
  draftPath(token) { return join(this.draftsRoot, `${token}.json`); }

  async metadata(id) {
    try {
      return JSON.parse(await readFile(this.metadataPath(id), "utf8"));
    } catch (cause) {
      if (cause.code === "ENOENT") throw attachmentError("附件不存在", "ATTACHMENT_NOT_FOUND", 404);
      throw cause;
    }
  }

  async objectPath(id) {
    const meta = await this.metadata(id);
    return join(this.objectsRoot, meta.sha256.slice(0, 2), meta.sha256);
  }

  async draft(sessionId, token, { allowConsumed = false } = {}) {
    let draft;
    try {
      draft = JSON.parse(await readFile(this.draftPath(String(token)), "utf8"));
    } catch {
      throw attachmentError("附件 token 无效或已过期", "ATTACHMENT_TOKEN_INVALID", 422);
    }
    if (draft.session_id !== sessionId) {
      throw attachmentError("附件不属于当前会话", "ATTACHMENT_SESSION_MISMATCH", 403);
    }
    if (!allowConsumed && draft.consumed_at) {
      throw attachmentError("附件 token 已被使用", "ATTACHMENT_TOKEN_CONSUMED", 409);
    }
    if (!draft.consumed_at && Date.parse(draft.expires_at) <= Date.now()) {
      throw attachmentError("附件 token 已过期", "ATTACHMENT_TOKEN_EXPIRED", 410);
    }
    return draft;
  }

  async upload(sessionId, input) {
    await this.ensure();
    const name = safeName(input.name);
    const typeInfo = TYPE_BY_EXTENSION[extname(name).toLowerCase()];
    if (!typeInfo) throw attachmentError("仅支持 PNG/JPEG/WebP/GIF、PDF、DOC/DOCX 和 XLS/XLSX", "ATTACHMENT_UNSUPPORTED_TYPE", 415);
    const limit = typeInfo.kind === "image" ? IMAGE_MAX_BYTES : DOCUMENT_MAX_BYTES;
    if (!input.data.length) throw attachmentError("附件不能为空", "ATTACHMENT_EMPTY", 422);
    if (input.data.length > limit) throw attachmentError(typeInfo.kind === "image" ? "图片超过 20 MiB" : "文档或工作簿超过 50 MiB", "ATTACHMENT_TOO_LARGE", 413);
    if (!validateMagic(input.data, typeInfo.type)) throw attachmentError("文件扩展名与实际内容不匹配", "ATTACHMENT_TYPE_MISMATCH", 422);
    if (typeInfo.type === "docx") await validateOoxml(input.data, "word/document.xml");
    if (typeInfo.type === "xlsx") await validateOoxml(input.data, "xl/workbook.xml");

    const sha256 = createHash("sha256").update(input.data).digest("hex");
    const id = `att_sha256_${sha256}`;
    const objectPath = join(this.objectsRoot, sha256.slice(0, 2), sha256);
    await mkdir(join(this.derivedRoot, id, "v1"), { recursive: true });
    await publishObject(objectPath, input.data);

    let parsed = { manifest: {}, chunks: [] };
    const existing = await readFile(this.metadataPath(id), "utf8").then(JSON.parse).catch(() => null);
    if (!existing) {
      if (typeInfo.type === "pdf") parsed = await parsePdf(input.data);
      else if (typeInfo.type === "docx") parsed = await parseDocx(input.data);
      else if (typeInfo.type === "doc") parsed = await parseDoc(input.data);
      else if (typeInfo.type === "xlsx" || typeInfo.type === "xls") parsed = parseWorkbook(input.data, typeInfo.type);

      if (parsed.chunks) await writeJsonAtomic(this.derivedPath(id, "chunks.json"), parsed.chunks);
      if (parsed.workbook) await writeJsonAtomic(this.derivedPath(id, "workbook.json"), parsed.workbook);
      await writeJsonAtomic(this.derivedPath(id, "manifest.json"), { version: 1, ...parsed.manifest });
    }

    const manifest = existing ? await this.manifest(id) : parsed.manifest;
    const meta = existing || {
      version: 1,
      id,
      kind: typeInfo.kind,
      document_type: typeInfo.type,
      name,
      mime_type: typeInfo.mime,
      size: input.data.length,
      sha256,
      status: "ready",
      created_at: new Date().toISOString(),
      ...(manifest.page_count != null ? { page_count: manifest.page_count } : {}),
      ...(manifest.sheet_count != null ? { sheet_count: manifest.sheet_count } : {}),
      ...(manifest.non_empty_cells != null ? { non_empty_cells: manifest.non_empty_cells } : {}),
      ...(manifest.has_extractable_text != null ? { has_extractable_text: manifest.has_extractable_text } : {}),
      ...(manifest.has_vision_pages != null ? { has_vision_pages: manifest.has_vision_pages } : {}),
    };
    if (!existing) await writeJsonAtomic(this.metadataPath(id), meta);

    const token = `draft_${randomUUID()}`;
    const draft = {
      version: 1,
      token,
      session_id: sessionId,
      attachment_id: id,
      name,
      created_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + DRAFT_TTL_MS).toISOString(),
      consumed_at: null,
      message_id: null,
    };
    await writeJsonAtomic(this.draftPath(token), draft);
    return { token, attachment: publicAttachment(meta, name) };
  }

  async manifest(id) {
    return JSON.parse(await readFile(this.derivedPath(id, "manifest.json"), "utf8"));
  }

  async resolveDrafts(sessionId, tokens) {
    if (!Array.isArray(tokens)) throw attachmentError("attachments 必须是数组", "ATTACHMENT_TOKENS_INVALID", 422);
    const unique = [...new Set(tokens.map(String))];
    if (unique.length !== tokens.length) throw attachmentError("附件 token 不能重复", "ATTACHMENT_TOKEN_DUPLICATE", 422);
    if (unique.length > 4) throw attachmentError("单条消息最多 4 个附件", "TOO_MANY_ATTACHMENTS", 422);
    const drafts = [];
    for (const token of unique) {
      const draft = await this.draft(sessionId, token);
      const meta = await this.metadata(draft.attachment_id);
      drafts.push({ draft, meta });
    }
    if (drafts.filter(({ meta }) => meta.kind !== "image").length > 2) {
      throw attachmentError("PDF、Word 与 Excel 合计最多 2 个", "TOO_MANY_DOCUMENTS", 422);
    }
    return drafts;
  }

  async consumeDrafts(sessionId, tokens, messageId) {
    const drafts = await this.resolveDrafts(sessionId, tokens);
    const consumedAt = new Date().toISOString();
    for (const { draft } of drafts) {
      await writeJsonAtomic(this.draftPath(draft.token), { ...draft, consumed_at: consumedAt, message_id: messageId });
    }
    return drafts.map(({ draft, meta }) => publicAttachment(meta, draft.name));
  }

  async deleteDraft(sessionId, token) {
    let draft;
    try {
      draft = JSON.parse(await readFile(this.draftPath(token), "utf8"));
    } catch {
      return false;
    }
    if (draft.session_id !== sessionId || draft.consumed_at) return false;
    await unlink(this.draftPath(token));
    return true;
  }

  async readChunks(id) {
    return readFile(this.derivedPath(id, "chunks.json"), "utf8").then(JSON.parse).catch(() => []);
  }

  async readWorkbook(id) {
    return JSON.parse(await readFile(this.derivedPath(id, "workbook.json"), "utf8"));
  }

  async toolRead(id, args = {}) {
    const meta = await this.metadata(id);
    const mode = String(args.mode || "outline");
    const manifest = await this.manifest(id);
    if (mode === "outline") return { attachment: publicAttachment(meta), manifest };
    if (mode === "search") {
      const query = String(args.query || "").trim().toLowerCase();
      if (!query) throw attachmentError("search 模式需要 query", "ATTACHMENT_QUERY_REQUIRED", 422);
      const chunks = await this.readChunks(id);
      return {
        attachment: publicAttachment(meta),
        matches: chunks.filter((chunk) => `${chunk.heading} ${chunk.text}`.toLowerCase().includes(query)).slice(0, 8).map((chunk) => ({
          id: chunk.id,
          location: chunk.location,
          heading: chunk.heading,
          excerpt: chunk.text.slice(0, 900),
        })),
      };
    }
    if (mode === "chunks") {
      const wanted = new Set((args.chunk_ids || []).map(String).slice(0, 8));
      const chunks = await this.readChunks(id);
      const selected = chunks.filter((chunk) => wanted.has(chunk.id));
      return { attachment: publicAttachment(meta), chunks: selected, omitted: Math.max(0, wanted.size - selected.length) };
    }
    if (mode === "pages") {
      if (meta.document_type !== "pdf") throw attachmentError("pages 模式仅适用于 PDF", "ATTACHMENT_MODE_INVALID", 422);
      const pages = [...new Set((args.pages || []).map(Number))].slice(0, 4);
      if (!pages.length) throw attachmentError("pages 模式需要页码", "ATTACHMENT_PAGE_REQUIRED", 422);
      const chunks = await this.readChunks(id);
      const textPages = [];
      const images = [];
      const source = await readFile(await this.objectPath(id));
      for (const number of pages) {
        const pageMeta = manifest.pages.find((page) => page.number === number);
        if (!pageMeta) throw attachmentError(`PDF 不存在第 ${number} 页`, "ATTACHMENT_PAGE_INVALID", 422);
        const text = chunks.filter((chunk) => chunk.location === `第 ${number} 页`).map((chunk) => chunk.text).join("\n");
        if (pageMeta.vision_required) images.push({ page: number, data: (await renderPdfPage(source, number)).toString("base64"), mimeType: "image/png" });
        else textPages.push({ page: number, text });
      }
      return { attachment: publicAttachment(meta), pages: textPages, images };
    }
    if (mode === "sheets") {
      if (meta.kind !== "workbook") throw attachmentError("sheets 模式仅适用于 Excel", "ATTACHMENT_MODE_INVALID", 422);
      return { attachment: publicAttachment(meta), sheets: manifest.sheets };
    }
    if (mode === "range") {
      if (meta.kind !== "workbook") throw attachmentError("range 模式仅适用于 Excel", "ATTACHMENT_MODE_INVALID", 422);
      const sheetName = String(args.sheet || "");
      const rangeText = String(args.range || "").toUpperCase();
      if (!sheetName || !rangeText) throw attachmentError("range 模式需要 sheet 和 range", "ATTACHMENT_RANGE_REQUIRED", 422);
      let range;
      try { range = XLSX.utils.decode_range(rangeText); } catch { throw attachmentError("Excel 区域格式无效", "ATTACHMENT_RANGE_INVALID", 422); }
      const area = (range.e.r - range.s.r + 1) * (range.e.c - range.s.c + 1);
      if (area > 1000) throw attachmentError("单次最多读取 1000 个单元格", "ATTACHMENT_RANGE_TOO_LARGE", 422);
      const workbook = await this.readWorkbook(id);
      const sheet = workbook.sheets.find((item) => item.name === sheetName);
      if (!sheet) throw attachmentError("工作表不存在", "ATTACHMENT_SHEET_NOT_FOUND", 404);
      const cells = sheet.cells.filter((cell) => cell.row >= range.s.r && cell.row <= range.e.r && cell.column >= range.s.c && cell.column <= range.e.c);
      return { attachment: publicAttachment(meta), sheet: sheetName, range: XLSX.utils.encode_range(range), cells };
    }
    throw attachmentError("不支持的附件读取模式", "ATTACHMENT_MODE_INVALID", 422);
  }
}

export const attachmentLimits = Object.freeze({
  max_attachments: 4,
  max_documents: 2,
  image_max_bytes: IMAGE_MAX_BYTES,
  document_max_bytes: DOCUMENT_MAX_BYTES,
  pdf_max_pages: PDF_MAX_PAGES,
  workbook_max_sheets: WORKBOOK_MAX_SHEETS,
  workbook_max_cells: WORKBOOK_MAX_CELLS,
});
