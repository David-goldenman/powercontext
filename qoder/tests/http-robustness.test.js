// 回归：非法 Host 头（如 "Host: ["）曾使 new URL 在 async 回调中抛 ERR_INVALID_URL，
// 未捕获拒绝导致整个服务进程退出码 1。修复后服务必须存活且能继续正常响应。
const test = require("node:test");
const assert = require("node:assert");
const net = require("node:net");
const { createServer } = require("../src/server");

let server;
let port;

test.before(async () => {
  server = createServer();
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  port = server.address().port;
});

test.after(() => {
  return new Promise((r) => server.close(r));
});

function rawRequest(raw) {
  return new Promise((resolve, reject) => {
    const sock = net.connect(port, "127.0.0.1", () => sock.write(raw));
    let buf = "";
    sock.setTimeout(5000, () => reject(new Error("原始请求超时，服务可能已崩溃")));
    sock.setEncoding("utf8");
    sock.on("data", (d) => (buf += d));
    sock.on("close", () => resolve(buf));
    sock.on("error", reject);
  });
}

test("Host: [ 的原始请求得到合法 HTTP 响应而非进程崩溃", async () => {
  const resp = await rawRequest("GET / HTTP/1.1\r\nHost: [\r\nConnection: close\r\n\r\n");
  assert.match(resp, /^HTTP\/1\.1 (200|400) /, "实际响应: " + resp.slice(0, 120));
});

test("恶意请求行（含 CRLF 注入尝试的 Host 头变体）不产生第二个响应头错乱", async () => {
  const resp = await rawRequest(
    'GET / HTTP/1.1\r\nHost: ]" x-evil: 1\r\nConnection: close\r\n\r\n'
  );
  assert.match(resp, /^HTTP\/1\.1 (200|400) /);
});

test("非法 Host 请求之后，服务仍然存活并可继续正常响应静态页", async () => {
  const resp = await rawRequest("GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n");
  assert.match(resp, /^HTTP\/1\.1 200 /, "实际响应: " + resp.slice(0, 120));
  assert.ok(resp.includes("科研证据卡"), "健康服务应返回展示页内容");
});

test("绝对形式请求行也可正常路由（固定 base 解析）", async () => {
  const resp = await rawRequest(
    "GET http://any-origin.invalid/ HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
  );
  assert.match(resp, /^HTTP\/1\.1 (200|400|404) /);
});
