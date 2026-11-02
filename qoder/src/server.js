const http = require("http");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { config } = require("./config");
const { getPool, initSchema } = require("./db");
const EvidenceCore = require("../public/evidence-core");

const PUBLIC_DIR = path.join(__dirname, "..", "public");
const STATUSES = new Set(["unread", "reading", "read"]);
const MAX_BODY = 2 * 1024 * 1024;

function json(res, code, data) {
  const body = JSON.stringify(data);
  res.writeHead(code, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(Object.assign(new Error("请求体过大"), { statusCode: 413 }));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => {
      if (!chunks.length) return resolve({});
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch {
        reject(Object.assign(new Error("请求体不是合法 JSON"), { statusCode: 400 }));
      }
    });
    req.on("error", reject);
  });
}

function escapeLike(s) {
  return String(s).replace(/[\\%_]/g, (m) => "\\" + m);
}

function validateNoteInput(body) {
  const title = String(body.title || "").trim();
  const url = String(body.url || "").trim();
  const content = String(body.content || "").trim();
  if (!title) throw Object.assign(new Error("title 不能为空"), { statusCode: 400 });
  if (title.length > 500) throw Object.assign(new Error("title 过长"), { statusCode: 400 });
  if (!/^https?:\/\//i.test(url))
    throw Object.assign(new Error("url 必须以 http:// 或 https:// 开头"), { statusCode: 400 });
  if (url.length > 2000) throw Object.assign(new Error("url 过长"), { statusCode: 400 });
  if (content.length < 10) throw Object.assign(new Error("content 至少 10 个字符"), { statusCode: 400 });
  return { title, url, content };
}

const rowCard = (r) => ({
  id: r.id,
  title: r.title,
  url: r.url,
  status: r.status,
  progress: r.progress,
  updatedAt: r.updated_at,
});

async function handleApi(req, res, url) {
  const seg = url.pathname.split("/").filter(Boolean); // ['api', ...]

  if (req.method === "GET" && url.pathname === "/api/health") {
    const pool = getPool();
    const [rows] = await pool.query("SELECT VERSION() AS v");
    return json(res, 200, { ok: true, version: rows[0].v, database: config.database });
  }

  if (url.pathname === "/api/notes" && req.method === "GET") {
    const status = url.searchParams.get("status");
    const limitRaw = Number(url.searchParams.get("limit"));
    const limit = Math.max(1, Math.min(Number.isFinite(limitRaw) ? Math.trunc(limitRaw) : 200, 500));
    const pool = getPool();
    let rows;
    if (status && STATUSES.has(status)) {
      [rows] = await pool.query(
        "SELECT id,title,url,content,status,progress,updated_at FROM notes WHERE status = ? ORDER BY updated_at DESC LIMIT " + limit,
        [status]
      );
    } else {
      [rows] = await pool.query(
        "SELECT id,title,url,content,status,progress,updated_at FROM notes ORDER BY updated_at DESC LIMIT " + limit
      );
    }
    return json(res, 200, {
      notes: rows.map((r) => ({
        ...rowCard(r),
        preview: r.content.slice(0, 80),
        length: r.content.length,
      })),
    });
  }

  if (url.pathname === "/api/notes" && req.method === "POST") {
    const body = await readBody(req);
    const { title, url: u, content } = validateNoteInput(body);
    const id = crypto.randomUUID();
    const pool = getPool();
    await pool.execute(
      "INSERT INTO notes (id, title, url, content, status, progress) VALUES (?, ?, ?, ?, 'unread', 0)",
      [id, title, u, content]
    );
    const [rows] = await pool.query(
      "SELECT id,title,url,status,progress,updated_at FROM notes WHERE id = ?",
      [id]
    );
    return json(res, 201, { note: rowCard(rows[0]) });
  }

  if (seg[0] === "api" && seg[1] === "notes" && seg[2] && req.method === "GET" && seg.length === 3) {
    const pool = getPool();
    const [rows] = await pool.query(
      "SELECT id,title,url,content,status,progress,created_at,updated_at FROM notes WHERE id = ?",
      [seg[2]]
    );
    if (!rows.length) return json(res, 404, { error: "笔记不存在" });
    return json(res, 200, { note: rows[0] });
  }

  if (seg[0] === "api" && seg[1] === "notes" && seg[2] && req.method === "PATCH" && seg.length === 3) {
    const body = await readBody(req);
    const fields = [];
    const params = [];
    if (body.status !== undefined) {
      if (!STATUSES.has(body.status))
        return json(res, 400, { error: "status 必须是 unread/reading/read" });
      fields.push("status = ?");
      params.push(body.status);
    }
    if (body.progress !== undefined) {
      const p = Number(body.progress);
      if (!Number.isInteger(p) || p < 0 || p > 100)
        return json(res, 400, { error: "progress 必须是 0-100 的整数" });
      fields.push("progress = ?");
      params.push(p);
    }
    if (!fields.length) return json(res, 400, { error: "没有可更新字段" });
    params.push(seg[2]);
    const pool = getPool();
    const [result] = await pool.execute(
      `UPDATE notes SET ${fields.join(", ")} WHERE id = ?`,
      params
    );
    if (result.affectedRows === 0) return json(res, 404, { error: "笔记不存在" });
    const [rows] = await pool.query(
      "SELECT id,title,url,status,progress,updated_at FROM notes WHERE id = ?",
      [seg[2]]
    );
    return json(res, 200, { note: rowCard(rows[0]) });
  }

  if (seg[0] === "api" && seg[1] === "notes" && seg[2] && req.method === "DELETE" && seg.length === 3) {
    const pool = getPool();
    const [result] = await pool.execute("DELETE FROM notes WHERE id = ?", [seg[2]]);
    if (result.affectedRows === 0) return json(res, 404, { error: "笔记不存在" });
    return json(res, 200, { deleted: seg[2] });
  }

  if (url.pathname === "/api/search" && req.method === "GET") {
    const q = url.searchParams.get("q") || "";
    const terms = EvidenceCore.tokenizeTerms(q);
    if (!terms.length)
      return json(res, 200, { query: q, terms, cards: [], matchedCandidates: 0, scanned: 0, truncated: false });
    const conds = terms.map(() => "(title LIKE ? OR content LIKE ?)");
    const params = [];
    for (const t of terms) params.push("%" + escapeLike(t) + "%", "%" + escapeLike(t) + "%");
    const pool = getPool();
    // 以稳定 id 游标分页扫描全部候选（参数化），逐页评分、只保留全局前 TOP_K，
    // 避免先无序 LIMIT 再评分时丢失后页的最佳命中；也避免无限堆积内存。
    const PAGE = 200;
    const MAX_SCAN = 5000;
    const TOP_K = 50;
    let cursor = "";
    let scanned = 0;
    let matched = 0;
    let scanCapped = false;
    let top = [];
    for (;;) {
      const [rows] = await pool.query(
        `SELECT id,title,url,content,status,progress FROM notes WHERE (${conds.join(" OR ")}) AND id > ? ORDER BY id LIMIT ${PAGE}`,
        [...params, cursor]
      );
      scanned += rows.length;
      if (rows.length) cursor = rows[rows.length - 1].id;
      const pageCards = EvidenceCore.searchNotes(rows, q);
      matched += pageCards.length;
      top = top
        .concat(pageCards)
        .sort((a, b) => b.score - a.score || String(a.title).localeCompare(String(b.title)) || (a.id < b.id ? -1 : 1));
      if (top.length > TOP_K) top.length = TOP_K;
      if (rows.length < PAGE) break;
      if (scanned >= MAX_SCAN) {
        scanCapped = true;
        break;
      }
    }
    return json(res, 200, {
      query: q,
      terms,
      cards: top,
      matchedCandidates: matched,
      scanned,
      truncated: scanCapped || matched > top.length,
      note: scanCapped
        ? `候选极多，仅扫描了前 ${scanned} 条；返回得分最高的 ${top.length} 张证据卡`
        : matched > top.length
          ? `共命中 ${matched} 条候选，返回得分最高的 ${top.length} 张证据卡`
          : null,
    });
  }

  return json(res, 404, { error: "接口不存在" });
}

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
};

