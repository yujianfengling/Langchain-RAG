/* 智慧学伴比赛版 · 浏览器全流程验收（Playwright + 系统 Edge）
 * 流程：登录 → 两班对比 → 点失分 → 学生答卷 → 预览反馈 → 开教材指定页 → 返回保留筛选
 * 截图：班级页、答卷页。仅本地验收，不提交。
 */
/** 登录凭据走环境变量，不写入仓库（本地运行：E2E_USER=教师账号 E2E_PASS=口令 node 脚本） */
const E2E_USER = process.env.E2E_USER;
const E2E_PASS = process.env.E2E_PASS;
if (!E2E_USER || !E2E_PASS) { console.error('需要 E2E_USER / E2E_PASS 环境变量（本地演示教师账号），不使用明文默认值'); process.exit(1); }
const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:8000';
const OUT = 'D:/Document/langchain-rag-work/competition-shots';
const fs = require('fs');
fs.mkdirSync(OUT, { recursive: true });

const log = [];
function step(name, ok, detail = '') {
  const line = `${ok ? 'PASS' : 'FAIL'} | ${name}${detail ? ' | ' + detail : ''}`;
  log.push(line);
  console.log(line);
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
    // 1. 打开首页 → 应跳到登录页
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);
    const url1 = page.url();
    step('首页加载', true, url1);

    // 2. 登录（内联表单）
    const userInput = page.locator('input[autocomplete="username"]').first();
    if (await userInput.count()) {
      await userInput.fill(E2E_USER);
      await page.locator('input[type="password"]').first().fill(E2E_PASS);
      // 按钮文案是「登 录」（中间有空格）
      await page.locator('button[type="submit"]').first().click().catch(async () => {
        await page.getByRole('button', { name: /登.*录|注册/ }).first().click();
      });
      await page.waitForTimeout(2500);
      step('登录教师账号', page.url().includes('class-analytics'), page.url());
    } else {
      step('登录表单存在', false, '未找到用户名输入框');
    }

    // 3. 班级学情页
    await page.goto(BASE + '/class-analytics?source=demo', { waitUntil: 'networkidle' }).catch(() => {});
    await page.waitForTimeout(1200);
    const titleText = await page.locator('h2, h1').first().textContent().catch(() => '');
    const hasTable = await page.getByText('两班考点对比').count();
    step('班级学情-考点对比表', hasTable > 0, '标题=' + titleText);
    // 两班名
    const hasA = await page.getByText('七年级（1）班').count();
    const hasB = await page.getByText('七年级（2）班').count();
    step('两班显示', hasA > 0 && hasB > 0, `班A=${hasA} 班B=${hasB}`);
    // 三种状态
    const statusNone = await page.getByText('本次未测').count();
    const statusFew = await page.getByText(/记录不足/).count();
    const statusZero = await page.getByText('真实零分').count();
    step('状态区分', statusNone > 0 && statusFew > 0 && statusZero > 0,
      `未测=${statusNone} 记录不足=${statusFew} 零分=${statusZero}`);

    // 只出现三个正式主题，无旧考点
    const t1 = await page.getByText(/代数式与整式运算/).count();
    const t2 = await page.getByText(/方程与方程组/).count();
    const t3 = await page.getByText(/不等式与不等式组/).count();
    const oldGeo = await page.getByText(/几何图形初步|补角与余角|数据的收集与整理|平均数/).count();
    step('三个正式主题且无旧考点', t1 > 0 && t2 > 0 && t3 > 0 && oldGeo === 0,
      `主题=${t1}/${t2}/${t3} 旧考点残留=${oldGeo}`);

    // 切换主题筛选后三种状态仍正确显示（选"不等式"主题：本次未测+记录不足都在）
    const themeLabel = page.locator('label.ant-radio-button-wrapper').filter({ hasText: '不等式与不等式组' }).first();
    if (await themeLabel.count()) {
      await themeLabel.click();
      await page.waitForTimeout(400);
      const noneAfter = await page.getByText('本次未测').count();
      const fewAfter = await page.getByText(/记录不足/).count();
      step('切换筛选后状态仍正确', noneAfter > 0 && fewAfter > 0,
        `未测=${noneAfter} 记录不足=${fewAfter}`);
      // 切回全部，避免影响后续点击
      const allLabel = page.locator('label.ant-radio-button-wrapper').filter({ hasText: '全部' }).first();
      if (await allLabel.count()) await allLabel.click();
      await page.waitForTimeout(300);
    } else {
      step('切换筛选后状态仍正确', false, '主题筛选按钮未找到');
    }
    await page.screenshot({ path: OUT + '/class-analytics.png', fullPage: true });
    step('截图-班级页', true, OUT + '/class-analytics.png');

    // 4. 点一个班的考点（一元一次方程解法在下钻有部分得分题）
    // 找到"一元一次方程的解法"行里第一个可点单元格
    const kpCell = page.locator('button').filter({ hasText: '62%' }).first();
    if (await kpCell.count()) {
      await kpCell.click();
      await page.waitForTimeout(600);
      step('点考点展开失分明细', await page.getByText(/失分明细/).count() > 0);
    } else {
      // 退而求其次：点任意含百分比的按钮
      const anyCell = page.locator('button').filter({ hasText: '%' }).first();
      if (await anyCell.count()) { await anyCell.click(); await page.waitForTimeout(600); }
      step('点考点展开失分明细', await page.getByText(/失分明细/).count() > 0, 'fallback');
    }

    // 5. 从明细点学生 → 跳答卷页（带筛选参数）
    const studentLink = page.getByRole('button', { name: /李明/ }).first();
    if (await studentLink.count()) {
      await studentLink.click();
      await page.waitForTimeout(1000);
      step('跳学生答卷并带筛选', page.url().includes('student-answers') && page.url().includes('kpId'), page.url());
    } else {
      // 直接构造带参 URL 验证定位
      await page.goto(BASE + '/student-answers?studentId=stu-001&questionId=q-201&quizId=quiz-mid&classId=class-a&kpId=kp-eq&source=demo', { waitUntil: 'networkidle' }).catch(()=>{});
      await page.waitForTimeout(1000);
      step('带参定位答卷页', page.getByText('作答详情').count() > 0, page.url());
    }

    // 6. 答卷页：部分得分、评分点、失分步
    const hasSteps = await page.getByText('原始解题步骤').count();
    const hasSp = await page.getByText('评分点（哪一步丢分）').count();
    const hasScore = await page.getByText(/得分 4/).count();
    step('答卷-步骤与评分点', hasSteps > 0 && hasSp > 0, `步骤=${hasSteps} 评分点=${hasSp}`);
    step('答卷-部分得分题(4/10)', hasScore > 0);

    // 评分点加总 = 总分：2/2 + 0/4 + 2/2 + 0/2 = 4/10
    const earned = await page.locator('text=/\\d+\\/\\d+ 分/').allTextContents();
    const nums = earned.map((s) => (s.match(/(\d+)\/(\d+)/) || []).slice(1).map(Number));
    const eSum = nums.reduce((a, n) => a + (n[0] || 0), 0);
    const fSum = nums.reduce((a, n) => a + (n[1] || 0), 0);
    step('评分点加总与总分一致', eSum === 4 && fSum === 10 && nums.length === 4,
      `得分和=${eSum} 满分和=${fSum} 评分点数=${nums.length}`);
    // 关键证据文案：移项应 11−3、除法本身对、无检验
    const evidenceOK = await page.getByText(/这里应是 2x = 11 − 3 = 8/).count();
    const divOK = await page.getByText(/除法本身计算正确/).count();
    const noCheck = await page.getByText(/没有代入检验步骤/).count();
    step('评分证据与诊断一致', evidenceOK > 0 && divOK > 0 && noCheck > 0,
      `移项=${evidenceOK} 除法=${divOK} 无检验=${noCheck}`);
    await page.screenshot({ path: OUT + '/student-answers.png', fullPage: true });
    step('截图-答卷页', true, OUT + '/student-answers.png');

    // 7. 预览学生反馈
    const fbBtn = page.getByRole('button', { name: '预览学生反馈' });
    if (await fbBtn.count()) {
      await fbBtn.click();
      await page.waitForTimeout(500);
      step('预览学生反馈', await page.getByText(/反馈预览/).count() > 0);
    } else {
      step('预览学生反馈', false, '按钮未找到');
    }

    // 8. 打开教材指定页
    const tbBtn = page.getByRole('button', { name: /打开教材第/ }).first();
    if (await tbBtn.count()) {
      await tbBtn.click();
      await page.waitForTimeout(1500);
      const drawer = await page.getByText('教材出处').count();
      const pageTag = await page.getByText(/第 3 页/).count();
      const canvas = await page.locator('.pdf-page-canvas-wrap canvas').count();
      const kpMatch = await page.getByText(/移项与检验/).count();
      step('教材抽屉打开指定页', drawer > 0 && pageTag > 0 && canvas > 0, `抽屉=${drawer} 页码=${pageTag} canvas=${canvas}`);
      step('教材页内容与考点对应', kpMatch > 0, `移项例题标识=${kpMatch}`);
      await page.screenshot({ path: OUT + '/textbook-drawer.png' });
      // 9. 关闭抽屉 → 回到答卷，筛选仍在
      await page.getByRole('button', { name: '关闭' }).first().click();
      await page.waitForTimeout(500);
      const stillAnswers = await page.getByText('作答详情').count();
      step('关闭教材返回作答位置', stillAnswers > 0 && page.url().includes('kpId'), page.url());
    } else {
      step('教材按钮存在', false, '未找到打开教材按钮');
    }

    // 10. 返回班级页保留筛选
    const backBtn = page.getByRole('button', { name: /返回班级学情/ });
    if (await backBtn.count()) {
      await backBtn.click();
      await page.waitForTimeout(700);
      step('返回班级页保留筛选', page.url().includes('class-analytics') && page.url().includes('kpId'), page.url());
    }

    // 11. 课标与教材页
    await page.goto(BASE + '/curriculum?source=demo', { waitUntil: 'networkidle' }).catch(()=>{});
    await page.waitForTimeout(800);
    step('课标与教材页', await page.getByText('课标要求').count() > 0);

    // 控制台错误汇总
    const realErrors = errors.filter((e) => !/favicon|Failed to load resource: 404/i.test(e));
    step('无JS运行时错误', realErrors.length === 0, realErrors.slice(0, 3).join(' || '));

  } catch (e) {
    step('验收流程异常', false, String(e).slice(0, 300));
  } finally {
    fs.writeFileSync(OUT + '/e2e-log.txt', log.join('\n'), 'utf8');
    await browser.close();
  }
})();
