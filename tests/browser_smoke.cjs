/* Local Chromium smoke test, no npm dependencies. All non-loopback page requests blocked. */
"use strict";
const {spawn} = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const net = require("node:net");
const root = path.resolve(__dirname, "..");
const output = path.join(root, "artifacts");
fs.mkdirSync(output, {recursive: true});
const browserPath = process.argv[2] || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
if (!fs.existsSync(browserPath)) throw new Error("Pass an installed Chromium browser path as the first argument.");
let server, browser, socket;
const pending = new Map(); let nextId = 0;
const consoleErrors = [], externalRequests = [];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function requireFreePort(port) {
  return new Promise((resolve,reject) => {
    const probe=net.createServer();
    probe.once("error",()=>reject(new Error(`Test port ${port} is occupied; no existing service will be used.`)));
    probe.listen(port,"127.0.0.1",()=>probe.close(resolve));
  });
}
async function until(check, message) {
  const end = Date.now() + 15000;
  while (Date.now() < end) { try { const value = await check(); if (value) return value; } catch {} await sleep(60); }
  throw new Error(message);
}
function cdp(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Browser command timed out: ${method}`)); }, 10000);
    pending.set(id, {resolve:value=>{clearTimeout(timer);resolve(value);},reject:error=>{clearTimeout(timer);reject(error);}});
    socket.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const response = await cdp("Runtime.evaluate", {expression, returnByValue: true, awaitPromise: true});
  if (response.exceptionDetails) throw new Error("Browser expression failed");
  return response.result.value;
}
async function click(selector) { return evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`); }
async function checkDeveloperCredit() {
  const credit = await evaluate(`(() => {
    const element = document.querySelector('.page-footer .developer-credit');
    if (!element) return null;
    const bounds = element.getBoundingClientRect(), style = getComputedStyle(element);
    return {text:element.textContent, visible:style.display !== 'none' && style.visibility === 'visible'
      && Number(style.opacity) > 0 && bounds.height > 0 && bounds.width > 0,
      fits:bounds.left >= 0 && bounds.right <= innerWidth && element.scrollWidth <= element.clientWidth};
  })()`);
  assert.equal(credit?.text, 'Developed by Chabir Ahamad Abusali');
  assert.equal(credit.visible, true, 'Developer attribution must be visible');
  assert.equal(credit.fits, true, 'Developer attribution must fit the viewport');
}
async function screenshot(name) {
  await evaluate("window.scrollTo(0,0);document.activeElement?.blur()");
  const result = await cdp("Page.captureScreenshot", {format: "png", captureBeyondViewport: true});
  fs.writeFileSync(path.join(output, name), Buffer.from(result.data, "base64"));
}
async function scenario(id) {
  await evaluate(`document.querySelector('#scenario').value=${JSON.stringify(id)};document.querySelector('#scenario').dispatchEvent(new Event('change'));`);
  await click("#run");
  await until(() => evaluate("!state.busy && state.result !== null"), `Scenario ${id} did not finish`);
  return evaluate("({status:state.result.status,report:state.result.report?.status,metrics:state.result.metrics,notice:state.result.notice})");
}
(async () => {
  await requireFreePort(8765); await requireFreePort(9337);
  server = spawn(path.join(root, ".venv", "Scripts", "python.exe"), ["-m", "uvicorn", "deepdive_ai.api:create_app", "--factory", "--host", "127.0.0.1", "--port", "8765", "--no-access-log"], {cwd: root, windowsHide: true, stdio: "ignore"});
  console.log("Starting isolated loopback demo server.");
  await until(async () => (await fetch("http://127.0.0.1:8765/health")).ok, "Local server did not start");
  browser = spawn(browserPath, ["--headless=new", "--disable-gpu", "--in-process-gpu", "--remote-debugging-port=9337", `--user-data-dir=${path.join(output, `browser-profile-${process.pid}`)}`, "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "--disable-component-update", "--disable-sync", "--disable-extensions", "--disable-default-apps", "--disable-features=MediaRouter,OptimizationHints", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1", "about:blank"], {windowsHide: true, stdio: ["ignore","ignore",fs.openSync(path.join(output,"browser-stderr.log"),"a")]});
  fs.writeFileSync(path.join(output,"smoke-processes.json"),JSON.stringify({server:server.pid,browser:browser.pid}));
  const pages = await until(async () => { const result = await (await fetch("http://127.0.0.1:9337/json/list")).json(); return result.some(p => p.type === "page") && result; }, "Browser did not start");
  socket = new WebSocket(pages.find(page => page.type === "page").webSocketDebuggerUrl);
  console.log("Connecting to isolated browser page.");
  await new Promise((resolve, reject) => { const timer=setTimeout(()=>reject(new Error("Browser socket did not open")),10000); socket.addEventListener("open", ()=>{clearTimeout(timer);resolve();}, {once:true}); socket.addEventListener("error", ()=>{clearTimeout(timer);reject(new Error("Browser socket error"));}, {once:true}); });
  socket.addEventListener("message", event => {
    const data = JSON.parse(event.data);
    if (data.id) { const operation = pending.get(data.id); if (!operation) return; pending.delete(data.id); data.error ? operation.reject(new Error(data.error.message)) : operation.resolve(data.result); }
    if (data.method === "Runtime.exceptionThrown") consoleErrors.push("runtime_exception");
    if (data.method === "Log.entryAdded" && data.params.entry.level === "error") consoleErrors.push("browser_error");
    if (data.method === "Fetch.requestPaused") {
      const url = new URL(data.params.request.url);
      const allowed = ["127.0.0.1", "localhost"].includes(url.hostname);
      if (!allowed) externalRequests.push(url.protocol);
      cdp(allowed ? "Fetch.continueRequest" : "Fetch.failRequest", {requestId:data.params.requestId, ...(allowed ? {} : {errorReason:"BlockedByClient"})}).catch(() => {});
    }
  });
  await cdp("Browser.getVersion"); console.log("Browser control channel ready.");
  await cdp("Page.enable"); await cdp("Runtime.enable"); await cdp("Log.enable");
  console.log("Browser protocol ready; checking the dashboard.");
  await cdp("Fetch.enable", {patterns:[{urlPattern:"*", requestStage:"Request"}]});
  await cdp("Emulation.setDeviceMetricsOverride", {width:1440,height:1100,deviceScaleFactor:1,mobile:false});
  await cdp("Page.navigate", {url:"http://127.0.0.1:8765/"});
  await until(() => evaluate("document.querySelector('#run') && !document.querySelector('#run').disabled"), "Dashboard did not initialize");
  await checkDeveloperCredit();
  await screenshot("dashboard-initial.png");
  let result = await scenario("supported"); assert.equal(result.report, "verified"); assert.equal(result.metrics.supported, 1);
  assert.match(result.notice, /NO LIVE RESEARCH/);
  await screenshot("dashboard-desktop.png");
  await click(".citation"); assert.equal(await evaluate("document.querySelector('#panel-sources').hidden"), false);
  assert.equal(await evaluate("document.querySelectorAll('.source-card').length"), 1);
  await cdp("Browser.setDownloadBehavior", {behavior:"allow",downloadPath:output});
  // Require this run's download, rather than accepting an earlier export.
  const exportPath = path.join(output,"deepdive-offline-supported.md");
  if (fs.existsSync(exportPath)) fs.unlinkSync(exportPath);
  await click("#export");
  await until(() => fs.existsSync(exportPath), "Markdown export failed");
  assert.match(fs.readFileSync(exportPath,"utf8"), /OFFLINE FIXTURE DEMO/);
  result = await scenario("unsupported"); assert.equal(result.metrics.unsupported, 1);
  await click("#tab-claims"); assert.match(await evaluate("document.querySelector('#claims-content').textContent"), /contradicts/);
  result = await scenario("conflict"); assert.equal(result.report, "needs_review"); assert.equal(result.metrics.research_retries, 1);
  await click("#tab-claims");
  assert.match(await evaluate("document.querySelector('#claims-content').textContent"), /Prior verification rounds/);
  await screenshot("dashboard-contradiction.png");
  await click("#tab-audit"); assert.match(await evaluate("document.querySelector('#audit-content').textContent"), /research again/);
  result = await scenario("recovery"); assert.equal(result.report, "verified"); assert.equal(result.metrics.research_retries, 1);
  result = await scenario("empty"); assert.equal(result.metrics.evidence, 0); assert.match(await evaluate("document.querySelector('#report-content').textContent"), /No claim cleared/);
  result = await scenario("failure"); assert.equal(result.status,"failed"); assert.equal(await evaluate("document.querySelector('#error').hidden"),false);
  assert.equal(await evaluate("document.querySelector('#export').disabled"),true);
  await scenario("supported");
  await cdp("Emulation.setDeviceMetricsOverride", {width:390,height:844,deviceScaleFactor:1,mobile:true});
  await checkDeveloperCredit();
  await screenshot("dashboard-mobile.png");
  assert.equal(await evaluate("document.documentElement.scrollWidth <= window.innerWidth"),true,"Mobile layout overflows");
  assert.deepEqual(consoleErrors,[]); assert.deepEqual(externalRequests,[]);
  console.log("PASS: six scenarios, streamed progress, citation navigation, contradictions, retries, empty/error states, Markdown download, desktop/mobile layout and exact visible developer attribution; zero browser errors or external page requests.");
})().catch(error => { console.error(error.message); process.exitCode=1; }).finally(async () => {
  if (socket?.readyState===1) { try { await cdp("Browser.close"); } catch {} socket.close(); }
  if (browser && browser.exitCode===null) browser.kill();
  if (server && server.exitCode===null) server.kill();
});
