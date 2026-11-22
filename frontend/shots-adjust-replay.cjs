/* 补拍交付截图：教师调整前后对比（面板展开+已替换）、回放前后对照（展开） */
/** 登录凭据走环境变量，不写入仓库（本地运行：E2E_USER=教师账号 E2E_PASS=口令 node 脚本） */
const E2E_USER = process.env.E2E_USER;
const E2E_PASS = process.env.E2E_PASS;
if (!E2E_USER || !E2E_PASS) { console.error('需要 E2E_USER / E2E_PASS 环境变量（本地演示教师账号），不使用明文默认值'); process.exit(1); }
const { chromium } = require('playwright');
const BASE = 'http://127.0.0.1:8000';
const OUT = 'D:/Document/langchain-rag-work/competition-shots';
(async () => {
  const browser = await chromium.launch({
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true,
  });
  const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(900);
  const u = page.locator('input[autocomplete="username"]').first();
  if (await u.count()) {
    await u.fill(E2E_USER);
    await page.locator('input[type="password"]').first().fill(E2E_PASS);
    await page.locator('button[type="submit"]').first().click().catch(() => {});
    await page.waitForTimeout(1600);
  }
  // 教师调整前后对比（D01 主案例，替换为题目候选）
  await page.goto(BASE + '/student-answers?source=official&studentId=D01&questionId=Q015&quizId=N01&classId=DC01&kpId=T01I2', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);
  await page.getByRole('button', { name: '教师调整' }).click();
  await page.waitForTimeout(300);
  const q = page.getByRole('button', { name: /替换为 Q/ });
  if (await q.count()) { await q.first().click(); await page.waitForTimeout(400); }
  await page.getByText('教师调整（前后对照）').scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await page.screenshot({ path: OUT + '/deliver-teacher-adjust-before-after.png', fullPage: true });
  // 回放前后对照（D15）
  await page.goto(BASE + '/student-answers?source=official&studentId=D15&questionId=Q049&quizId=N02&classId=DC01&kpId=T03I2', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);
  const ex = page.getByRole('button', { name: '展开对照' });
  if (await ex.count()) { await ex.click(); await page.waitForTimeout(400); }
  // 把回放卡滚到视口顶部，保证前后对照表一屏可见
  await page.locator('.ant-card', { hasText: '证据回放' }).last().scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await page.screenshot({ path: OUT + '/deliver-replay-before-after.png', fullPage: true });
  console.log('shots done');
  await browser.close();
})();
