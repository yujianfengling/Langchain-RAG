/* 智慧学伴比赛版 · 真实数据链路验收（?source=real，读取 CSV bundle）
 * 流程：登录 → 真实两班统计 → 点考点 → 真实部分得分答卷 → 暂无学习建议
 * 说明：当前 bundle 由自造虚构 CSV 生成，仅验证「读取→统计→答卷」链路，不是官方真实数据。
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

(async () => {
  const browser = await chromium.launch({
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true,
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));

  try {
    // 登录
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1200);
    const userInput = page.locator('input[autocomplete="username"]').first();
    if (await userInput.count()) {
      await userInput.fill(E2E_USER);
      await page.locator('input[type="password"]').first().fill(E2E_PASS);
      await page.locator('button[type="submit"]').first().click().catch(async () => {
        await page.getByRole('button', { name: /登.*录|注册/ }).first().click();
      });
      await page.waitForTimeout(2200);
    }

    // 真实数据源：班级学情
    await page.goto(BASE + '/class-analytics?source=real', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    step('真实数据源标签', await page.getByText('CSV样例数据（非官方）').count() > 0);
    step('官方班级编号显示', await page.getByText('DC01').count() > 0 && await page.getByText('DC02').count() > 0);

    // 真实统计：IND-A 在 DC01 的得分率 58%（7/12）
    const has58 = await page.getByText('58%').count();
    step('真实统计 DC01/IND-A=58%(7/12)', has58 > 0, `58%命中=${has58}`);
    // DC02 真实零分
    const hasZero = await page.getByText(/0%（3 条有效）/).count();
    step('真实统计 DC02 真实零分', hasZero > 0, `零分命中=${hasZero}`);

    await page.screenshot({ path: OUT + '/real-class-analytics.png', fullPage: true });

    // 点 DC01 的 IND-A 考点（58% 按钮）
    const cell = page.locator('button').filter({ hasText: '58%' }).first();
    if (await cell.count()) {
      await cell.click();
      await page.waitForTimeout(500);
      step('点真实考点展开失分明细', await page.getByText(/失分明细/).count() > 0);
    } else {
      step('点真实考点展开失分明细', false, '未找到 58% 按钮');
    }

    // 点一名学生的真实部分得分答卷（ITEM-A2：样例学生乙 2/4）
    const stuBtn = page.getByRole('button', { name: /样例学生乙/ }).first();
    if (await stuBtn.count()) {
      await stuBtn.click();
      await page.waitForTimeout(900);
      step('跳真实答卷页并带定位参数',
        page.url().includes('student-answers') && page.url().includes('questionId=ITEM-A2'),
        page.url());
    } else {
      await page.goto(BASE + '/student-answers?source=real&studentId=S02&questionId=ITEM-A2&quizId=N01&classId=DC01&kpId=IND-A', { waitUntil: 'networkidle' });
      await page.waitForTimeout(800);
      step('直接定位真实答卷页', await page.getByText('作答详情').count() > 0, page.url());
    }

    // 真实答卷：题干来自 CSV、步骤/评分点、来源记录
    const stemOK = await page.getByText(/2a−3b/).count();
    const stepsOK = await page.getByText('原始解题步骤').count();
    const rubricOK = await page.getByText(/评分点（哪一步丢分/).count();
    const srcOK = await page.getByText(/来源记录 DR0004/).count();
    step('真实题干+步骤+评分点+来源记录',
      stemOK > 0 && stepsOK > 0 && rubricOK > 0 && srcOK > 0,
      `题干=${stemOK} 步骤=${stepsOK} 评分点=${rubricOK} 来源=${srcOK}`);
    step('真实部分得分 2/4', await page.getByText(/得分 2/).count() > 0);

    // 真实答卷旁不挂演示建议
    const noAdvice = await page.getByText(/暂无学习建议/).count();
    step('真实答卷显示暂无学习建议', noAdvice > 0, `命中=${noAdvice}`);
    const demoAdvice = await page.getByText(/教材例题（第 3 页）/).count();
    step('未把演示建议挂到真实学生', demoAdvice === 0, `演示建议命中=${demoAdvice}`);

    await page.screenshot({ path: OUT + '/real-student-answers.png', fullPage: true });

    const realErrors = errors.filter((e) => !/favicon|Failed to load resource: 404/i.test(e));
    step('无JS运行时错误', realErrors.length === 0, realErrors.slice(0, 3).join(' || '));
  } catch (e) {
    step('真实链路验收异常', false, String(e).slice(0, 300));
  } finally {
    fs.writeFileSync(OUT + '/e2e-real-log.txt', log.join('\n'), 'utf8');
    await browser.close();
  }
})();
