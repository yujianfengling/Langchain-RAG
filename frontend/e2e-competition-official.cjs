/* 智慧学伴比赛版 · 官方数据链路验收（?source=official，受控接口）
 * 覆盖：两班对比→证据明细→官方答卷(过程/填答/评分点)→教材受控PDF→
 *       证据化诊断→教师调整与恢复→三类案例不同→官方数据不进静态资源
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
function step(name, ok, detail = '') {
  const line = `${ok ? 'PASS' : 'FAIL'} | ${name}${detail ? ' | ' + detail : ''}`;
  log.push(line); console.log(line);
}
const waitReady = async () => {
  for (let i = 0; i < 20; i++) {
    try { const r = await fetch(BASE + '/'); if (r.ok) return; } catch {}
    await new Promise((r) => setTimeout(r, 2000));
  }
};

(async () => {
  await waitReady();
  const browser = await chromium.launch({
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true,
  });
  const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  try {
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1000);
    const u = page.locator('input[autocomplete="username"]').first();
    if (await u.count()) {
      await u.fill(E2E_USER);
      await page.locator('input[type="password"]').first().fill(E2E_PASS);
      await page.locator('button[type="submit"]').first().click().catch(async () => {
        await page.getByRole('button', { name: /登.*录|注册/ }).first().click();
      });
      await page.waitForTimeout(1800);
    }

    await page.goto(BASE + '/class-analytics?source=official', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    step('官方数据源标签', await page.getByText('官方赛题数据').count() > 0);
    step('官方两班 DC01/DC02', await page.getByText('DC01').count() > 0 && await page.getByText('DC02').count() > 0);
    step('N01/T01I2 两班 68%/66%',
      await page.getByText('68%').count() > 0 && await page.getByText('66%').count() > 0);
    await page.screenshot({ path: OUT + '/official-class-analytics.png', fullPage: true });

    const cell = page.locator('button').filter({ hasText: '68%' }).first();
    if (await cell.count()) { await cell.click(); await page.waitForTimeout(500); }
    step('证据明细-得分和/满分和', await page.getByText(/得分和 69 \/ 满分和 101/).count() > 0);
    step('证据明细-有效作答数与人数',
      await page.getByText(/有效作答 67 条/).count() > 0
      && await page.getByText(/有记录 21 \/ 班级 21 人/).count() > 0);
    step('证据明细-计算依据', await page.getByText(/68.32% = 69÷101/).count() > 0);
    await page.screenshot({ path: OUT + '/official-evidence.png', fullPage: true });

    // 主答卷 D01/Q015/DR0001
    await page.goto(BASE + '/student-answers?source=official&studentId=D01&questionId=Q015&quizId=N01&classId=DC01&kpId=T01I2',
      { waitUntil: 'networkidle' });
    await page.waitForTimeout(1100);
    step('官方题干', await page.getByText(/3\(2a-b\)-2\(a\+2b\)/).count() > 0);
    step('官方步骤', await page.getByText(/原式=6a-3b-2a-4b/).count() > 0);
    step('过程与填答不一致突出显示',
      await page.getByText('过程末行与最终填答需核对').count() > 0
      && await page.getByText('4a+7b').count() > 0);
    step('官方 2/3 与评分点',
      await page.getByText(/得分 2/).count() > 0
      && await page.getByText('正确去两个括号').count() > 0
      && await page.getByText('写出正确结果').count() > 0);
    step('来源记录 DR0001', await page.getByText(/来源记录 DR0001/).count() > 0);

    // 教材受控 PDF
    const tb = page.getByRole('button', { name: /打开教材 PDF 第/ }).first();
    if (await tb.count()) {
      await tb.click(); await page.waitForTimeout(1500);
      step('教材受控PDF打开指定页', await page.getByText(/PDF 第 31 页/).count() > 0
        && await page.locator('.pdf-page-canvas-wrap canvas').count() > 0);
      await page.screenshot({ path: OUT + '/official-textbook.png' });
      await page.getByRole('button', { name: '关闭' }).first().click();
      await page.waitForTimeout(400);
    } else step('教材受控PDF打开指定页', false, '按钮未找到');

    // 证据化诊断 + 教师调整
    step('证据化诊断事实/推测/复查',
      await page.getByText('观察到的事实').count() > 0
      && await page.getByText('推测（需核实）').count() > 0
      && await page.getByText('复查条件').count() > 0);
    step('关键差异提示', await page.getByText(/关键差异 · DR0001/).count() > 0);
    const adj = page.getByRole('button', { name: '教师调整' });
    if (await adj.count()) {
      await adj.click(); await page.waitForTimeout(300);
      step('教师调整前后并排', await page.getByText('系统原建议').count() > 0
        && await page.getByText('当前方案').count() > 0);
      const pretest = page.getByRole('button', { name: '改为先补测' });
      if (await pretest.count()) { await pretest.click(); await page.waitForTimeout(300); }
      step('调整后显示先补测', await page.getByText('先补测').count() > 0);
      const restore = page.getByRole('button', { name: /恢复系统建议/ });
      if (await restore.count()) { await restore.click(); await page.waitForTimeout(300); }
      step('恢复系统建议', await page.getByText(/对照练习/).count() > 0);
    } else step('教师调整前后并排', false, '按钮未找到');

    // 三类案例不同（从受控接口直接核对 bundle 的 case_book）
    const api = await page.request.get(BASE + '/api/competition/official-bundle', {
      headers: { 'X-User-Token': await page.evaluate(() => localStorage.getItem('edu_user_token')) },
    });
    const payload = await api.json();
    const names = (payload.case_book?.cases || []).map((c) => c.case_name);
    step('三类官方案例不同',
      names.includes('部分得分') && names.includes('表现较好') && names.includes('记录不足'),
      names.join('/'));
    step('案例册保留记录编号',
      (payload.case_book?.cases || []).some((c) => (c.evidence_ids || []).some((e) => e.includes('D01-N01-Q015'))));

    const staticRes = await page.request.get(BASE + '/assets/competition-data/manifest.json');
    step('官方数据未进静态资源', !(await staticRes.text()).includes('"source": "official"'));

    const realErrors = errors.filter((e) => !/favicon|404/i.test(e));
    step('无JS运行时错误', realErrors.length === 0, realErrors.slice(0, 2).join(' || '));
  } catch (e) {
    step('官方链路异常', false, String(e).slice(0, 300));
  } finally {
    fs.writeFileSync(OUT + '/e2e-official-log.txt', log.join('\n'), 'utf8');
    await browser.close();
  }
})();
