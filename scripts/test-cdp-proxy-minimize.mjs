// 假 Chrome + 真 cdp-proxy 联调：后台标签页新开窗口要被最小化。
// 用法：node /tmp/test-proxy-minimize.mjs（退出码 0 = 通过）
// 覆盖：第一个后台标签新开窗口 → setWindowBounds minimized；
// 第二个后台标签复用已有窗口 → 不最小化；前台标签 → 不最小化。
import http from 'node:http';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// 路径都按脚本自身位置解析，不依赖 CWD。
const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, '../src/bosshunter/web/frontend/package.json'));
const { WebSocketServer } = require('ws');

const FAKE_PORT = 19222;
const PROXY_PORT = 13456;
const PROXY_PATH = path.join(here, '../src/bosshunter/browser/runtime/cdp-proxy.mjs');
const calls = [];
const pages = [{ targetId: 'panel-target', windowId: 1, url: 'http://127.0.0.1:8686/' }];
let tabSeq = 0;

const server = http.createServer((req, res) => {
  if (req.url === '/json/version') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      Browser: 'Chrome/120.0.0.0',
      'Protocol-Version': '1.3',
      webSocketDebuggerUrl: `ws://127.0.0.1:${FAKE_PORT}/devtools/browser/fake`,
    }));
    return;
  }
  res.writeHead(404);
  res.end();
});

const wss = new WebSocketServer({ noServer: true });
server.on('upgrade', (req, socket, head) => {
  wss.handleUpgrade(req, socket, head, (ws) => {
    ws.on('message', (raw) => {
      const msg = JSON.parse(raw.toString());
      const { id, method, params = {} } = msg;
      calls.push({ method, params });
      const reply = (result) => ws.send(JSON.stringify({ id, result }));
      if (method === 'Browser.getVersion') return reply({ product: 'Chrome/120.0.0.0' });
      if (method === 'Target.getTargets') {
        return reply({ targetInfos: pages.map((p) => ({ targetId: p.targetId, type: 'page', url: p.url })) });
      }
      if (method === 'Target.createTarget') {
        tabSeq += 1;
        const targetId = `tab-${tabSeq}`;
        // 模拟 Chrome：新建标签都进普通窗口 2；第一个新建时窗口 2 还不存在。
        pages.push({ targetId, windowId: 2, url: params.url });
        return reply({ targetId });
      }
      if (method === 'Target.attachToTarget') return reply({ sessionId: `sess-${params.targetId}` });
      if (method === 'Browser.getWindowForTarget') {
        const page = pages.find((p) => p.targetId === params.targetId);
        return reply({ windowId: page ? page.windowId : 1, bounds: { width: 800, height: 600 } });
      }
      if (method === 'Browser.setWindowBounds') return reply({});
      if (method === 'Fetch.enable') return reply({});
      return reply({});
    });
  });
});

function fail(message) {
  console.error(`FAIL: ${message}`);
  console.error('CDP calls:', JSON.stringify(calls, null, 2));
  process.exitCode = 1;
}

function check(condition, message) {
  if (condition) console.log(`ok: ${message}`);
  else fail(message);
}

await new Promise((resolve) => server.listen(FAKE_PORT, resolve));
const proxy = spawn('node', [PROXY_PATH], {
  env: {
    ...process.env,
    BOSSHUNTER_BROWSER_PROXY_PORT: String(PROXY_PORT),
    BOSSHUNTER_CHROME_PORTS: String(FAKE_PORT),
    BOSSHUNTER_ENABLE_PORT_GUARD: 'false',
  },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let proxyOutput = '';
proxy.stdout.on('data', (d) => { proxyOutput += d; });
proxy.stderr.on('data', (d) => { proxyOutput += d; });

const minimizeCalls = () => calls.filter((c) => c.method === 'Browser.setWindowBounds');
async function createTab(query) {
  const res = await fetch(`http://127.0.0.1:${PROXY_PORT}/new?${query}`, { signal: AbortSignal.timeout(15000) });
  return res.json();
}
async function waitForProxy(timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${PROXY_PORT}/health`, { signal: AbortSignal.timeout(1000) });
      if (res.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error('proxy 未在 10 秒内起来');
}

try {
  await waitForProxy();
  const r1 = await createTab('url=https://example.com/job1&background=1');
  check(r1.targetId === 'tab-1', `第一个后台标签建出 tab-1（实际 ${r1.targetId}）`);
  check(minimizeCalls().length === 1, `第一个后台标签新开窗口被最小化 1 次（实际 ${minimizeCalls().length} 次）`);
  const first = minimizeCalls()[0];
  check(first?.params?.windowId === 2, '最小化的是新窗口 windowId 2');
  check(first?.params?.bounds?.windowState === 'minimized', '最小化用 windowState=minimized');

  const r2 = await createTab('url=https://example.com/job2&background=1');
  check(r2.targetId === 'tab-2', `第二个后台标签建出 tab-2（实际 ${r2.targetId}）`);
  check(minimizeCalls().length === 1, `复用已有窗口时不再最小化（实际 ${minimizeCalls().length} 次）`);

  const r3 = await createTab('url=https://example.com/job3');
  check(r3.targetId === 'tab-3', `前台标签建出 tab-3（实际 ${r3.targetId}）`);
  check(minimizeCalls().length === 1, `前台标签不最小化（实际 ${minimizeCalls().length} 次）`);
} catch (error) {
  console.error('proxy output:', proxyOutput);
  fail(`调用异常：${error.message}`);
} finally {
  proxy.kill();
  server.close();
}
if (!process.exitCode) console.log('ALL PROXY MINIMIZE CHECKS PASSED');
