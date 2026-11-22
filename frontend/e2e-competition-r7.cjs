/* 智慧学伴比赛版 · 第七轮讲评方案走查（?source=official，N01/DC01/T01）
 * 选方案 → 调预算 → 查学生依据 → 现场补测 → 查看方案变化 → 会话应用/清除
 */
/** 登录凭据走环境变量，不写入仓库（本地运行：E2E_USER=教师账号 E2E_PASS=口令 node 脚本） */
const E2E_USER = process.env.E2E_USER;
const E2E_PASS = process.env.E2E_PASS;
if (!E2E_USER || !E2E_PASS) { console.error('需要 E2E_USER / E2E_PASS 环境变量（本地演示教师账号），不使用明文默认值'); process.exit(1); }
const { chromium } = require('playwright');
const fs = require('fs');
const BASE = 'http://127.0.0.1:8000';
const OUT = 'D:/Document/langchain-rag-work/competition-shots';
const { parseLinear, sameLinear, stripPrefix } = require('D:/Document/langchain-rag-work/competition-data/expr-test.cjs');
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
  const goto = async (p) => { await page.goto(BASE + p, { waitUntil: 'networkidle' }); await page.waitForTimeout(1000); };
  try {
    await goto('/');
    const u = page.locator('input[autocomplete="username"]').first();
    if (await u.count()) {
      await u.fill(E2E_USER);
      await page.locator('input[type="password"]').first().fill(E2E_PASS);
      await page.locator('button[type="submit"]').first().click().catch(() => {});
      await page.waitForTimeout(1800);
    }
    const token = await page.evaluate(() => localStorage.getItem('edu_user_token'));
    const payload = await (await page.request.get(BASE + '/api/competition/official-bundle', { headers: { 'X-User-Token': token } })).json();
    const rp = payload.review_plans;
    step('bundle 含讲评方案', !!rp && !!rp.strategies?.common?.budgets?.['3']);

    await goto('/class-analytics?source=official&quizId=N01&classId=DC01');
    const panel = page.getByTestId('review-plan');
    step('面板默认 common/3 讲评题 Q014 Q018 Q006', await panel.getByText('Q014').count() > 0
      && await panel.getByText('Q018').count() > 0 && await panel.getByText('Q006').count() > 0);
    const p3 = await panel.innerText();
    step('覆盖学生19/已覆盖观察项44', p3.includes('覆盖学生 19') && p3.includes('已覆盖观察项 44'));
    // 预算 3→2
    await panel.locator('.ant-select:has(input[aria-label="方案题目预算"])').click();
    await page.locator('.ant-select-item-option', { hasText: '2 道' }).click();
    await page.waitForTimeout(500);
    const p2 = await panel.innerText();
    step('预算2移除Q006影响D02并给保留理由', p2.includes('少讲 Q006，D02 需要另作安排')
      && p2.includes('Q014') && p2.includes('Q018'));
    await panel.getByRole('button', { name: /看 4 份任务的去向/ }).click().catch(() => {}); await page.waitForTimeout(400);
    const p2d = await panel.innerText();
    step('去向展开含前后任务量', p2d.includes('19 → 15 份（少 4）') && p2d.includes('27 份（不变）'));
    await panel.getByRole('button', { name: /看 4 份任务的去向/ }).click().catch(() => {}); await page.waitForTimeout(400);
    await panel.getByText(/少讲 Q006/).scrollIntoViewIfNeeded().catch(() => {}); await page.waitForTimeout(300);
    await page.screenshot({ path: OUT + '/r7-plan-budget2.png', fullPage: true });
    // 策略 pretest
    await panel.getByText('策略与复核').click().catch(() => {}); await page.waitForTimeout(400);
    await panel.locator('.ant-radio-button-wrapper', { hasText: '优先给证据不足学生补测' }).click();
    await page.waitForTimeout(400);
    await panel.locator('.ant-select:has(input[aria-label="方案题目预算"])').click();
    await page.locator('.ant-select-item-option', { hasText: '3 道' }).click();
    await page.waitForTimeout(500);
    const pp = await panel.innerText();
    step('pretest 策略补测题 Q001 Q002 Q003 带学生', pp.includes('Q001') && pp.includes('Q002') && pp.includes('Q003')
      && pp.includes('D17'));
    await panel.getByTestId('plan-workload').scrollIntoViewIfNeeded(); await page.waitForTimeout(300);
    await page.screenshot({ path: OUT + '/r7-plan-pretest.png', fullPage: true });
    // 对照表（默认收起，展开后再核对数字）
    await panel.getByText('对照：按失分人数选题 vs 本方案', { exact: true }).click();
    await page.waitForTimeout(400);
    const pcmp = await panel.innerText();
    step('naive vs 讲评方案对照', pcmp.includes('按失分人数') && pcmp.includes('讲评方案')
      && pcmp.includes('Q014,Q018,Q002') && pcmp.includes('Q014,Q018,Q006'));
    await panel.getByText('对照：按失分人数选题 vs 本方案', { exact: true }).click();
    // 回 common 点 D01 分组
    await panel.locator('.ant-radio-button-wrapper', { hasText: '优先讲共性失分' }).click().catch(() => {});
    await page.waitForTimeout(400);
    await panel.getByTestId('lecture-choice-Q014').getByRole('button', { name: '看失分步骤' }).click().catch(() => {}); await page.waitForTimeout(400);
    const d01chip = panel.getByRole('button', { name: 'D01', exact: true }).first();
    await d01chip.click(); await page.waitForTimeout(900);
    step('分组点进 D01 官方答卷', page.url().includes('studentId=D01') && page.url().includes('quizId=N01')
      && await page.getByText('作答详情').count() > 0);
    step('D01 当前题评分点可见', await page.locator('.ant-card', { hasText: '作答详情' }).getByText(/1\/1|0\/1/).count() > 0);
    // 现场补测（Q015 页，T01I2）
    await goto('/student-answers?source=official&studentId=D01&questionId=Q015&quizId=N01&classId=DC01&kpId=T01I2');
    await page.locator('.ant-card').filter({ hasText: '下一步' }).getByRole('button', { name: '现场补测' }).first().click(); await page.waitForTimeout(400);
    const suitable = (payload.questions || []).filter((q) => q.kpId === 'T01I2' && q.stem.includes('化简') && q.stem.includes('(') && q.referenceAnalysis)
      .map((q) => ({ q, segs: q.referenceAnalysis.split('；').map((s) => s.trim()).filter(Boolean) }))
      .filter((x) => {
        if (x.segs.length < 2) return false;
        const m = parseLinear(stripPrefix(x.segs[0])); const f = parseLinear(stripPrefix(x.segs[x.segs.length - 1]));
        return m && f && sameLinear(m, f) && stripPrefix(x.segs[0]).replace(/\s/g, '') !== stripPrefix(x.segs[x.segs.length - 1]).replace(/\s/g, '');
      })
      .sort((a, b) => a.q.id.localeCompare(b.q.id))[0];
    const mid = stripPrefix(suitable.segs[0]); const fin = stripPrefix(suitable.segs[suitable.segs.length - 1]);
    // 先提交一个中间错，制造可观察的新失分证据
    await page.getByLabel('去括号后的式子', { exact: true }).fill(mid.replace(/-4$/, '+4'));
    await page.getByLabel('最终填答', { exact: true }).fill(fin);
    await page.getByRole('button', { name: '提交判断' }).click(); await page.waitForTimeout(400);
    step('现场补测中间错判定', await page.getByText('判定：中间变形出错').count() > 0);
    // 回班级页重算
    await goto('/class-analytics?source=official&quizId=N01&classId=DC01');
    const panel2 = page.getByTestId('review-plan');
    await panel2.getByRole('button', { name: '用现场证据重算' }).click();
    await page.waitForTimeout(1200);
    const rc = await panel2.innerText();
    step('重算显示新旧差异', rc.includes('带回现场证据后的重算结果')
      && (rc.includes('未覆盖观察项') || rc.includes('有效证据') || rc.includes('未改变')));
    await panel2.getByTestId('plan-recompute').scrollIntoViewIfNeeded(); await page.waitForTimeout(300);
    await page.screenshot({ path: OUT + '/r7-recompute.png', fullPage: true });
    await panel2.getByRole('button', { name: '确认应用到当前演示会话' }).click();
    await page.waitForTimeout(400);
    step('会话应用徽标', await panel2.getByText(/已应用会话方案/).count() > 0);
    const payload2 = await (await page.request.get(BASE + '/api/competition/official-bundle', { headers: { 'X-User-Token': token } })).json();
    step('官方统计不受会话应用影响',
      JSON.stringify(payload2.kps.find((k) => k.id === 'N01-T01I2').stats) === JSON.stringify(payload.kps.find((k) => k.id === 'N01-T01I2').stats));
    await panel2.getByRole('button', { name: '清除会话应用' }).click(); await page.waitForTimeout(300);
    step('清除会话应用', await panel2.getByText(/已应用会话方案/).count() === 0);
    // 清理现场作答，避免污染后续
    await goto('/student-answers?source=official&studentId=D01&questionId=Q015&quizId=N01&classId=DC01&kpId=T01I2');
    const clr = page.getByRole('button', { name: '清除现场作答' });
    if (await clr.count()) { await clr.click(); await page.waitForTimeout(300); }
    const realErrors = errors.filter((e) => !/favicon|404/i.test(e));
    step('无JS运行时错误', realErrors.length === 0, realErrors.slice(0, 2).join(' || '));
  } catch (e) {
    step('R7 走查异常', false, String(e).slice(0, 300));
  } finally {
    fs.writeFileSync(OUT + '/e2e-r7-log.txt', log.join('\n'), 'utf8');
    await browser.close();
  }
})();
