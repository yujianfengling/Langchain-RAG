// Runs against an isolated teacher database with the official competition dataset.
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');

const { E2E_AUTH_STATE, E2E_BASE, E2E_OUTPUT } = process.env;
if (!E2E_AUTH_STATE || !E2E_BASE || !E2E_OUTPUT) throw new Error('Set E2E_AUTH_STATE, E2E_BASE and E2E_OUTPUT');

async function run() {
  const browser = await chromium.launch({ channel: 'msedge' });
  try {
    const page = await browser.newPage({ storageState: E2E_AUTH_STATE, viewport: { width: 1366, height: 768 } });
    const original = E2E_BASE + '/student-answers?studentId=D01&classId=DC01&quizId=N01&questionId=Q018';
    await page.goto(original);
    await page.getByRole('link', { name: '教师讲评卡', exact: true }).click();
    await page.waitForURL((url) => url.pathname === '/curriculum' && url.searchParams.get('view') === 'lecture');
    const cardUrl = page.url();
    const source = page.getByRole('region', { name: '教材条目' });
    await source.getByRole('heading', { name: '教师讲评卡', exact: true }).waitFor();
    await page.waitForFunction(() => document.activeElement?.textContent === '教师讲评卡');
    await source.getByRole('button', { name: /^(自己填写|修改讲评卡)$/ }).click();
    const editor = source.getByRole('region', { name: '编辑讲评卡' });
    await editor.getByRole('textbox', { name: '从哪里开始讲', exact: true }).fill('');
    assert.equal(await editor.getByRole('button', { name: '确认保存讲评卡', exact: true }).isEnabled(), false);
    await editor.getByRole('textbox', { name: '从哪里开始讲', exact: true }).fill('先请学生说清为何计算结果与 x 无关。');
    await editor.getByRole('textbox', { name: '怎样借教材讲', exact: true }).fill('借第 71 页例 2 区分代数式中各项的含义。');
    await editor.getByRole('textbox', { name: '讲完怎么确认', exact: true }).fill('请说清合并后 x 项为零意味着什么。');
    const [save] = await Promise.all([
      page.waitForResponse((r) => r.url().endsWith('/lecture-card') && r.request().method() === 'PUT'),
      editor.getByRole('button', { name: '确认保存讲评卡', exact: true }).click(),
    ]);
    assert.equal(save.status(), 200, await save.text());
    const payload = save.request().postDataJSON();
    assert.equal(payload.question_id, 'Q018');
    assert.equal(payload.student_id, 'D01');
    assert.equal(payload.pdf_page, 4);
    assert.equal(payload.class_id, 'DC01');

    const otherPage = await browser.newPage({ storageState: E2E_AUTH_STATE });
    await otherPage.goto(cardUrl);
    await otherPage.getByRole('button', { name: '预览讲评卡', exact: true }).waitFor();
    await otherPage.close();

    await source.getByRole('button', { name: '预览讲评卡', exact: true }).click();
    const preview = page.getByRole('dialog', { name: '教师讲评卡 · 第 18 题', exact: true });
    assert.match(await preview.innerText(), new RegExp('D01.*原评分记录 · 2/3 分.*合并后 x 项为零意味着什么', 's'));
    await preview.locator('.teacher-lecture-preview').waitFor();
    await page.waitForFunction(() => {
      const panel = document.querySelector('.teacher-lecture-preview');
      return panel && panel.getBoundingClientRect().height > 180 && !panel.closest('.ant-modal').className.includes('zoom-appear');
    });
    await page.screenshot({ path: path.join(E2E_OUTPUT, 'teacher-lecture-card.png'), animations: 'disabled' });
    await preview.getByRole('button', { name: '打开第 71 页', exact: true }).click();
    const material = page.getByRole('dialog', { name: '七年级上册 · 第 71 页', exact: true });
    await material.locator('[data-pdf-page="4"] canvas').waitFor();
    await material.getByRole('button', { name: '关闭', exact: true }).click();
    await material.waitFor({ state: 'hidden' });
    await source.getByRole('link', { name: '安排课后补测', exact: true }).click();
    await page.waitForURL((url) => url.pathname === '/student-answers' && url.searchParams.get('tab') === 'profile'
      && url.searchParams.get('studentId') === 'D01' && url.searchParams.get('questionId') === 'Q018'
      && url.searchParams.get('followup') === 'T01I3');
    await page.getByRole('region', { name: '课后五分钟补测' }).waitFor();
    console.log('PASS: lecture card entry, edit, save, cross-browser reload, source page and original-question follow-up');
  } finally {
    await browser.close();
  }
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
