/**
 * 阶段一 真实模型端到端验收（浏览器）
 *
 * 与 tests/e2e_browser_check.js 的区别：
 * - 本脚本会**真实调用后端配置的大模型**生成自测题（非 Mock），
 *   用于验证"真实模型 → 题库入库 → 前端作答 → 后端判分 → 学习状态更新"整条链路。
 * - 因此耗时较长（可能 10-120s），且依赖模型服务可用。
 *
 * 用法：
 *   node tests/e2e_real_flow.js [baseUrl] [subjectName] [topic]
 * 例：
 *   node tests/e2e_real_flow.js http://localhost:3000 离散数学 集合的基本概念
 *
 * 产出：tests/_screenshots/real-*.png
 */

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { launchBrowser } = require('./e2e_launch');

const BASE = process.argv[2] || 'http://localhost:3000';
const SUBJECT_NAME = process.argv[3] || '离散数学';
const TOPIC = process.argv[4] || '集合的基本概念';
const OUT = path.join(__dirname, '_screenshots');
const USERNAME = `stu_${Date.now().toString(36)}`;
const GEN_TIMEOUT = 180000;

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
  await requireBackendIsolated(BASE, "真实模型全流程验收");
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));

  try {
    // 1) 注册
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.click('button:has-text("登录 / 注册")');
    await page.click('button:has-text("还没有账号？去注册")');
    await page.fill('#username', USERNAME);
    await page.fill('#password', 'test-pass-123');
    await page.click('.ant-modal-footer button.ant-btn-primary');
    await page.waitForSelector('.ant-modal-content', { state: 'detached', timeout: 15000 });
    record('注册并登录', true, USERNAME);

    // 2) 进入学习助手
    await page.click('.ant-menu-item:has-text("AI学习助手")');
    await page.waitForTimeout(1000);

    // 3) 选择学科
    await page.click('.ant-select-selector');
    await page.waitForSelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option', { timeout: 8000 });
    await page.locator(`.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option:has-text("${SUBJECT_NAME}")`).first().click();
    record('选择学科', true, SUBJECT_NAME);
    await page.waitForTimeout(800);

    // 4) 新建会话（结构化消息需要会话才能持久化）
    const newConvBtn = page.locator('button:has-text("新建对话")');
    if (await newConvBtn.count()) {
      await newConvBtn.first().click();
      await page.waitForTimeout(1500);
      record('新建会话', true);
    } else {
      record('新建会话', false, '未找到按钮');
    }

    // 4b) 选择模型（默认 MiMo 的密钥当前不可用，这里显式选 DeepSeek）
    const MODEL = process.argv[5] || 'DeepSeek';
    // 精确定位"当前模型"下拉：其当前值显示为 小米MiMo
    const modelSelect = page.locator('.ant-select')
      .filter({ has: page.locator('.ant-select-selection-item:has-text("小米MiMo")') })
      .last();
    await modelSelect.locator('.ant-select-selector').click();
    await page.waitForSelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option', { timeout: 8000 });
    await page.locator(`.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option:has-text("${MODEL}")`).first().click();
    await page.waitForTimeout(300);
    record('选择模型', true, MODEL);

    // 5) 切到「自测」模式（用 placeholder 变化确认真的切换成功）
    await page.locator('button:has-text("自测"), .ant-tag:has-text("自测")').first().click();
    await page.waitForTimeout(500);
    const ph = await page.locator('textarea:visible').first().getAttribute('placeholder');
    record('切换到自测模式', !!ph && ph.includes('自测主题'), ph || 'placeholder 未获取到');

    // 6) 输入知识点并发送（真实模型生成）
    const textarea = page.locator('textarea:visible').last();
    await textarea.fill(TOPIC);
    await page.keyboard.press('Enter');
    record('已发送自测请求（真实模型生成中）', true, TOPIC);

    // 7) 等待题目卡片（提交作答按钮）
    const submitBtn = page.locator('button:has-text("提交作答")').first();
    await submitBtn.waitFor({ state: 'visible', timeout: GEN_TIMEOUT });
    const qCount = await page.locator('button:has-text("提交作答")').count();
    record('真实模型生成自测题并渲染', qCount > 0, `${qCount} 道`);

    // 8) 关键约束：题目下发时不含答案
    const bodyBefore = await page.locator('.ant-list').last().innerText();
    const hasAnswerLeak = /正确答案：|参考答案：|解析：/.test(bodyBefore);
    record('题目下发时不泄露答案', !hasAnswerLeak);
    await page.screenshot({ path: path.join(OUT, 'real-01-quiz-generated.png'), fullPage: true });

    // 9) 选中第一个选项并提交（由后端判分）
    const firstRadio = page.locator('.ant-radio-wrapper').first();
    const hasRadio = await firstRadio.count();
    if (hasRadio) {
      await firstRadio.click();
    } else {
      await page.locator('textarea:visible').last().fill('测试作答');
    }
    await submitBtn.click();
    const resultAlert = page.locator('.ant-alert').filter({ hasText: /你的作答：/ }).first();
    await resultAlert.waitFor({ state: 'visible', timeout: 30000 });
    const resultText = (await resultAlert.innerText()).replace(/\n/g, ' / ');
    const graded = /回答正确|回答错误|待自评|已自评/.test(resultText);
    record('提交后返回后端判分结果', graded, resultText.slice(0, 130));
    // 标准答案只在提交后下发
    record('判分后展示正确答案', /正确答案：|参考要点：/.test(resultText), '');
    await page.screenshot({ path: path.join(OUT, 'real-02-graded.png'), fullPage: true });

    // 10) 刷新页面后判分结果/作答历史仍在（服务端持久化）
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    record('刷新后页面无异常', pageErrors.length === 0, pageErrors.slice(0, 2).join(' ; '));

    // 11) 我的学习：作答统计与学习状态已联动
    await page.click('.ant-menu-item:has-text("我的学习")');
    await page.waitForTimeout(2000);
    const statsText = await page.locator('.ant-statistic').allInnerTexts();
    const attemptsStat = statsText.find((s) => s.includes('作答道数')) || '';
    const joined = statsText.join(' | ');
    const total = parseInt((attemptsStat.match(/(\d+)/) || [])[1] || '0', 10);
    record('首页作答道数已统计到本次作答', total >= 1, joined.replace(/\n/g, ' '));
    await page.screenshot({ path: path.join(OUT, 'real-03-student-home.png'), fullPage: true });

    // 12) 掌握度页签：先选学科，再确认显示的是证据驱动状态（不是伪造分数）
    await page.click('.ant-tabs-tab:has-text("掌握度")');
    await page.waitForTimeout(400);
    await page.locator('.ant-select').first().locator('.ant-select-selector').click();
    await page.waitForSelector(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
      { timeout: 8000 });
    await page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')
      .first().click();
    await page.waitForTimeout(1500);
    const masteryText = await page.locator('.ant-tabs-tabpane-active').innerText();
    const hasStatus = /暂估|待评估/.test(masteryText);
    record('掌握度显示证据驱动状态', hasStatus, masteryText.split('\n').slice(0, 6).join(' / '));
    record('掌握度口径说明可见', /只由独立作答证据计算/.test(masteryText), '');
    await page.screenshot({ path: path.join(OUT, 'real-04-mastery.png'), fullPage: true });

    // 13) 联动记录（2026-10-02 起学生界面不再展示该页签）
    // 事件流水与失败重试的能力仍在后端（/api/student/learning-events），
    // 由后端测试覆盖；这里不再有可点的界面，故移除该步验收。
  } catch (e) {
    record('脚本执行', false, String(e && e.message ? e.message : e));
    await page.screenshot({ path: path.join(OUT, 'real-error.png'), fullPage: true }).catch(() => {});
  } finally {
    await browser.close();
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`);
  console.log(`截图目录：${OUT}`);
  process.exit(failed.length ? 1 : 0);
})();
