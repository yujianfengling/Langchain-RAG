/* 智慧学伴比赛版 · 本轮针对性反例验收（?source=official）
 * P0-1 跨测验定位：D01/Q002 N01=DR0006 0/1、N02=DR0019 1/1；目标节点无记录不跨场补；
 *      班级下钻不混入另一班/另一场；直接进答卷页按测验列作答；返回恢复筛选。
 * P0-2 一致性：页面=案例册=诊断=CSV；无旧占位文案。
 * P1-3 教师调整整套任务变化+恢复；官方统计不受影响。
 * P1-4 证据回放前后对照（自造，隔离）。
 */
/** 登录凭据走环境变量，不写入仓库（本地运行：E2E_USER=教师账号 E2E_PASS=口令 node 脚本） */
const E2E_USER = process.env.E2E_USER;
const E2E_PASS = process.env.E2E_PASS;
if (!E2E_USER || !E2E_PASS) { console.error('需要 E2E_USER / E2E_PASS 环境变量（本地演示教师账号），不使用明文默认值'); process.exit(1); }
const { chromium } = require('playwright');
const fs = require('fs');
const BASE = 'http://127.0.0.1:8000';
const OUT = 'D:/Document/langchain-rag-work/competition-shots';
const DELIV = 'D:/Document/langchain-rag-work/competition-data/deliverable';
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
  const goto = async (path) => { await page.goto(BASE + path, { waitUntil: 'networkidle' }); await page.waitForTimeout(900); };
  try {
    await goto('/');
    const u = page.locator('input[autocomplete="username"]').first();
    if (await u.count()) {
      await u.fill(E2E_USER);
      await page.locator('input[type="password"]').first().fill(E2E_PASS);
      await page.locator('button[type="submit"]').first().click().catch(async () => {
        await page.getByRole('button', { name: /登.*录|注册/ }).first().click();
      });
      await page.waitForTimeout(1800);
    }
    const token = await page.evaluate(() => localStorage.getItem('edu_user_token'));
    const api = await page.request.get(BASE + '/api/competition/official-bundle', { headers: { 'X-User-Token': token } });
    const payload = await api.json();

    // ---------- P0-1 跨测验定位 ----------
    await goto('/student-answers?source=official&studentId=D01&questionId=Q002&quizId=N01&classId=DC01&kpId=T01I1');
    step('N01/D01/Q002=DR0006 0/1',
      await page.getByText(/来源记录 DR0006/).count() > 0 && await page.getByText(/得分 0/).count() > 0,
      await page.getByText(/来源记录 DR\d+/).first().textContent().catch(() => ''));
    await goto('/student-answers?source=official&studentId=D01&questionId=Q002&quizId=N02&classId=DC01&kpId=T01I1');
    step('N02/D01/Q002=DR0019 1/1',
      await page.getByText(/来源记录 DR0019/).count() > 0 && await page.getByText(/得分 1/).count() > 0);

    // 目标节点无记录：D01/Q043 只在 N02 有记录
    await goto('/student-answers?source=official&studentId=D01&questionId=Q043&quizId=N01&classId=DC01&kpId=T03I1');
    step('N01 无 Q043 记录时如实显示', await page.getByText(/无此题作答记录/).count() > 0);
    step('不跨测验拿 N02 答卷补上', await page.getByText(/来源记录 DR0023/).count() === 0);
    step('提供切换测验入口', await page.getByText(/切换到 N02/).count() > 0);

    // 班级下钻：N01/T01I1 DC01 → Q002 → 点 D01
    await goto('/class-analytics?source=official&quizId=N01');
    await page.getByRole('button', { name: '看明细表' }).click().catch(() => {}); await page.waitForTimeout(400);
    const cellT01I1 = page.locator('tr', { hasText: 'T01I1' }).locator('button').filter({ hasText: '%' }).first();
    if (await cellT01I1.count()) { await cellT01I1.click(); await page.waitForTimeout(500); }
    const drill = page.locator('.ant-drawer', { hasText: '失分明细' }).last();
    const drillText = await drill.textContent().catch(() => '');
    const idsInDrill = [...new Set((drillText.match(/D\d{2}/g) || []))];
    step('下钻未混入另一班学生（DC01=D01..D21）',
      idsInDrill.length > 0 && idsInDrill.every((x) => parseInt(x.slice(1), 10) <= 21), idsInDrill.join(','));
    const d01btn = drill.getByRole('button', { name: /D01 · 得分/ }).first();
    if (await d01btn.count()) {
      await d01btn.click(); await page.waitForTimeout(900);
      step('下钻跳转后仍在 N01 上下文', page.url().includes('quizId=N01')
        && await page.getByText(/来源记录 DR0006/).count() > 0, page.url());
    } else step('下钻跳转后仍在 N01 上下文', false, 'D01 按钮未找到');

    // 直接进答卷页：选学生后按测验列作答
    await goto('/student-answers?source=official');
    await page.locator('.ant-select').first().click();
    await page.locator('.ant-select-item-option', { hasText: 'D01' }).first().click();
    await page.waitForTimeout(600);
    const n01rows = await page.locator('.ant-list-item').count();
    step('直接入口列出 N01 作答（12 条事件）', n01rows === 12, `rows=${n01rows}`);
    // 切换测验到 N02
    const quizSelect = page.locator('.ant-select').nth(1);
    await quizSelect.click();
    await page.locator('.ant-select-item-option', { hasText: 'N02' }).first().click();
    await page.waitForTimeout(600);
    const n02rows = await page.locator('.ant-list-item').count();
    step('切换 N02 后列出 10 条事件', n02rows === 10, `rows=${n02rows}`);

    // 返回班级页恢复筛选（从 N02 答卷返回）
    await goto('/student-answers?source=official&studentId=D01&questionId=Q002&quizId=N02&classId=DC01&kpId=T01I1');
    await page.getByRole('button', { name: '返回班级学情' }).click();
    await page.waitForTimeout(900);
    step('返回班级页恢复 N02 测验', page.url().includes('quizId=N02')
      && await page.locator('.ant-select-selection-item', { hasText: 'N02' }).count() > 0, page.url());
    step('返回保留数据源', page.url().includes('source=official'));

    // ---------- P0-2 一致性 ----------
    await goto('/student-answers?source=official&studentId=D01&questionId=Q015&quizId=N01&classId=DC01&kpId=T01I2');
    const diag = (payload.diagnoses?.diagnoses || []).find((d) => d.student_id === 'D01' && d.node_id === 'N01' && d.indicator_id === 'T01I2');
    const caseEntry = (payload.case_book?.cases || []).find((c) => c.student_id === 'D01' && c.node_id === 'N01' && c.indicator_id === 'T01I2');
    step('诊断两层结论并存', !!diag && diag.case === '记录不足' && diag.question_case === '部分得分'
      && (diag.question_findings || []).some((f) => f.record_id === 'DR0001'));
    step('案例册与诊断一致（不覆盖）', !!caseEntry && !!diag
      && JSON.stringify(caseEntry.resource) === JSON.stringify(diag.resource)
      && caseEntry.case === diag.case && JSON.stringify(caseEntry.evidence_ids) === JSON.stringify(diag.evidence_ids));
    await page.getByText('完整建议与依据').first().click().catch(() => {}); await page.waitForTimeout(500);
    const bodyText = await page.locator('body').innerText();
    step('页面显示两层标签', bodyText.includes('本题：部分得分') && bodyText.includes('考点覆盖：记录不足'));
    step('页面建议与诊断同源', bodyText.includes(diag.resource.material_id) && bodyText.includes('核对 DR0001 最后一行'));
    step('无旧占位文案', !bodyText.includes('等待正式学习建议接入'));
    // CSV 行一致
    const csv = fs.readFileSync(DELIV + '/recommendations.csv', 'utf8').replace(/^\uFEFF/, '');
    const csvRow = csv.split(/\r?\n/).find((l) => l.startsWith('D01,N01,T01I2,'));
    step('CSV 与诊断一致', !!csvRow && csvRow.includes(',记录不足,部分得分,')
      && csvRow.includes(diag.resource.material_id) && csvRow.includes('EV-D01-N01-Q015'), (csvRow || '').slice(0, 120));
    await page.screenshot({ path: OUT + '/p0-main-case.png', fullPage: true });

    // ---------- P1-3 教师调整整套任务 ----------
    const beforeStats = JSON.stringify((payload.kps || []).find((k) => k.id === 'N01-T01I2')?.stats);
    await page.getByRole('button', { name: '教师调整' }).click();
    await page.waitForTimeout(300);
    // 优先选题目候选（材料编号与系统原建议不同，变化更直观）
    const qReplace = page.getByRole('button', { name: /替换为 Q/ });
    const replaceBtn = (await qReplace.count()) ? qReplace.first() : page.getByRole('button', { name: /替换为/ }).first();
    const replaceLabel = await replaceBtn.textContent().catch(() => '');
    const newMid = (replaceLabel.match(/替换为\s*([A-Z]\d+)/) || [])[1] || '';
    await replaceBtn.click(); await page.waitForTimeout(400);
    const currentCard = await page.locator('.ant-card', { hasText: '当前方案' }).last().textContent().catch(() => '');
    const afterReplace = await page.locator('body').innerText();
    step('替换后当前方案材料变化', !!newMid && currentCard.includes(newMid) && afterReplace.includes('本次改动'),
      `${diag.resource.material_id}→${newMid}`);
    // 学生预览同步整套任务
    await page.getByRole('button', { name: '预览学生反馈' }).click();
    await page.waitForTimeout(400);
    const previewText = await page.locator('.ant-card', { hasText: '反馈预览' }).last().textContent();
    step('学生预览同步新任务', previewText.includes(newMid) || previewText.includes('独立完成'), previewText.slice(0, 100));
    await page.getByRole('button', { name: /恢复系统建议/ }).click();
    await page.waitForTimeout(300);
    const restored = await page.locator('body').innerText();
    step('恢复回到系统原建议', restored.includes('核对 DR0001 最后一行'));
    // 先补测：具体题号
    await page.getByRole('button', { name: '改为先补测' }).click();
    await page.waitForTimeout(400);
    const pretestText = await page.locator('body').innerText();
    const preIds = diag.pretest_questions || [];
    step('先补测给出具体题号', preIds.length > 0 && preIds.every((q) => pretestText.includes(q)), preIds.join(','));
    await page.getByRole('button', { name: /恢复系统建议/ }).click();
    await page.waitForTimeout(300);
    await page.screenshot({ path: OUT + '/p0-teacher-adjust.png', fullPage: true });
    // 官方统计不受教师选择影响
    const api2 = await page.request.get(BASE + '/api/competition/official-bundle', { headers: { 'X-User-Token': token } });
    const payload2 = await api2.json();
    const afterStats = JSON.stringify((payload2.kps || []).find((k) => k.id === 'N01-T01I2')?.stats);
    const ansScore = (payload2.answers || []).find((a) => a.studentId === 'D01' && a.questionId === 'Q015' && a.quizId === 'N01')?.score;
    step('教师调整不改官方统计与评分', beforeStats === afterStats && ansScore === 2);

    // ---------- P1-4 证据回放 ----------
    await goto('/student-answers?source=official&studentId=D15&questionId=Q049&quizId=N02&classId=DC01&kpId=T03I2');
    await page.getByText('完整建议与依据').first().click().catch(() => {}); await page.waitForTimeout(500);
    step('回放卡标注自造', await page.getByText('自造回放').count() > 0);
    const expand = page.getByRole('button', { name: '展开对照' });
    if (await expand.count()) {
      await expand.click(); await page.waitForTimeout(400);
      const replayText = await page.locator('body').innerText();
      const sc = (payload.replay?.scenarios || []).find((s) => s.student_id === 'D15');
      step('回放前后数值正确', !!sc && replayText.includes('6/6') && replayText.includes('5/6')
        && replayText.includes('表现较好') && replayText.includes('部分得分'),
        sc ? `${sc.before.score}/${sc.before.full_score}→${sc.after.score}/${sc.after.full_score}` : 'no scenario');
      step('回放展示变动记录', replayText.includes('DR9001'));
      await page.screenshot({ path: OUT + '/p0-replay.png', fullPage: true });
      const back = page.getByRole('button', { name: '恢复官方视图' });
      if (await back.count()) { await back.click(); await page.waitForTimeout(300); }
      step('回放可恢复官方视图', await page.getByText('DR9001').count() === 0);
    } else step('回放前后数值正确', false, '展开对照按钮未找到');

    const realErrors = errors.filter((e) => !/favicon|404/i.test(e));
    step('无JS运行时错误', realErrors.length === 0, realErrors.slice(0, 2).join(' || '));
  } catch (e) {
    step('反例验收异常', false, String(e).slice(0, 300));
  } finally {
    fs.writeFileSync(OUT + '/e2e-p0-log.txt', log.join('\n'), 'utf8');
    await browser.close();
  }
})();
