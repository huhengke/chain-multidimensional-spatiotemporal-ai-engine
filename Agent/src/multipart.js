import Busboy from "busboy";

export function receiveSingleFile(req, { maxBytes }) {
  return new Promise((resolve, reject) => {
    let parser;
    try {
      parser = Busboy({
        headers: req.headers,
        limits: { files: 1, fileSize: maxBytes, fields: 4, parts: 5 },
      });
    } catch (cause) {
      reject(Object.assign(new Error("上传请求必须使用 multipart/form-data"), {
        code: "INVALID_MULTIPART",
        status: 400,
        cause,
      }));
      return;
    }

    let settled = false;
    let found = false;
    let fileResult = null;
    const fail = (error) => {
      if (settled) return;
      settled = true;
      reject(error);
    };

    parser.on("file", (field, stream, info) => {
      if (field !== "file" || found) {
        stream.resume();
        fail(Object.assign(new Error("每次只能上传一个 file 附件"), {
          code: "ATTACHMENT_FILE_REQUIRED",
          status: 422,
        }));
        return;
      }
      found = true;
      const chunks = [];
      let size = 0;
      stream.on("data", (chunk) => {
        size += chunk.length;
        chunks.push(chunk);
      });
      stream.on("limit", () => fail(Object.assign(new Error("附件超过 50 MiB"), {
        code: "ATTACHMENT_TOO_LARGE",
        status: 413,
      })));
      stream.on("error", fail);
      stream.on("end", () => {
        fileResult = {
          name: String(info.filename || "attachment"),
          mimeType: String(info.mimeType || "application/octet-stream"),
          data: Buffer.concat(chunks, size),
        };
      });
    });
    parser.on("filesLimit", () => fail(Object.assign(new Error("每次只能上传一个附件"), {
      code: "TOO_MANY_FILES",
      status: 422,
    })));
    parser.on("error", fail);
    parser.on("finish", () => {
      if (settled) return;
      if (!found || !fileResult) {
        fail(Object.assign(new Error("缺少 file 附件"), {
          code: "ATTACHMENT_FILE_REQUIRED",
          status: 422,
        }));
        return;
      }
      settled = true;
      resolve(fileResult);
    });
    req.pipe(parser);
  });
}
