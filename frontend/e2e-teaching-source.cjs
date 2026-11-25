const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');

const { E2E_AUTH_STATE, E2E_OUTPUT, E2E_BASE = 'http://127.0.0.1:8315' } = process.env;
if (!E2E_AUTH_STATE || !E2E_OUTPUT) throw new Error('Set E2E_AUTH_STATE and E2E_OUTPUT');

async function run() {
  const browser = await chromium.launch({ channel: 'msedge' });
  try {
    const page = await browser.newPage({ storageState: E2E_AUTH_STATE, viewport: { width: 1366, height: 768 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const shot = (name, target = page) => target.screenshot({ path: path.join(E2E_OUTPUT, name + '.png'), animations: 'disabled' });
    await page.goto(E2E_BASE + '/');
    const lecture = page.getByTestId('lecture-choice-Q018');
    await lecture.getByRole('button', { name: /看答卷/ }).click();
    await page.getByRole('link', { name: '查教学出处' }).click();
    const source = page.getByRole('region', { name: '教材条目' });
    await source.getByRole('heading', { name: '用整式解释具体问题' }).waitFor();
    assert.match(await page.getByRole('region', { name: '原题与答卷' }).innerText(), /班级 DC01.*第 18 题/);
    await page.getByText('评委复核明细', { exact: true }).click();
    assert.match(await page.locator('.source-audit').innerText(), /B05.*第 71 页.*PDF 文件第 4 页/);
    await page.getByText('评委复核明细', { exact: true }).click();
    await page.getByRole('link', { name: '返回考试分析' }).click();
    await page.waitForURL('**/class-analytics**');
    await page.getByRole('dialog', { name: '看失分情况' }).getByRole('button', { name: 'D01', exact: true }).click();
    await page.waitForURL((url) => url.pathname === '/student-answers' && url.searchParams.get('studentId') === 'D01');
    const originalUrl = page.url();
    const answerSource = page.getByRole('region', { name: '原题与得分' }).getByRole('link', { name: '查教学出处' });
    await answerSource.click();
    await page.waitForURL((url) => url.pathname === '/curriculum' && url.searchParams.get('studentId') === 'D01');
    await page.getByRole('heading', { name: '课标与教材', exact: true }).waitFor();
    assert.match(await page.getByRole('region', { name: '原题与答卷' }).innerText(), /学生 D01.*第 18 题[\s\S]*2\/3 分/);
    await shot('01-teaching-source');
    const sequence = page.getByRole('region', { name: '考点学习顺序' });
    await sequence.getByRole('button', { name: '表示数量关系并代入求值', exact: true }).click();
    await source.getByRole('heading', { name: '表示数量关系并代入求值', exact: true }).waitFor();
    assert.equal(await source.getByRole('button', { name: '用这页教材备一段讲评' }).count(), 0);
    await source.getByRole('button', { name: '返回本题考点' }).click();
    await source.getByRole('heading', { name: '用整式解释具体问题' }).waitFor();

    const [pdfResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith('/official-pdf/B05')),
      source.getByRole('button', { name: /打开第 71 页/ }).click(),
    ]);
    assert.equal(pdfResponse.status(), 200);
    const drawer = page.getByRole('dialog');
    const originalPage = drawer.locator('[data-pdf-page="4"] canvas');
    await originalPage.waitFor();
    await page.waitForFunction(() => {
      const canvas = document.querySelector('[data-pdf-page="4"] canvas');
      return canvas?.getContext('2d')?.getImageData(0, 0, 1, 1).data[3] === 255;
    });
    assert.match(await drawer.innerText(), /七年级上册 · 第 71 页/);
    await shot('02-textbook-page-71');
    await drawer.getByRole('button', { name: '关闭', exact: true }).click();
    await drawer.waitFor({ state: 'hidden' });
    const [standardResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith('/official-pdf/CR')),
      page.getByRole('button', { name: '查看课标原页' }).click(),
    ]);
    assert.equal(standardResponse.status(), 200);
    await drawer.locator('canvas').waitFor();
    await drawer.getByRole('button', { name: '关闭', exact: true }).click();
    await drawer.waitFor({ state: 'hidden' });

    async function generate(expectedPage) {
      const [result] = await Promise.all([
        page.waitForResponse((r) => r.url().endsWith('/review-draft'), { timeout: 90_000 }),
        source.getByRole('button', { name: '用这页教材备一段讲评' }).click(),
      ]);
      const request = result.request().postDataJSON();
      assert.equal(request.pdf_page, expectedPage);
      assert.equal(request.student_id, 'D01');
      assert.equal(result.status(), 200, await result.text());
      const draft = page.getByRole('region', { name: 'AI 讲评草稿' });
      await draft.waitFor();
      assert.equal(await draft.locator('dd').count(), 3);
      return draft;
    }
    const draft = await generate(4);
    assert.match(await draft.innerText(), /原评分记录 · 2\/3 分/);
    await shot('03-ai-review', draft);
    await draft.getByRole('link', { name: '返回原答卷' }).click();
    await page.waitForURL(originalUrl);
    assert.equal(page.url(), originalUrl);

    await page.locator('.teacher-question-nav').getByRole('button', { name: /^第 2 题/ }).click();
    await page.waitForURL((url) => url.searchParams.get('questionId') === 'Q002');
    await answerSource.click();
    assert.match(await page.getByRole('region', { name: '原题与答卷' }).innerText(), /0\/1 分.*最终填答 14.*未记录解题步骤/);
    const missing = await generate(2);
    assert.match(await missing.innerText(), /无法判断错在哪一步/);
    await shot('04-ai-missing-steps', missing);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.locator('.comp-main').evaluate((node) => node.scrollWidth <= node.clientWidth + 1));
    assert.deepEqual(errors, []);
    console.log('PASS: official loss question, original answer, textbook page 71, standard, AI, return, missing steps and mobile');
  } finally {
    await browser.close();
  }
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
