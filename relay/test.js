// Run: node relay/test.js   (no network; upstream is mocked)
const assert = require("node:assert");
const handler = require("./api/stock.js");
process.env.RELAY_SECRET = "a-very-long-test-secret";

function call(req, fetchImpl) {
  return new Promise((resolve) => {
    const res = { headers: {}, setHeader(k, v) { this.headers[k] = v; }, end(b) { resolve({ status: this.statusCode, body: b, headers: this.headers }); } };
    handler(req, res, fetchImpl);
  });
}
const ok = { method: "GET", url: "/api/stock?stateCode=16&componentId=12", headers: { "x-relay-secret": process.env.RELAY_SECRET } };

(async () => {
  assert.equal((await call({ ...ok, method: "POST" })).status, 405);
  assert.equal((await call({ ...ok, headers: {} })).status, 401);
  assert.equal((await call({ ...ok, headers: { "x-relay-secret": "wrong-secret-value!!" } })).status, 401);
  assert.equal((await call({ ...ok, url: "/api/stock?stateCode=16&componentId=1;rm" })).status, 400);
  assert.equal((await call({ ...ok, url: "/api/stock?stateCode=http://evil&componentId=12" })).status, 400);

  let seen;
  const good = await call(ok, async (u) => { seen = String(u); return new Response('[{"hospitalCode":"1"}]', { status: 200 }); });
  assert.equal(good.status, 200);
  assert.ok(seen.startsWith("https://eraktkosh.mohfw.gov.in/eraktkoshPortal/eraktkosh/blood-availability?"));
  assert.ok(seen.includes("stateCode=16") && seen.includes("componentId=12"));

  assert.equal((await call(ok, async () => new Response("denied", { status: 403 }))).status, 403);
  const rl = await call(ok, async () => new Response("slow", { status: 429, headers: { "retry-after": "30" } }));
  assert.equal(rl.status, 429); assert.equal(rl.headers["Retry-After"], "30");
  const t = await call(ok, async () => { const e = new Error("t"); e.name = "TimeoutError"; throw e; });
  assert.equal(t.status, 504);
  console.log("relay tests passed");
})();

// Verify the nearest endpoint is secret-protected and only targets the fixed upstream.
const nearestHandler = require("./api/nearest.js");
function callNearest(req, fetchImpl) {
  return new Promise((resolve) => { const res = { headers: {}, setHeader(k,v){this.headers[k]=v;}, end(b){resolve({status:this.statusCode,body:b,headers:this.headers});} }; nearestHandler(req,res,fetchImpl); });
}
(async () => {
  const base = { method: "GET", url: "/api/nearest?latitude=20.5937&longitude=78.9629&radius=2000000", headers: { "x-relay-secret": process.env.RELAY_SECRET } };
  assert.equal((await callNearest({ ...base, headers: {} })).status, 401);
  assert.equal((await callNearest({ ...base, url: "/api/nearest?latitude=999&longitude=78&radius=2000000" })).status, 400);
  let seen;
  const good = await callNearest(base, async (u) => { seen=String(u); return new Response('[{"hospitalCode":"25001","latitude":24.8,"longitude":93.9,"dist":1200}]', {status:200}); });
  assert.equal(good.status, 200);
  assert.ok(seen.startsWith("https://eraktkosh.mohfw.gov.in/eraktkoshPortal/eraktkosh/bloodbank/nearest?"));
  assert.ok(seen.includes("latitude=20.5937") && seen.includes("radius=2000000"));
  console.log("nearest relay tests passed");
})();
