/* 智慧学伴比赛版 · 第六轮反例验收（?source=official）
 * 修复1 超范围DR0515 / 修复2 截至所选测验 / 修复3 本题标签随当前答卷 /
 * 修复4 零记录页显示补测任务 / 升级一 同题对比 / 升级二 现场补测三场景+正确+复用
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
  const goto = async (p) => { await page.goto(BASE + p, { waitUntil: 'networkidle' }); await page.waitForTimeout(900); };
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

    // ---- 修复1 超范围 ----
    await goto('/class-analytics?source=official&quizId=N01');
    await page.getByRole('button', { name: '看明细表' }).click().catch(() => {}); await page.waitForTimeout(400);
    const rowT03 = page.locator('tr', { hasText: 'T03I1' }).first();
    step('N01/T03I1 两班本次未测', (await rowT03.getByText('本次未测').count()) >= 2);
    await goto('/class-analytics?source=official&quizId=N01&classId=DC02&kpId=T03I1');
    await page.getByRole('button', { name: '看明细表' }).click().catch(() => {}); await page.waitForTimeout(400);
    // URL 里 classId=DC02 是讲评班 → 第一列就是 DC02
    await rowT03.locator('button').nth(0).click().catch(() => {}); await page.waitForTimeout(600);
    await page.getByText('待核异常（不计入统计）').click().catch(() => {}); await page.waitForTimeout(400);
    step('异常明细含 DR0515 超范围', await page.getByText('DR0515').count() > 0
      && await page.getByText(/超出测验范围/).count() > 0);
    await goto('/student-answers?source=official&studentId=D25&questionId=Q038&quizId=N01&classId=DC02&kpId=T03I1');
    step('答卷页超范围状态', await page.getByText(/超出所选测验的可用题范围/).count() > 0);
    const rec = payload.reconciliation || payload.manifest?.reconciliation || {};
    step('对账：超范围1/隔离2/纳入787/原始795-6-5', rec.out_of_scope_events === 1
      && rec.quarantined_events === 2
      && rec.included_scored_events === 787 && rec.raw_status?.['已评分'] === 795
      && rec.raw_status?.['待评分'] === 6 && rec.raw_status?.['未作答'] === 5 && rec.merged_events === 799,
      JSON.stringify(rec));

    // ---- 修复2 截至所选测验（浏览器侧反例） ----
    const d12n02 = (payload.events || []).filter((e) => e.studentId === 'D12' && e.quizId === 'N02'
      && (payload.questions.find((q) => q.id === e.questionId) || {}).kpId === 'T01I2').map((e) => e.questionId);
    const d12n01 = (payload.events || []).filter((e) => e.studentId === 'D12' && e.quizId === 'N01'
      && (payload.questions.find((q) => q.id === e.questionId) || {}).kpId === 'T01I2').map((e) => e.questionId);
    const laterOnly = d12n02.filter((x) => !d12n01.includes(x));
    await goto('/student-answers?source=official&studentId=D12&questionId=' + (d12n01[0] || 'Q015') + '&quizId=N01&classId=DC01&kpId=T01I2');
    await page.getByRole('button', { name: '教师调整' }).click(); await page.waitForTimeout(300);
    let asofOk = laterOnly.length > 0;
    for (const qid of laterOnly) {
      const btn = page.getByRole('button', { name: new RegExp('替换为 ' + qid) });
      if (await btn.count()) {
        const t = await btn.textContent();
        if (!t.includes('未做过')) asofOk = false;
      } else asofOk = false;
    }
    step('N01 候选题不把 N02 才做过的题标已做过', asofOk, 'laterOnly=' + laterOnly.join(','));

    // ---- 修复3 本题标签随当前答卷 ----
    await goto('/student-answers?source=official&studentId=D01&questionId=Q013&quizId=N01&classId=DC01&kpId=T01I1');
    step('满分题显示本题满分', await page.getByText('本题：满分（Q013）').count() > 0
      && await page.getByText(/本题满分、无失分评分点/).count() > 0);
    await goto('/student-answers?source=official&studentId=D01&questionId=Q014&quizId=N01&classId=DC01&kpId=T01I1');
    const q014 = (payload.answers || []).find((a) => a.studentId === 'D01' && a.questionId === 'Q014' && a.quizId === 'N01');
    step('失分题显示本题部分得分+当前评分点', await page.getByText('本题：部分得分（Q014）').count() > 0
      && await page.getByText('本题：满分').count() === 0
      && !!q014 && await page.getByText(q014.scoringPoints[0].label).count() > 0);

    // ---- 修复4 零记录页显示任务 ----
    const d20ev = (payload.events || []).find((e) => e.studentId === 'D20' && e.quizId === 'N01'
      && (payload.questions.find((q) => q.id === e.questionId) || {}).kpId === 'T01I3');
    await goto('/student-answers?source=official&studentId=D20&questionId=' + d20ev.questionId + '&quizId=N01&classId=DC01&kpId=T01I3');
    await page.getByText('完整建议与依据').first().click().catch(() => {}); await page.waitForTimeout(500);
    const bodyD20 = await page.locator('body').innerText();
    step('D20 待评分+0条+得分率—+补测Q011', bodyD20.includes('待评分')
      && bodyD20.includes('考点覆盖：记录不足（0/3 条）') && bodyD20.includes('得分率 —')
      && bodyD20.includes('Q011'), d20ev.recordId);
    await goto('/student-answers?source=official&studentId=D16&questionId=Q044&quizId=N02&classId=DC01&kpId=T03I2');
    await page.getByText('完整建议与依据').first().click().catch(() => {}); await page.waitForTimeout(500);
    const bodyD16 = await page.locator('body').innerText();
    step('D16 隔离状态+右栏任务', bodyD16.includes('分数越界') && bodyD16.includes('考点覆盖：记录不足（0/3 条）')
      && bodyD16.includes('Q045'));
    await goto('/student-answers?source=official&studentId=D01&questionId=Q002&quizId=N01&classId=DC01&kpId=T01I1');
    step('有效零分与未作答区分', await page.getByText(/得分 0/).count() > 0
      && await page.getByText('该题在所选测验未作答').count() === 0);

    // ---- 升级一 同题对比 ----
    await goto('/class-analytics?source=official&quizId=N02');
    await page.getByRole('button', { name: '看明细表' }).click().catch(() => {}); await page.waitForTimeout(400);
    const rowT03I1 = page.locator('tr', { hasText: 'T03I1' }).first();
    // R4：失分明细在抽屉里，断言时保持抽屉打开
    await rowT03I1.locator('button').first().click(); await page.waitForTimeout(600);
    const panelDC01 = await page.locator('body').innerText();
    step('DC01 全部口径 57.89% 33/57', panelDC01.includes('57.89%') && panelDC01.includes('33') && panelDC01.includes('57'));
    await page.keyboard.press('Escape'); await page.waitForTimeout(500);
    await rowT03I1.locator('button').nth(1).click(); await page.waitForTimeout(600);
    const panelDC02 = await page.locator('body').innerText();
    step('DC02 全部口径 53.19% 25/47', panelDC02.includes('53.19%') && panelDC02.includes('25') && panelDC02.includes('47'));
    await page.keyboard.press('Escape'); await page.waitForTimeout(500);
    await page.locator('.ant-radio-button-wrapper', { hasText: '仅比较共同题目' }).click(); await page.waitForTimeout(500);
    await rowT03I1.locator('button').nth(1).click(); await page.waitForTimeout(600);
    const panelCommon = await page.locator('body').innerText();
    step('DC02 共同口径 48.78% 20/41', panelCommon.includes('48.78%') && panelCommon.includes('20')
      && panelCommon.includes('41'));
    step('仅一边题目 Q038 可见', panelCommon.includes('Q038'));
    step('口径说明文案', panelCommon.includes('学生覆盖与作答分布仍可能不同'));
    await page.screenshot({ path: OUT + '/r6-same-question.png', fullPage: true });
    await page.keyboard.press('Escape'); await page.waitForTimeout(400);
    const sq = JSON.parse(fs.readFileSync(DELIV + '/same_question_comparison.json', 'utf8'));
    const node = sq.find((x) => x.node_id === 'N02' && x.indicator_id === 'T03I1');
    step('导出两口径可复算', !!node && node.classes.DC01.all.score === 33 && node.classes.DC01.all.full_score === 57
      && node.classes.DC02.all.score === 25 && node.classes.DC02.all.full_score === 47
      && node.classes.DC02.common.score === 20 && node.classes.DC02.common.full_score === 41
      && (node.only.DC02 || []).includes('Q038'));
    await goto('/class-analytics?source=official&quizId=N01');
    await page.getByRole('button', { name: '看明细表' }).click().catch(() => {}); await page.waitForTimeout(400);
    const rowN01T03 = page.locator('tr', { hasText: 'T03I1' }).first();
    await rowN01T03.locator('button').first().click(); await page.waitForTimeout(400);
    await page.keyboard.press('Escape'); await page.waitForTimeout(400);
    await page.locator('.ant-radio-button-wrapper', { hasText: '仅比较共同题目' }).click(); await page.waitForTimeout(400);
    step('无共同题明确无法比较', await page.getByText(/无法同题比较/).count() > 0);

    // ---- 升级二 现场补测 ----
    const statsBefore = JSON.stringify((payload.kps || []).find((k) => k.id === 'N01-T01I2')?.stats);
    await goto('/student-answers?source=official&studentId=D01&questionId=Q015&quizId=N01&classId=DC01&kpId=T01I2');
    await page.locator('.ant-card').filter({ hasText: '下一步' }).getByRole('button', { name: '现场补测' }).first().click(); await page.waitForTimeout(400);
    const preBody = await page.locator('body').innerText();
    const chosen = (payload.questions || []).filter((q) => q.kpId === 'T01I2' && q.stem.includes('化简') && q.stem.includes('(') && q.referenceAnalysis)
      .map((q) => ({ q, segs: q.referenceAnalysis.split('；').map((s) => s.trim()).filter(Boolean) }))
      .filter((x) => x.segs.length >= 2 && parseLinear(stripPrefix(x.segs[0])) && parseLinear(stripPrefix(x.segs[x.segs.length - 1])))
      .sort((a, b) => a.q.id.localeCompare(b.q.id))[0];
    step('补测选题为同指标可两步观察题', !!chosen && preBody.includes(chosen.q.id) && preBody.includes('为什么选这道题'), chosen?.q.id);
    const midExp = stripPrefix(chosen.segs[0]);
    const finExp = stripPrefix(chosen.segs[chosen.segs.length - 1]);
    const fill = async (m, f) => {
      await page.getByLabel('去括号后的式子', { exact: true }).fill(m);
      await page.getByLabel('最终填答', { exact: true }).fill(f);
      await page.getByRole('button', { name: '提交判断' }).click();
      await page.waitForTimeout(350);
    };
    // 场景a 中间错（把常数符号改错）
    const midWrong = midExp.replace(/-4$/, '+4').replace(/-2$/, '+2');
    await fill(midWrong === midExp ? '0' : midWrong, finExp);
    step('场景a 中间变形出错+去括号提示', await page.getByText('判定：中间变形出错').count() > 0
      && await page.getByText(/去括号或符号检查/).count() > 0);
    await page.getByRole('button', { name: '重新输入' }).click(); await page.waitForTimeout(200);
    // 场景b 中间对最终错
    const finWrong = finExp.replace(/-6$/, '+6').replace(/-7b$/, '+7b');
    await fill(midExp, finWrong === finExp ? '0' : finWrong);
    step('场景b 中间正确填答有误+结果核对', await page.getByText('判定：中间正确·填答有误').count() > 0
      && await page.getByText(/结果核对/).count() > 0);
    await page.getByRole('button', { name: '重新输入' }).click(); await page.waitForTimeout(200);
    // 场景c 无法解析
    await fill('???', finExp);
    step('场景c 待人工核对', await page.getByText('判定：待人工核对').count() > 0
      && await page.getByText(/重新输入，或交由教师核对/).count() > 0);
    await page.getByRole('button', { name: '重新输入' }).click(); await page.waitForTimeout(200);
    // 场景d 全对
    await fill(midExp, finExp);
    const dBody = await page.locator('body').innerText();
    step('场景d 两步正确+覆盖判断保留', await page.getByText('判定：两步正确').count() > 0
      && dBody.includes('该测验下该考点有效记录 1 条'));
    step('现场记录4条单独保存', await page.getByText(/现场作答记录（4 条/).count() > 0);
    await page.screenshot({ path: OUT + '/r6-live-pretest.png', fullPage: true });
    await page.getByRole('button', { name: '清除现场作答' }).click(); await page.waitForTimeout(300);
    step('清除现场作答', await page.getByText(/现场作答记录（/).count() === 0);
    const payload2 = await (await page.request.get(BASE + '/api/competition/official-bundle', { headers: { 'X-User-Token': token } })).json();
    step('官方统计与评分不受现场作答影响',
      JSON.stringify((payload2.kps || []).find((k) => k.id === 'N01-T01I2')?.stats) === statsBefore
      && (payload2.answers || []).find((a) => a.studentId === 'D01' && a.questionId === 'Q015' && a.quizId === 'N01')?.score === 2);
    // 复用：另一学生 D12
    await goto('/student-answers?source=official&studentId=D12&questionId=' + (d12n01[0] || 'Q015') + '&quizId=N01&classId=DC01&kpId=T01I2');
    await page.locator('.ant-card').filter({ hasText: '下一步' }).getByRole('button', { name: '现场补测' }).first().click(); await page.waitForTimeout(400);
    const preBody2 = await page.locator('body').innerText();
    const d12diag = (payload.diagnoses?.diagnoses || []).find((x) => x.student_id === 'D12' && x.node_id === 'N01' && x.indicator_id === 'T01I2');
    const chosen2 = (payload.questions || []).filter((q) => q.kpId === 'T01I2' && q.stem.includes('化简') && q.stem.includes('(') && q.referenceAnalysis)
      .map((q) => ({ q, segs: q.referenceAnalysis.split('；').map((s) => s.trim()).filter(Boolean) }))
      .filter((x) => {
        if (x.segs.length < 2) return false;
        const m = parseLinear(stripPrefix(x.segs[0]));
        const f = parseLinear(stripPrefix(x.segs[x.segs.length - 1]));
        return m && f && sameLinear(m, f) && stripPrefix(x.segs[0]).replace(/\s/g, '') !== stripPrefix(x.segs[x.segs.length - 1]).replace(/\s/g, '');
      })
      .map((x) => ({ ...x, done: (d12diag?.candidates || []).find((c) => c.material_id === x.q.id)?.done ?? false }))
      .sort((a, b) => Number(a.done) - Number(b.done) || a.q.id.localeCompare(b.q.id))[0];
    await fill(stripPrefix(chosen2.segs[0]), stripPrefix(chosen2.segs[chosen2.segs.length - 1]));
    step('复用：D12 同逻辑判定正确', preBody2.includes(chosen2.q.id) && await page.getByText('判定：两步正确').count() > 0);
    await page.getByRole('button', { name: '清除现场作答' }).click(); await page.waitForTimeout(200);

    // ---- 随机学生换测验核对 ----
    const d09n02 = (payload.answers || []).filter((a) => a.studentId === 'D09' && a.quizId === 'N02');
    const pick = d09n02[0];
    await goto('/student-answers?source=official&studentId=D09&questionId=' + pick.questionId + '&quizId=N02&classId=DC01&kpId=' + (payload.questions.find((q) => q.id === pick.questionId) || {}).kpId);
    const ind9 = (payload.questions.find((q) => q.id === pick.questionId) || {}).kpId;
    const csv = fs.readFileSync(DELIV + '/recommendations.csv', 'utf8').replace(/^\uFEFF/, '');
    const row9 = csv.split(/\r?\n/).find((l) => l.startsWith('D09,N02,' + ind9 + ','));
    const d9 = (payload.diagnoses?.diagnoses || []).find((x) => x.student_id === 'D09' && x.node_id === 'N02' && x.indicator_id === ind9);
    step('随机学生 D09/N02 页面与导出一致', !!row9 && !!d9
      && await page.getByText(new RegExp('得分 ' + pick.score)).count() > 0
      && row9.includes(',' + d9.case + ',') && row9.includes(d9.resource.material_id), pick.questionId + '/' + ind9);

    const realErrors = errors.filter((e) => !/favicon|404/i.test(e));
    step('无JS运行时错误', realErrors.length === 0, realErrors.slice(0, 2).join(' || '));
  } catch (e) {
    step('R6 验收异常', false, String(e).slice(0, 300));
  } finally {
    fs.writeFileSync(OUT + '/e2e-r6-log.txt', log.join('\n'), 'utf8');
    await browser.close();
  }
})();
