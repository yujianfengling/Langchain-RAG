const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const BASE = process.env.RECORDING_BASE || 'http://127.0.0.1:5174';
const OUT = process.env.RECORDING_OUT || 'D:/Document/langchain-rag-work/competition-data/recording-acceptance-20261120';
const USER = process.env.RECORDING_USER;
const PASSWORD = process.env.RECORDING_PASSWORD;
const results = [];
const traces = [];
const hash = (x) => crypto.createHash('sha256').update(JSON.stringify(x)).digest('hex');
async function check(name, fn) {
  try { const detail = await fn(); results.push({ name, status: 'PASS', detail }); console.log(`PASS | ${name} | ${detail || ''}`); }
  catch (e) { results.push({ name, status: 'FAIL', detail: e.message }); throw e; }
}
async function screenshot(page, locator, name) {
  await locator.evaluate((el) => { el.style.scrollMarginTop = '70px'; el.scrollIntoView({ block: 'start' }); });
  await page.screenshot({ path: path.join(OUT, name), mask: [page.getByText(USER, { exact: false })], maskColor: '#ffffff' });
}

(async () => {
  assert(USER && PASSWORD, 'Set RECORDING_USER and RECORDING_PASSWORD locally; never persist credentials.');
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const navigate = async (p) => { await page.goto(BASE + p); await page.getByText('官方赛题数据', { exact: true }).waitFor(); };
  const readBundle = async () => {
    const token = await page.evaluate(() => localStorage.getItem('edu_user_token'));
    const res = await page.request.get(BASE + '/api/competition/official-bundle', { headers: { 'X-User-Token': token } });
    assert.equal(res.status(), 200);
    return res.json();
  };
  const getPlan = () => page.getByTestId('review-plan');
  try {
    await page.goto(BASE + '/class-analytics?source=demo');
    await page.locator('input[autocomplete="username"]').fill(USER);
    await page.locator('input[type="password"]').first().fill(PASSWORD);
    await page.locator('button[type="submit"]').first().click();
    await page.locator('[data-testid=bar-compare], .ant-table').first().waitFor();
    await navigate('/class-analytics?source=official&quizId=N01&classId=DC01');
    const bundle = await readBundle();
    const originalHash = hash({ answers: bundle.answers, kps: bundle.kps, events: bundle.events });
    const plan3 = bundle.review_plans.strategies.common.budgets['3'];
    await check('从班级页核对讲评3道+额外补测3道，46份学生任务', async () => {
      assert.equal(plan3.workload.lecture_questions, 3); assert.equal(plan3.workload.pretest_questions, 3);
      assert.equal(plan3.workload.total_unique_questions, 6); assert.equal(plan3.workload.total_assignments, 46);
      const text = await getPlan().getByTestId('plan-workload').innerText();
      assert(text.includes('讲评 3/3 道')); assert(text.includes('补测 3/3 道')); assert(text.includes('46 份'));
      assert.deepEqual(plan3.workload.groups.map((g) => [g.item_id, g.students.length, g.questions_per_student, g.assignments]),
        [['Q001', 10, 1, 10], ['Q002', 9, 1, 9], ['Q003', 8, 1, 8]]);
      return 'Q001 10×1=10；Q002 9×1=9；Q003 8×1=8；讲评19+补测27=46';
    });
    await screenshot(page, getPlan(), '01-review-plan.png');
    await getPlan().getByText('每名学生的实际任务清单', { exact: true }).click();
    await check('每生清单与小组分配可复核', async () => {
      const d01 = plan3.workload.student_tasks.find((s) => s.student === 'D01');
      assert.deepEqual(d01.lecture_items, ['Q014', 'Q018']); assert.deepEqual(d01.pretest_items, ['Q003']);
      assert.equal(d01.task_count, 3);
      const row = getPlan().locator('tr').filter({ hasText: 'D01' });
      assert((await row.innerText()).includes('Q003'));
      return 'D01：讲评Q014/Q018 + 补测Q003 = 3份';
    });
    await getPlan().getByText('每名学生的实际任务清单', { exact: true }).click();
    await getPlan().locator('.ant-select:has(input[aria-label="方案题目预算"])').click();
    await page.locator('.ant-select-item-option').filter({ hasText: '2 道' }).click();
    await check('预算降到2道只减少讲评，补测仍3道', async () => {
      const txt = await getPlan().getByTestId('plan-workload').innerText();
      assert(txt.includes('讲评 2/2 道')); assert(txt.includes('补测 3/3 道')); assert(txt.includes('42 份'));
      return '2讲评+3补测=5道去重题；15+27=42份';
    });
    await getPlan().getByText('策略与复核').click().catch(() => {}); await page.waitForTimeout(400);
    await getPlan().locator('.ant-radio-button-wrapper').filter({ hasText: '优先给证据不足学生补测' }).click();
    await check('补测优先预算2：无讲评、2道补测', async () => {
      assert((await getPlan().getByTestId('plan-workload').innerText()).includes('不安排讲评；分组补测 2/2 道'));
    });
    await getPlan().locator('.ant-radio-button-wrapper').filter({ hasText: '优先讲共性失分' }).click().catch(() => {});
    await getPlan().locator('.ant-select:has(input[aria-label="方案题目预算"])').click();
    await page.locator('.ant-select-item-option').filter({ hasText: '3 道' }).click();
    await getPlan().getByTestId('lecture-choice-Q014').getByRole('button', { name: '看失分步骤' }).click().catch(() => {}); await page.waitForTimeout(400);
    await getPlan().getByTestId('lecture-choice-Q014').getByRole('button', { name: '看失分步骤' }).click().catch(() => {}); await page.waitForTimeout(400);
    await getPlan().getByRole('button', { name: 'D01', exact: true }).first().click();
    await page.getByText('作答详情', { exact: true }).waitFor();
    await check('从学生分组进入官方答卷，节点仍为N01', async () => {
      assert(page.url().includes('studentId=D01')); assert(page.url().includes('quizId=N01'));
      assert((await page.locator('body').innerText()).includes('来源记录'));
    });
    await navigate('/student-answers?source=official&studentId=D01&questionId=Q015&quizId=N01&classId=DC01&kpId=T01I2');
    await page.locator('.ant-card').filter({ hasText: '下一步' }).getByRole('button', { name: '现场补测' }).first().click();
    let live = page.getByTestId('live-pretest');
    assert.equal(await live.getAttribute('data-question-id'), 'Q009');
    await page.getByLabel('去括号后的式子', { exact: true }).fill('6x-2-x+4');
    await page.getByLabel('最终填答', { exact: true }).fill('5x-6');
    await page.getByRole('button', { name: '提交判断', exact: true }).click();
    await page.getByText('判定：中间变形出错', { exact: true }).waitFor();
    // 补测在独立面板里完成：先关闭面板回答卷页，再返回班级
    await page.getByRole('button', { name: '关闭补测，返回答卷' }).click();
    await page.getByRole('button', { name: /返回班级学情/ }).click();
    await getPlan().waitFor();
    const response = page.waitForResponse((r) => r.url().endsWith('/review-plan/recompute') && r.request().method() === 'POST');
    await getPlan().getByRole('button', { name: '用现场证据重算' }).click();
    const res = await response;
    assert.equal(res.status(), 200);
    const recalculated = await res.json(); traces.push(recalculated.audit);
    await check('53→54 的分母97→98、已覆盖44不变', async () => {
      assert.equal(recalculated.audit.before.total_points, 97); assert.equal(recalculated.audit.after.total_points, 98);
      assert.equal(recalculated.audit.before.covered_points, 44); assert.equal(recalculated.audit.after.covered_points, 44);
      assert.equal(recalculated.audit.before.uncovered_points, 53); assert.equal(recalculated.audit.after.uncovered_points, 54);
      const txt = await page.getByTestId('plan-recompute').innerText();
      for (const x of ['97', '98', '44', '53', '54']) assert(txt.includes(x));
      return '新增现场观察使分母+1；不是分数下降';
    });
    await check('具体新增项D01/Q009/LIVE-MID，学生任务新增0移除0保留46', async () => {
      const audit = recalculated.audit;
      assert.equal(audit.added_points.length, 1);
      assert.equal(audit.added_points[0].student, 'D01'); assert.equal(audit.added_points[0].item, 'Q009');
      assert.equal(audit.added_points[0].point_id, 'LIVE-MID'); assert(audit.added_points[0].record_id.startsWith('LIVE-'));
      assert.equal(audit.assignments.changed, false); assert.equal(audit.assignments.unchanged_count, 46);
      assert.equal(audit.assignments.added.length, 0); assert.equal(audit.assignments.removed.length, 0);
      assert.deepEqual(recalculated.base.workload.student_tasks, recalculated.new.workload.student_tasks);
      assert.deepEqual(recalculated.base.lecture_items, recalculated.new.lecture_items);
      assert.deepEqual(recalculated.base.pretest_tasks, recalculated.new.pretest_tasks);
      return '逐生/逐题/类型及任务内容相等；不宣称任务已调整';
    });
    await screenshot(page, page.getByTestId('plan-recompute'), '03-recompute-diff.png');
    await getPlan().getByRole('button', { name: '确认应用到当前演示会话' }).click();
    await check('应用会话后上方真实显示98项，学生任务仍46份', async () => {
      assert((await getPlan().innerText()).includes('观察项总数（分母）98'));
      assert((await getPlan().getByTestId('plan-workload').innerText()).includes('46 份'));
    });
    await getPlan().getByRole('button', { name: '清除会话应用' }).click();
    await check('清除应用恢复官方方案97项', async () => {
      assert((await getPlan().innerText()).includes('观察项总数（分母）97'));
    });
    await navigate('/student-answers?source=official&studentId=D01&questionId=Q015&quizId=N01&classId=DC01&kpId=T01I2');
    await page.locator('.ant-card').filter({ hasText: '下一步' }).getByRole('button', { name: '现场补测' }).first().click();
    await page.getByRole('button', { name: '清除现场作答' }).click();
    await page.getByRole('button', { name: '关闭补测，返回答卷' }).click();

    // 临时换 D08，输入值是独立人工复核样例，不由被测解析器生成。
    await page.locator('.ant-select').first().click();
    await page.locator('.ant-select-item-option').filter({ hasText: 'D08' }).first().click();
    const target = bundle.answers.find((a) => a.studentId === 'D08' && a.quizId === 'N01'
      && bundle.questions.find((q) => q.id === a.questionId)?.kpId === 'T01I2');
    assert(target, 'D08需要有N01/T01I2官方记录');
    await page.getByRole('button', { name: new RegExp(`^${target.questionId} ·`) }).click();
    await page.locator('.ant-card').filter({ hasText: '下一步' }).getByRole('button', { name: '现场补测' }).first().click();
    live = page.getByTestId('live-pretest');
    const picked = await live.getAttribute('data-question-id');
    const fixtures = {
      Q009: { mid: '6x-2-x-4', wrongMid: '6x-2-x+4', final: '5x-6', wrongFinal: '5x+6', equivalent: '-6+5x' },
      Q015: { mid: '6a-3b-2a-4b', wrongMid: '6a-3b-2a+4b', final: '4a-7b', wrongFinal: '4a+7b', equivalent: '-7b+4a' },
    };
    const f = fixtures[picked]; assert(f, `补测题${picked}需要独立期望值`);
    await check('临时换D08，界面显示所选学生和补测题', async () => {
      assert(page.url().includes('studentId=D08'));
      const txt = await live.innerText(); assert(txt.includes(picked)); assert(!txt.includes('去括号后 5x'));
      return `D08/N01/${picked}；未提前显示参考答案`;
    });
    async function submit(mid, combined, final, title, name) {
      await page.getByLabel('去括号后的式子', { exact: true }).fill(mid);
      await page.getByLabel('合并后过程末行', { exact: true }).fill(combined);
      await page.getByLabel('最终填答', { exact: true }).fill(final);
      await page.getByRole('button', { name: '提交判断', exact: true }).click();
      await check(name, async () => {
        await page.getByText(`判定：${title}`, { exact: true }).waitFor();
        return `${mid} | ${combined || '未提供'} | ${final} => ${title}`;
      });
    }
    await submit(f.wrongMid, f.final, f.final, '中间变形出错', 'D08去括号阶段差异');
    await submit(f.mid, f.wrongFinal, f.wrongFinal, '合并同类项有差异', 'D08合并阶段差异（过程末行首次不等价）');
    await submit(f.mid, f.final, f.wrongFinal, '过程正确·最终填答不一致', 'D08最终填答差异（前两步已核验）');
    await screenshot(page, live, '02-live-pretest.png');
    await submit(f.mid, '', f.wrongFinal, '中间正确·填答有误', '未提供过程末行：明确不能区分后两类原因');
    assert((await live.innerText()).includes('暂不能区分'));
    await submit(f.mid, 'x*x', f.final, '待人工核对', '超范围表达式不猜结果');
    await submit(f.mid, f.final, f.equivalent, '三处输入均等价', '支持等价写法，保留原覆盖判断');
    await page.getByRole('button', { name: '清除现场作答' }).click();
    await check('现场清除与官方数据隔离', async () => {
      const after = await readBundle();
      assert.equal(hash({ answers: after.answers, kps: after.kps, events: after.events }), originalHash);
      assert.equal(await page.getByRole('button', { name: '清除现场作答' }).count(), 0);
      assert.equal(await page.evaluate(() => localStorage.getItem('competition.reviewplan.applied.v1')), null);
      return `官方答卷/统计/事件SHA256前后一致：${originalHash}`;
    });
    await check('浏览器无运行时错误', async () => { assert.deepEqual(errors, []); });
  } catch (e) {
    console.error(e.message); process.exitCode = 1;
    console.error('URL', page.url(), 'BUTTONS', await page.getByRole('button').allTextContents());
    await page.screenshot({ path: path.join(OUT, 'failed-step.png'), mask: [page.getByText(USER, { exact: false })] });
    if (!results.some((r) => r.status === 'FAIL')) results.push({ name: '走查中断', status: 'FAIL', detail: e.message });
  } finally {
    fs.writeFileSync(path.join(OUT, 'walkthrough.json'), JSON.stringify({ base: BASE, viewport: '1366x768', results, traces }, null, 2));
    fs.writeFileSync(path.join(OUT, 'walkthrough.log'), results.map((r) => `${r.status} | ${r.name} | ${r.detail || ''}`).join('\n'));
    await context.close(); await browser.close();
  }
})();
