/**
 * 前端默认模型验收（真实浏览器 + 真实后端）
 *
 * 对应本轮要求第 3 节（同步前端与历史兼容）：
 * 1. 新用户 / 新会话 / 未设置过模型 → 默认选中 DeepSeek（不是 MiMo）
 * 2. 不手动选择模型也能正常问答，回答下方显示实际使用的提供方与模型
 * 3. 旧版本遗留的本地"默认值 mimo"（未标记为主动选择）→ 只提示并切换一次，不每次强制覆盖
 * 4. 学生主动选择后（记为显式选择）不再被自动改动
 *
 * 前置：后端跑在隔离数据目录；前端 dev server 已启动
 * 用法：node tests/e2e_model_default.js [baseUrl]
 */

const path = require('path');
const { chromium } = require('playwright');
const { launchBrowser } = require('./e2e_launch');

const BASE = process.argv[2] || 'http://localhost:3000';
const USERNAME = `md_${Date.now().toString(36)}`;
const OUT = path.join(__dirname, '_screenshots');

const results = [];
function record(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} | ${name}${detail ? ' | ' + detail : ''}`);
}

const modelSelect = (page) => page.locator('.ant-select')
  .filter({ has: page.locator('.ant-select-selection-item') }).last();

async function currentModel(page) {
  const sel = page.locator('.ant-select-selection-item').last();
  return (await sel.innerText().catch(() => '')).trim();
}


/** 数据隔离闸门：后端必须跑在隔离数据目录上（本脚本会在后端写入账号/课程/作答） */
async function requireBackendIsolated(base, purpose) {
  // 用 Node 的 fetch 直接查健康检查，**不要依赖 Playwright 的 page**：
  // 这个闸门要在页面创建之前调用（早期写成收 page 参数，结果 page 还没声明就被引用，
  // 6 个 E2E 脚本一启动就抛 "Cannot access 'page' before initialization"）。
  let scope = null;
  try {
    const resp = await fetch(`${base}/api/health`);
    scope = ((await resp.json())?.data || {}).data_scope || {};
  } catch (e) {
    console.log(`拒绝执行：无法读取后端健康检查（${e}）。请先启动后端。`);
    process.exit(2);
  }
  if (!scope.isolated) {
    console.log('='.repeat(64));
    console.log(`拒绝执行：${purpose}`);
    console.log(`后端当前数据目录：${scope.dir_name}（正式目录）`);
    console.log('本脚本会写入账号/课程/作答数据，不能在正式库上运行。请用隔离目录启动后端：');
    console.log('  $env:EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_work_data"');
    console.log('  python -m uvicorn api.app:app --host 127.0.0.1 --port 8000');
    console.log('='.repeat(64));
    process.exit(2);
  }
  console.log(`[数据隔离] 后端数据目录：${scope.dir_name}（隔离，已确认）`);
}

(async () => {
  await requireBackendIsolated(BASE, "默认模型验收");
  require('fs').mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser({ headless: true });
  // 全新上下文：没有任何 localStorage 残留（模拟新用户）
  const context = await browser.newContext({ viewport: { width: 1440, height: 950 } });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));

  try {
    // 隔离校验
    const health = await page.request.get(`${BASE}/api/health`);
    const scope = ((await health.json()).data || {}).data_scope || {};
    record('后端运行在隔离数据目录', !!scope.isolated, scope.dir_name || '');
    if (!scope.isolated) throw new Error('数据目录未隔离');

    // 注册（新用户）
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.click('button:has-text("登录 / 注册")');
    await page.click('button:has-text("还没有账号？去注册")');
    await page.fill('#username', USERNAME);
    await page.fill('#password', 'test-pass-123');
    await page.click('.ant-modal-footer button.ant-btn-primary');
    await page.waitForSelector('.ant-modal-content', { state: 'detached', timeout: 15000 });
    // 注册后仍在学生页（聊天组件未挂载），模型断言放到进入聊天页之后

    // 选择学科 → 进入学习助手页
    await page.click('.ant-select-selector');
    await page.waitForSelector(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
      { timeout: 8000 });
    await page.locator(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option:has-text("离散数学")')
      .first().click();
    await page.waitForTimeout(800);
    await page.click('.ant-menu-item:has-text("AI学习助手")');
    await page.waitForTimeout(1500);
    // 聊天页的学科是页面级状态，需要在本页再选一次（与真实用户操作一致）
    const chatSubject = page.locator('.ant-select').first();
    await chatSubject.locator('.ant-select-selector').click();
    await page.waitForSelector(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
      { timeout: 8000 });
    await page.locator(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option:has-text("离散数学")')
      .first().click();
    await page.waitForTimeout(1200);

    // 1) 界面默认选中 DeepSeek（不是 MiMo）
    const shown = await currentModel(page);
    record('界面默认选中 DeepSeek', shown.includes('DeepSeek'), `当前模型=${shown}`);
    const stored = await page.evaluate(() => localStorage.getItem('chat_model'));
    record('新用户不写死 MiMo（未主动选择时跟随后端默认）',
      stored === null || stored === 'deepseek', `localStorage.chat_model=${stored}`);

    // 2) 不手动选模型直接问答
    await page.locator('textarea:visible').last().fill('集合的基本概念是什么');
    await page.keyboard.press('Enter');
    // 回答底部的模型标注形如「模型: deepseek / deepseek-chat」，
    // 不能匹配工具栏的「当前模型:」标签，因此用 "提供方 / 模型" 形态判定
    await page.waitForFunction(
      () => /模型: [a-z-]+ *\//.test(document.body.innerText),
      null, { timeout: 180000 });
    const footerText = await page.evaluate(() => {
      const m = document.body.innerText.match(/模型: [^\n]*/g) || [];
      return m.filter((s) => s.includes('/')).join(' ; ');
    });
    record('不选模型即可问答，并显示实际提供方与模型',
      /deepseek \/ deepseek-chat/.test(footerText), footerText.slice(0, 90));
    await page.screenshot({ path: path.join(OUT, 'model-01-default.png'), fullPage: true });

    // 3) 模拟旧版本遗留：本地保存了 mimo 且未标记为主动选择
    await page.evaluate(() => {
      localStorage.setItem('chat_model', 'mimo');
      localStorage.removeItem('chat_model_explicit');
      localStorage.removeItem('model_default_notice_v1');
    });
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(2500);
    const after = await page.evaluate(() => localStorage.getItem('chat_model'));
    const noticed = await page.evaluate(
      () => localStorage.getItem('model_default_notice_v1'));
    record('旧版遗留的 MiMo 默认值被一次性切换为 DeepSeek',
      after === 'deepseek' && noticed === '1', `chat_model=${after} notice=${noticed}`);
    const noticeVisible = await page.locator(
      '.ant-message-notice-content, .ant-message').first().isVisible().catch(() => false);
    record('给出一次清晰提示（而不是静默改动）', noticeVisible);
    await page.screenshot({ path: path.join(OUT, 'model-02-notice.png'), fullPage: true });

    // 4) 再打开一次：不再重复覆盖
    await page.evaluate(() => localStorage.setItem('chat_model', 'mimo'));
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(2500);
    const kept = await page.evaluate(() => localStorage.getItem('chat_model'));
    record('提示只出现一次，之后不再强制覆盖（保留已缓存的 MiMo）',
      kept === 'mimo', `chat_model=${kept}`);

    // 5) 学生主动选择后被记为显式选择
    await page.click('.ant-menu-item:has-text("AI学习助手")');
    await page.waitForTimeout(1200);
    const sel = modelSelect(page);
    await sel.locator('.ant-select-selector').click();
    await page.waitForSelector(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
      { timeout: 8000 });
    await page.locator(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option:has-text("DeepSeek")')
      .first().click();
    await page.waitForTimeout(600);
    const explicit = await page.evaluate(
      () => localStorage.getItem('chat_model_explicit'));
    const nowModel = await page.evaluate(() => localStorage.getItem('chat_model'));
    record('主动选择后记为显式选择（后续不再被自动改动）',
      explicit === '1' && nowModel === 'deepseek', `explicit=${explicit} model=${nowModel}`);

    record('无页面级 JS 异常', pageErrors.length === 0, pageErrors.slice(0, 2).join(' ; '));
  } catch (e) {
    record('脚本执行', false, String(e && e.message ? e.message : e));
    await page.screenshot({ path: path.join(OUT, 'model-error.png'), fullPage: true })
      .catch(() => {});
  } finally {
    await browser.close();
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`);
  process.exit(failed.length ? 1 : 0);
})();
