const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const { E2E_AUTH_STATE, E2E_OUTPUT, E2E_BASE = 'http://127.0.0.1:8338', E2E_INDEPENDENT_BASE } = process.env;
if (!E2E_AUTH_STATE || !E2E_OUTPUT) throw new Error('Set E2E_AUTH_STATE and E2E_OUTPUT');
const auth = (origin) => {
  const saved = JSON.parse(fs.readFileSync(E2E_AUTH_STATE, 'utf8'));
  return { ...saved, origins: saved.origins.map((entry) => ({ ...entry, origin })) };
};

async function run() {
  const browser = await chromium.launch({ channel: 'msedge' });
  try {
    const context = await browser.newContext({ storageState: auth(E2E_BASE), viewport: { width: 1366, height: 768 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const shot = (name, target = page) => target.screenshot({ path: path.join(E2E_OUTPUT, name + '.png'), animations: 'disabled' });
    const profileUrl = (studentId, questionId, indicator, quizId = 'N01') => E2E_BASE + '/student-answers?' + new URLSearchParams({
      studentId, questionId, quizId, tab: 'profile', ...(indicator ? { followup: indicator } : {}),
    });
    const profile = page.getByTestId('student-profile');
    const followup = page.getByRole('region', { name: '课后五分钟补测' });
    const evidence = page.getByRole('region', { name: '作答证据分析' });
    const map = page.getByRole('region', { name: '证据图' });
    const openEditor = async () => followup.locator('summary').filter({ hasText: /补测安排/ }).click();
    const waitResponse = (method, endpoint, action, timeout = 30000) => Promise.all([
      page.waitForResponse((r) => r.url().includes(endpoint) && r.request().method() === method, { timeout }),
      action(),
    ]).then(([response]) => response);
    const approve = async () => {
      const save = followup.getByRole('button', { name: '教师确认并保存' });
      if (await save.isEnabled()) {
        const response = await waitResponse('PUT', '/competition/follow-up', () => save.click());
        assert.equal(response.status(), 200, await response.text());
      }
      await followup.getByRole('button', { name: '预览学生补测卡' }).waitFor();
    };
    const official = async () => page.evaluate(async () => {
      const response = await fetch('/api/competition/official-bundle', { headers: { 'X-User-Token': localStorage.getItem('edu_user_token') } });
      return response.json();
    });

    await page.goto(E2E_BASE + '/');
    await page.getByTestId('lecture-choice-Q014').getByRole('button', { name: '课后五分钟补测' }).click();
    await page.getByRole('dialog', { name: /课后五分钟补测 · 选择学生/ }).waitFor();
    await page.getByRole('button', { name: '安排学生 D01 的课后补测', exact: true }).click();
    await page.waitForURL((url) => url.searchParams.get('followup') === 'T01I1' && url.searchParams.get('studentId') === 'D01');
    await followup.getByRole('heading', { name: '课后五分钟补测' }).waitFor();

    await page.goto(profileUrl('D01', 'Q015'));
    await evidence.waitFor();
    await map.getByRole('link', { name: '看教师讲评卡' }).waitFor();
    assert.match(await map.innerText(), /第 15 题[\s\S]*2\/3/);
    await map.scrollIntoViewIfNeeded();
    await shot('04-evidence-map', map);
    const material = await waitResponse('GET', '/competition/official-pdf/B05',
      () => map.getByRole('button', { name: '打开第 98 页', exact: true }).click());
    assert.equal(material.status(), 200);
    const book = page.getByRole('dialog', { name: /第 98 页/ });
    await book.locator('[data-pdf-page="31"] canvas').waitFor();
    await book.getByRole('button', { name: '关闭', exact: true }).click();
    assert.match(await evidence.innerText(), /满分\s*\d+[\s\S]*部分得分[\s\S]*零分[\s\S]*写出正确结果/);
    await shot('01-profile-evidence', evidence);
    const baseline = await official();
    await page.getByRole('button', { name: '课后五分钟补测', exact: true }).click();
    await openEditor();
    assert.match(await followup.innerText(), /核对过程与填答|过程末行/);
    const draft = await waitResponse('POST', '/competition/follow-up/draft', () => followup.getByRole('button', { name: 'AI 拟定观察与提示' }).click(), 90000);
    assert.equal(draft.status(), 200, await draft.text());
    const invalid = await page.evaluate(async (plan) => (await fetch('/api/competition/follow-up', {
      method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-User-Token': localStorage.getItem('edu_user_token') },
      body: JSON.stringify({ ...plan, question_ids: ['Q050'] }),
    })).status, draft.request().postDataJSON());
    assert.equal(invalid, 422);
    await followup.getByText('AI 草稿已填入。', { exact: false }).waitFor();
    await followup.getByRole('textbox', { name: '分步提示' }).fill('先分别写出去括号、合并后的算式。\n把合并后的算式和最终填答逐项比一比。');
    await followup.getByLabel('下次复查日期').fill('2026-11-27');
    await approve();
    await followup.getByRole('button', { name: '预览学生补测卡' }).click();
    const preview = page.getByRole('dialog', { name: '学生补测卡' });
    assert.match(await preview.innerText(), /化简：2\(3x-1\)-\(x\+4\)/);
    assert.doesNotMatch(await preview.innerText(), /标准答案|原答卷|学生 D01/);
    await preview.getByRole('button', { name: '卡住时看提示' }).click();
    await preview.getByRole('button', { name: '再看一步提示' }).waitFor();
    assert.doesNotMatch(await preview.innerText(), /逐项比一比/);
    await preview.getByRole('button', { name: '再看一步提示' }).click();
    assert.match(await preview.innerText(), /逐项比一比/);
    await preview.getByRole('button', { name: /(?:写作答|查看或补充作答) · 第 4 题/ }).click();
    await preview.getByRole('textbox', { name: '补测作答 · 第 4 题' }).fill('先把 x 的系数相加，再写出常数项。');
    const written = await waitResponse('PUT', '/competition/follow-up', () => preview.getByRole('button', { name: '交给老师核对' }).click());
    assert.equal(written.status(), 200);
    await page.waitForFunction(() => {
      const modal = document.querySelector('.ant-modal-content');
      return modal?.getBoundingClientRect().height > 180 && !modal.closest('.ant-modal').className.includes('zoom-appear');
    });
    await shot('02-student-followup-card');

    async function tryPractice(combined, startedFromCard = false) {
      if (startedFromCard) await preview.getByRole('button', { name: '分步作答 · 第 9 题' }).click();
      else await followup.getByRole('button', { name: /打开分步核对 · 第 9 题/ }).click();
      const practice = page.getByTestId('live-pretest');
      await practice.waitFor();
      assert.equal(await practice.getAttribute('data-question-id'), 'Q009');
      assert.equal(new URL(page.url()).searchParams.get('questionId'), 'Q015');
      await practice.getByRole('button', { name: '卡住时看提示' }).waitFor();
      await practice.getByRole('textbox', { name: '去括号后' }).fill('6x-2-x-4');
      await practice.getByRole('textbox', { name: '合并后（可选）' }).fill(combined);
      await practice.getByRole('textbox', { name: '最终答案' }).fill('5x-5');
      await practice.getByRole('button', { name: '查看结果' }).click();
      await practice.getByRole('button', { name: '返回个人档案' }).click();
      await followup.getByRole('heading', { name: '补测作答与下一步' }).waitFor();
    }
    await tryPractice('', true);
    assert.match(await followup.innerText(), /仍需保留[\s\S]*原因待核实|原因待核实[\s\S]*仍需保留/);
    await tryPractice('5x-5');
    assert.match(await followup.innerText(), /支持「合并同类项」检查/);
    await tryPractice('5x-6');
    assert.match(await followup.innerText(), /支持「最终填答核对」/);
    await followup.getByText('本题核对记录（3 次）', { exact: true }).click();
    assert.match(await followup.innerText(), /支持「合并同类项」检查/);
    const comparison = followup.getByRole('region', { name: '原作答与补测对照' });
    assert.match(await comparison.innerText(), /4a-7b[\s\S]*4a\+7b[\s\S]*5x-6[\s\S]*5x-5[\s\S]*仍需保留/);
    const explained = await waitResponse('POST', '/competition/follow-up/explain',
      () => comparison.getByRole('button', { name: '请 AI 说明调整理由' }).click(), 90000);
    assert.equal(explained.status(), 200, await explained.text());
    await comparison.scrollIntoViewIfNeeded();
    await comparison.evaluate((element) => {
      const main = element.closest('.comp-main');
      main.scrollTop += element.getBoundingClientRect().top - main.getBoundingClientRect().top - 78;
    });
    await shot('03-followup-before-after');
    await followup.getByRole('button', { name: /记录教师观察 · 第 9 题/ }).click();
    await waitResponse('PUT', '/competition/follow-up', () => page.getByRole('button', { name: '保存观察', exact: true }).click());
    await followup.getByText('教师核对 · 需要复练：', { exact: true }).waitFor();
    await map.getByText(/教师已核对 1 题/).waitFor();
    const independentContext = await browser.newContext({ storageState: auth(E2E_BASE) });
    const second = await independentContext.newPage();
    await second.goto(profileUrl('D01', 'Q015', 'T01I2'));
    await second.getByText('教师核对 · 需要复练：', { exact: true }).waitFor();
    assert.equal(await second.getByRole('region', { name: '原作答与补测对照' }).count(), 0);
    const savedFollowup = await second.getByRole('region', { name: '课后五分钟补测' }).innerText();
    assert.match(savedFollowup, /2026-11-27[\s\S]*待教师核对/);
    assert.match(savedFollowup, /合并后：5x-6[\s\S]*最终填答：5x-5/);
    await independentContext.close();

    for (const [student, item, indicator, label] of [
      ['D15', 'Q019', 'T02I1', '观察方法迁移'], ['D17', 'Q014', 'T01I1', '补充观察'],
    ]) {
      await page.goto(profileUrl(student, item, indicator));
      await profile.waitFor();
      assert.match(await followup.innerText(), new RegExp(label));
      if (student === 'D17') assert.match(await followup.innerText(), /2 条正式有效记录 · 4\/4 分/);
      await openEditor();
      await approve();
      await followup.getByRole('button', { name: '预览学生补测卡' }).click();
      assert.match(await preview.innerText(), student === 'D15' ? /3\(x-2\)=2x\+5/ : /每支笔x元/);
      await preview.getByRole('button', { name: '关闭预览' }).click();
    }
    await page.goto(profileUrl('D17', 'Q013', 'T01I1'));
    await followup.getByRole('button', { name: /打开原答卷 · 第 13 题/ }).waitFor();
    assert.equal(await followup.getByRole('button', { name: '预览学生补测卡' }).count(), 0);
    await page.goto(profileUrl('D01', 'Q002', '', 'N02'));
    assert.match(await evidence.innerText(), /共同题对照[\s\S]*第 2 题[\s\S]*0\/1[\s\S]*1\/1/);
    assert.deepEqual(await official(), baseline);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.locator('.comp-main').evaluate((node) => node.scrollWidth <= node.clientWidth + 1));
    assert.deepEqual(errors, []);
    console.log('PASS: evidence, repeated items, AI draft, Q015–Q009 branches, persisted observation, D15, D17, and unchanged official data');
    if (E2E_INDEPENDENT_BASE) {
      const evaluation = await browser.newPage({ storageState: auth(E2E_INDEPENDENT_BASE) });
      await evaluation.goto(E2E_INDEPENDENT_BASE + '/student-answers?studentId=R17&questionId=Q014&quizId=N01&tab=profile');
      await evaluation.getByTestId('student-profile').waitFor();
      assert.equal(await evaluation.getByTestId('student-profile').getAttribute('data-student-id'), 'R17');
      assert.match(await evaluation.getByTestId('student-profile').innerText(), /独立评测数据/);
      assert.match(await evaluation.locator('[data-indicator-id="T01I1"]').innerText(), /4\/4 分 · 2 条有效记录/);
      console.log('PASS: independently imported R17 appears in the browser with the expected evidence');
    }
  } finally {
    await browser.close();
  }
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
