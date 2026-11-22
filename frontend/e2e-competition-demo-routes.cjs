/* 智慧学伴比赛版 · 两条可直接演示路线（1366×768）
 * 路线A：完整主案例 D01/Q015/DR0001 → 教材 → 补练 → 教师调整
 * 路线B：对比 D15(表现较好) 与 D17(记录不足) → 教师调整 → 预览学生反馈
 * 官方数据经 ?source=official 受控接口读取；输出截图供录屏/方案引用。
 */
/** 登录凭据走环境变量，不写入仓库（本地运行：E2E_USER=教师账号 E2E_PASS=口令 node 脚本） */
const E2E_USER = process.env.E2E_USER;
const E2E_PASS = process.env.E2E_PASS;
if (!E2E_USER || !E2E_PASS) { console.error('需要 E2E_USER / E2E_PASS 环境变量（本地演示教师账号），不使用明文默认值'); process.exit(1); }
const { chromium } = require('playwright');
const fs = require('fs');
const BASE = 'http://127.0.0.1:8000';
const OUT = 'D:/Document/langchain-rag-work/competition-shots';
fs.mkdirSync(OUT, { recursive: true });
const log = [];
const step = (n, ok, d = '') => { const l = `${ok ? 'PASS' : 'FAIL'} | ${n}${d ? ' | ' + d : ''}`; log.push(l); console.log(l); };
const goto = async (p, path) => { await p.goto(BASE + path, { waitUntil: 'networkidle' }); await p.waitForTimeout(900); };

(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  try {
    await goto(page, '/');
    const u = page.locator('input[autocomplete="username"]').first();
    if (await u.count()) {
      await u.fill(E2E_USER);
      await page.locator('input[type="password"]').first().fill(E2E_PASS);
      await page.locator('button[type="submit"]').first().click().catch(async () => {
        await page.getByRole('button', { name: /登.*录|注册/ }).first().click();
      });
      await page.waitForTimeout(1600);
    }

    // 路线A：主案例
    await goto(page, '/class-analytics?source=official&quizId=N01&kpId=N01-T01I2');
    step('A1 班级页入口可见', await page.getByText('两班考点对比').count() > 0);
    await goto(page, '/student-answers?source=official&studentId=D01&questionId=Q015&quizId=N01&classId=DC01&kpId=T01I2');
    step('A2 过程/填答并排', await page.getByText('过程末行与最终填答需核对').count() > 0);
    await page.screenshot({ path: OUT + '/route-A-answer.png', fullPage: true });
    const tb = page.getByRole('button', { name: /打开教材 PDF/ }).first();
    if (await tb.count()) {
      await tb.click(); await page.waitForTimeout(1200);
      step('A3 教材原页打开', await page.locator('.pdf-page-canvas-wrap canvas').count() > 0);
      await page.screenshot({ path: OUT + '/route-A-textbook.png' });
      await page.getByRole('button', { name: '关闭' }).first().click(); await page.waitForTimeout(300);
      step('A4 关闭教材保持原位置', page.url().includes('questionId=Q015'));
    }
    const adj = page.getByRole('button', { name: '教师调整' });
    if (await adj.count()) { await adj.click(); await page.waitForTimeout(300); }
    step('A5 教师调整入口', await page.getByText('系统原建议').count() > 0);
    await page.screenshot({ path: OUT + '/route-A-adjust.png', fullPage: true });

    // 路线B：D15 表现较好
    await goto(page, '/student-answers?source=official&studentId=D15&questionId=Q049&quizId=N02&classId=DC01&kpId=T03I2');
    step('B1 D15 表现较好案例', await page.getByText('表现较好').count() > 0);
    await page.screenshot({ path: OUT + '/route-B-good.png', fullPage: true });
    // 路线B：D17 记录不足
    await goto(page, '/student-answers?source=official&studentId=D17&questionId=Q014&quizId=N01&classId=DC01&kpId=T01I1');
    step('B2 D17 记录不足案例', await page.getByText('记录不足').count() > 0);
    await page.screenshot({ path: OUT + '/route-B-insufficient.png', fullPage: true });
    const adj2 = page.getByRole('button', { name: '教师调整' });
    if (await adj2.count()) { await adj2.click(); await page.waitForTimeout(300); }
    const replace = page.getByRole('button', { name: /替换为/ }).first();
    if (await replace.count()) { await replace.click(); await page.waitForTimeout(300); }
    step('B3 替换练习后显示当前方案', await page.getByText('当前方案').count() > 0);
    const prev = page.getByRole('button', { name: '预览学生反馈' });
    if (await prev.count()) { await prev.click(); await page.waitForTimeout(300); }
    step('B4 学生预览仅本人', await page.getByText(/反馈预览/).count() > 0);
    await page.screenshot({ path: OUT + '/route-B-adjust.png', fullPage: true });

    const real = errors.filter((e) => !/favicon|404/i.test(e));
    step('无JS运行时错误', real.length === 0, real.slice(0, 2).join(' || '));
  } catch (e) { step('路线异常', false, String(e).slice(0, 300)); }
  finally { fs.writeFileSync(OUT + '/e2e-demo-routes-log.txt', log.join('\n'), 'utf8'); await browser.close(); }
})();
