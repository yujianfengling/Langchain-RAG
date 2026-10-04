/**
 * 阶段一 浏览器验收脚本（人工可见的页面与跳转检查）
 *
 * 用法：
 *   node tests/e2e_browser_check.js [baseUrl]
 *
 * 说明：
 * - 使用系统已安装的 Chrome（channel: 'chrome'），不下载额外浏览器
 * - 只做真实页面渲染、跳转、登录与接口联通性检查；不调用真实大模型
 *   （自测题生成需要模型，本脚本只验证入口与空态，模型相关验证见
 *   tests/test_learning_loop_phase1.py 的 Mock 用例与人工真实调用）
 * - 产出截图到 tests/_screenshots/
 */

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { launchBrowser } = require('./e2e_launch');

const BASE = process.argv[2] || 'http://localhost:3000';
const OUT = path.join(__dirname, '_screenshots');
const USERNAME = `e2e_${Date.now().toString(36)}`;

const results = [];
function record(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} | ${name}${detail ? ' | ' + detail : ''}`);
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
  await requireBackendIsolated(BASE, "端到端浏览检查");
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  const consoleErrors = [];
  const pageErrors = [];
  const failedRequests = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('pageerror', (e) => pageErrors.push(String(e)));
  page.on('requestfailed', (r) => failedRequests.push(`${r.method()} ${r.url()}`));

  try {
    // 1) 默认路由 → 我的学习（未登录提示）
    await page.goto(BASE, { waitUntil: 'networkidle' });
    const url = page.url();
    // "/" 与 "/student" 都渲染学生首页（侧边栏高亮按 /student 处理）
    record('默认入口渲染学生首页', url.endsWith('/') || url.includes('/student'), url);
    await page.waitForSelector('text=我的学习', { timeout: 10000 });
    record('学生页标题渲染', true);
    const loginAlert = await page.locator('text=请先登录').count();
    record('未登录时显示登录提示', loginAlert > 0);
    await page.screenshot({ path: path.join(OUT, '01-student-guest.png'), fullPage: true });

    // 2) 侧边栏导航入口齐全
    for (const label of ['我的学习', 'AI学习助手', '知识库', '学习计划', '设置']) {
      const n = await page.locator(`.ant-menu-item:has-text("${label}")`).count();
      record(`侧边栏包含「${label}」`, n > 0);
    }

    // 3) 注册并登录
    await page.click('button:has-text("登录 / 注册")');
    await page.waitForSelector('.ant-modal-content', { timeout: 5000 });
    await page.click('button:has-text("还没有账号？去注册")');
    await page.fill('#username', USERNAME);
    await page.fill('#dialog_display_name', 'E2E 同学').catch(() => {});
    await page.fill('#password', 'test-pass-123');
    await page.click('.ant-modal-footer button.ant-btn-primary');
    await page.waitForSelector('.ant-modal-content', { state: 'detached', timeout: 15000 });
    const badge = await page.locator(`text=${USERNAME}`).count();
    const badgeAlt = await page.locator('text=E2E 同学').count();
    record('注册后显示当前用户', badge + badgeAlt > 0);

    // 4) 登录后学生页展示统计与页签
    await page.waitForTimeout(1200);
    for (const tab of ['今日任务', '逾期', '复习安排', '错题本', '掌握度']) {
      const n = await page.locator(`.ant-tabs-tab:has-text("${tab}")`).count();
      record(`学生页包含页签「${tab}」`, n > 0);
    }
    const statOk = await page.locator('text=今日任务').count();
    record('概览统计渲染', statOk > 0);
    await page.screenshot({ path: path.join(OUT, '02-student-logged-in.png'), fullPage: true });

    // 5) 空态提示（无数据时不伪造学情）
    await page.click('.ant-tabs-tab:has-text("掌握度")');
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(OUT, '03-mastery-empty.png'), fullPage: true });

    // 5b) 学科选择器可交互（学生页数据按学科过滤）
    await page.click('.ant-tabs-tab:has-text("今日任务")');
    await page.click('.ant-select-selector');
    await page.waitForSelector('.ant-select-item-option', { timeout: 8000 });
    const optionCount = await page.locator('.ant-select-item-option').count();
    record('学科下拉有可选项', optionCount > 0, `${optionCount} 个`);
    const firstSubject = await page.locator('.ant-select-item-option').first().innerText();
    await page.locator('.ant-select-item-option').first().click();
    await page.waitForTimeout(800);
    record('选择学科后页面正常刷新', (await page.locator('text=加载学习首页失败').count()) === 0,
           firstSubject);
    await page.screenshot({ path: path.join(OUT, '03b-subject-selected.png'), fullPage: true });

    // 6) 学习计划页
    await page.click('.ant-menu-item:has-text("学习计划")');
    await page.waitForSelector('text=生成学习计划', { timeout: 10000 });
    record('学习计划页渲染', true);
    await page.screenshot({ path: path.join(OUT, '04-learning-plan.png'), fullPage: true });

    // 7) 学习助手页（模式入口 + 学科选择）
    await page.click('.ant-menu-item:has-text("AI学习助手")');
    await page.waitForTimeout(1200);
    const chatOk = await page.locator('text=历史对话').count();
    record('学习助手页渲染', chatOk > 0);
    for (const mode of ['自测', '智能笔记', '普通问答']) {
      const n = await page.locator(`text=${mode}`).count();
      record(`助手页包含模式「${mode}」`, n > 0);
    }
    await page.screenshot({ path: path.join(OUT, '05-chat.png'), fullPage: true });

    // 8) 退出登录 → 回到未登录态
    await page.click('button:has-text("退出登录")');
    await page.waitForTimeout(800);
    const backToLogin = await page.locator('button:has-text("登录 / 注册")').count();
    record('退出登录后回到未登录态', backToLogin > 0);

    // 9) 控制台干净度
    const apis = failedRequests.filter((u) => u.includes('/api/'));
    record('无页面级 JS 异常', pageErrors.length === 0, pageErrors.slice(0, 3).join(' ; '));
    record('API 请求无网络失败', apis.length === 0, apis.slice(0, 3).join(' ; '));
    const realErrors = consoleErrors.filter((t) =>
      !t.includes('favicon')
      && !t.includes('Static function can not consume context'));
    record('无控制台错误', realErrors.length === 0, realErrors.slice(0, 3).join(' ; '));
  } catch (e) {
    record('脚本执行', false, String(e && e.message ? e.message : e));
    await page.screenshot({ path: path.join(OUT, 'error.png'), fullPage: true }).catch(() => {});
  } finally {
    await browser.close();
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`);
  console.log(`截图目录：${OUT}`);
  process.exit(failed.length ? 1 : 0);
})();
