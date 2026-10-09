import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import * as XLSX from "xlsx";

import { AttachmentStore } from "../src/attachment-store.js";

const PNG_1X1 = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

const PDF_WITH_TEXT = Buffer.from(
  "JVBERi0xLjcKJcK1wrYKJSBXcml0dGVuIGJ5IE11UERGIDEuMjkuMAoKMSAwIG9iago8PC9UeXBlL0NhdGFsb2cvUGFnZXMgMiAwIFIvSW5mbzw8L1Byb2R1Y2VyKE11UERGIDEuMjkuMCk+Pj4+CmVuZG9iagoKMiAwIG9iago8PC9UeXBlL1BhZ2VzL0NvdW50IDEvS2lkc1s0IDAgUl0+PgplbmRvYmoKCjMgMCBvYmoKPDwvRm9udDw8L2hlbHYgNSAwIFI+Pj4+CmVuZG9iagoKNCAwIG9iago8PC9UeXBlL1BhZ2UvTWVkaWFCb3hbMCAwIDU5NSA4NDJdL1JvdGF0ZSAwL1Jlc291cmNlcyAzIDAgUi9QYXJlbnQgMiAwIFIvQ29udGVudHNbNiAwIFJdPj4KZW5kb2JqCgo1IDAgb2JqCjw8L1R5cGUvRm9udC9TdWJ0eXBlL1R5cGUxL0Jhc2VGb250L0hlbHZldGljYS9FbmNvZGluZy9XaW5BbnNpRW5jb2Rpbmc+PgplbmRvYmoKCjYgMCBvYmoKPDwvTGVuZ3RoIDgyPj4Kc3RyZWFtCgpxCkJUCjEgMCAwIDEgNzIgNzQ2IFRtCi9oZWx2IDE4IFRmIFs8NTA0NDQ2MjA0MTU0NTQ0MTQzNDg0ZDQ1NGU1NDIwNGY0Yj5dVEoKRVQKUQoKZW5kc3RyZWFtCmVuZG9iagoKeHJlZgowIDcKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDQyIDAwMDAwIG4gCjAwMDAwMDAxMjAgMDAwMDAgbiAKMDAwMDAwMDE3MiAwMDAwMCBuIAowMDAwMDAwMjEzIDAwMDAwIG4gCjAwMDAwMDAzMjAgMDAwMDAgbiAKMDAwMDAwMDQwOSAwMDAwMCBuIAoKdHJhaWxlcgo8PC9TaXplIDcvUm9vdCAxIDAgUi9JRFs8NjdDMzg1MkJDMjlFNTk0N0MyOUFDMjhCQzM5MzU5QzI+PEJDQzYyOTYyMUI0RTlBOUE5MzYyREJDMTRDNUUxQ0RCPl0+PgpzdGFydHhyZWYKNTQwCiUlRU9GCg==",
  "base64",
);

test("image drafts are session-bound and can only be consumed once", async () => {
  const root = await mkdtemp(join(tmpdir(), "chain-attachments-"));
  try {
    const store = new AttachmentStore(root);
    const uploaded = await store.upload("session-a", {
      name: "示意图.png",
      mimeType: "image/png",
      data: PNG_1X1,
    });
    assert.equal(uploaded.attachment.kind, "image");
    await assert.rejects(
      store.resolveDrafts("session-b", [uploaded.token]),
      (error) => error.code === "ATTACHMENT_SESSION_MISMATCH",
    );
    const consumed = await store.consumeDrafts("session-a", [uploaded.token], "message-1");
    assert.equal(consumed[0].name, "示意图.png");
    await assert.rejects(
      store.resolveDrafts("session-a", [uploaded.token]),
      (error) => error.code === "ATTACHMENT_TOKEN_CONSUMED",
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Excel attachments expose sheet metadata and bounded cell ranges without executing formulas", async () => {
  const root = await mkdtemp(join(tmpdir(), "chain-workbook-"));
  try {
    const workbook = XLSX.utils.book_new();
    const sheet = XLSX.utils.aoa_to_sheet([
      ["月份", "业务金额", "合计"],
      ["1月", 10, 10],
      ["2月", 20, 30],
    ]);
    sheet.C3 = { t: "n", f: "SUM(B2:B3)", v: 30 };
    XLSX.utils.book_append_sheet(workbook, sheet, "业务明细");
    const data = Buffer.from(XLSX.write(workbook, { type: "buffer", bookType: "xlsx" }));
    const store = new AttachmentStore(root);
    const uploaded = await store.upload("session-xlsx", {
      name: "业务.xlsx",
      mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      data,
    });

    assert.equal(uploaded.attachment.kind, "workbook");
    assert.equal(uploaded.attachment.sheet_count, 1);
    const sheets = await store.toolRead(uploaded.attachment.id, { mode: "sheets" });
    assert.equal(sheets.sheets[0].name, "业务明细");
    const range = await store.toolRead(uploaded.attachment.id, {
      mode: "range",
      sheet: "业务明细",
      range: "A1:C3",
    });
    assert.equal(range.cells.length, 9);
    const formula = range.cells.find((cell) => cell.address === "C3");
    assert.equal(formula.formula, "SUM(B2:B3)");
    assert.equal(formula.value, 30);
    await assert.rejects(
      store.toolRead(uploaded.attachment.id, { mode: "range", sheet: "业务明细", range: "A1:ZZ100" }),
      (error) => error.code === "ATTACHMENT_RANGE_TOO_LARGE",
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("PDF attachments keep extracted text after parser cleanup", async () => {
  const root = await mkdtemp(join(tmpdir(), "chain-pdf-"));
  try {
    const store = new AttachmentStore(root);
    const uploaded = await store.upload("session-pdf", {
      name: "acceptance.pdf",
      mimeType: "application/pdf",
      data: PDF_WITH_TEXT,
    });
    assert.equal(uploaded.attachment.page_count, 1);
    assert.equal(uploaded.attachment.has_extractable_text, true);
    const search = await store.toolRead(uploaded.attachment.id, { mode: "search", query: "attachment" });
    assert.equal(search.matches.length, 1);
    assert.match(search.matches[0].excerpt, /PDF ATTACHMENT OK/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