function serveStatic(req, res, url) {
  let file = url.pathname === "/" ? "/index.html" : url.pathname;
  if (file === "/app") file = "/app.html";
  const full = path.normalize(path.join(PUBLIC_DIR, file));
  if (!full.startsWith(PUBLIC_DIR)) {
    res.writeHead(403);
    return res.end("Forbidden");
  }
  if (!fs.existsSync(full) || !fs.statSync(full).isFile()) {
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    return res.end("404 Not Found");
  }
  res.writeHead(200, { "Content-Type": MIME[path.extname(full)] || "application/octet-stream" });
  fs.createReadStream(full).pipe(res);
}

function createServer() {
  return http.createServer(async (req, res) => {
    // 固定可信 base 解析请求 URL：绝不拿外部 Host 头当 base，
    // 否则非法 Host（如 "["）会让 new URL 在 async 回调里抛出 ERR_INVALID_URL，
    // 形成未捕获拒绝并使服务进程退出。
    let url;
    try {
      url = new URL(req.url, "http://127.0.0.1");
    } catch {
      return json(res, 400, { error: "非法请求 URL" });
    }
    try {
      if (url.pathname.startsWith("/api/")) {
        await handleApi(req, res, url);
      } else if (req.method === "GET") {
        serveStatic(req, res, url);
      } else {
        json(res, 405, { error: "方法不允许" });
      }
    } catch (err) {
      const code = err.statusCode || 500;
      // 数据库等后端故障必须显式返回错误，绝不返回假数据。
      const detail =
        code === 500 ? `后端服务或数据库不可用：${err.code || ""} ${err.message || err}` : err.message;
      json(res, code, { error: detail });
    }
  });
}

async function start(port) {
  await initSchema();
  const server = createServer();
  const p = port !== undefined ? port : config.httpPort;
  await new Promise((r) => server.listen(p, "127.0.0.1", r));
  return { server, port: server.address().port };
}

if (require.main === module) {
  start().then(({ port }) => {
    console.log(`Evidence Desk 已启动: http://127.0.0.1:${port} （数据库 ${config.host}:${config.port}/${config.database}）`);
  }).catch((err) => {
    console.error("启动失败（数据库初始化未成功，未提供假服务）:", err.message);
    process.exit(1);
  });
}

module.exports = { createServer, start };
