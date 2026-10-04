/**
 * 统一的浏览器启动器（验收脚本共用）
 *
 * 为什么需要：各 e2e 脚本原先硬编码 `chromium.launch({ channel: 'chrome' })`。
 * 在没有安装 Google Chrome 的机器上会直接报错
 * `Chromium distribution 'chrome' is not found`，导致整套验收脚本无法运行。
 *
 * 回退顺序：系统 Chrome → 系统 Edge（同为 Chromium 内核）→ Playwright 自带 Chromium。
 * 三者的渲染与 JS 行为一致；启动哪个会在日志里打印，便于核对。
 *
 * 用法：const { launchBrowser } = require('./e2e_launch');
 *       const browser = await launchBrowser({ headless: true });
 */
const { chromium } = require('playwright');

async function launchBrowser(opts = {}) {
  const headless = opts.headless !== false;   // 与 Playwright 默认一致
  const candidates = [
    [{ channel: 'chrome', headless }, 'system-chrome'],
    [{ channel: 'msedge', headless }, 'system-edge'],
    [{ headless }, 'bundled-chromium'],
  ];
  let lastErr = null;
  for (const [launchOpts, label] of candidates) {
    try {
      const browser = await chromium.launch(launchOpts);
      console.log(`[browser] 已启动: ${label}`);
      return browser;
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr;
}

module.exports = { launchBrowser };
