/**
 * 阶段二 真实模型端到端验收：目标输入 → 读取状态 → 生成计划 → 练习反馈 → 调整建议
 *
 * 前置：后端必须在**独立数据目录**下启动（不污染正式库），例如
 *   EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_e2e_data" python -m uvicorn api.app:app --port 8000
 *   （可用 python tests/make_e2e_env.py 生成该目录）
 *
 * 用法：
 *   node tests/e2e_agent_flow.js [baseUrl] [subjectName] [goal] [modelLabel]
 * 例：
 *   node tests/e2e_agent_flow.js http://localhost:3000 离散数学 "我3天后要考这一章，每天只有30分钟，帮我复习" "DeepSeek"
 *
 * 说明：本脚本会**真实调用大模型**（模型由界面选择；MiMo 密钥当前不可用，默认用 DeepSeek）。
 * 产出截图到 tests/_screenshots/agent-*.png
 */

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { launchBrowser } = require('./e2e_launch');

const BASE = process.argv[2] || 'http://localhost:3000';
const SUBJECT_NAME = process.argv[3] || '离散数学';
const GOAL = process.argv[4] || '我3天后要考这一章，每天只有30分钟，帮我复习';
const MODEL_LABEL = process.argv[5] || 'DeepSeek';
const OUT = path.join(__dirname, '_screenshots');
// Agent 会串行调用多个工具（含模型），给足时间
const AGENT_TIMEOUT = 300000;
const USERNAME = `agent_${Date.now().toString(36)}`;

const results = [];
function record(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} | ${name}${detail ? ' | ' + detail : ''}`);
}


/** 数据隔离闸门：后端必须跑在隔离数据目录上（本脚本会在后端写入账号/课程/作答） */
async function requireBackendIsolated(base, purpose) {
  // 用 Node 的 fetch 直接查健康检查，**不要依赖 Playwright 的 page**：
  // 这个闸门要在页面创建之前调用（早期写成收 page 参数，结果 page 还没声明就被引用，
  // 6 个 E2E 脚本一启动就抛 "Cannot access 'page' before initialization"）。
  let scope = null;
  try {
    const resp = await fetch(`${base}/api/health`);
    scope = ((await resp.json())?.data || {}).data_scope || {};
  } catch (e) {
    console.log(`拒绝执行：无法读取后端健康检查（${e}）。请先启动后端。`);
    process.exit(2);
  }
  if (!scope.isolated) {
    console.log('='.repeat(64));
    console.log(`拒绝执行：${purpose}`);
    console.log(`后端当前数据目录：${scope.dir_name}（正式目录）`);
    console.log('本脚本会写入账号/课程/作答数据，不能在正式库上运行。请用隔离目录启动后端：');
    console.log('  $env:EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_work_data"');
    console.log('  python -m uvicorn api.app:app --host 127.0.0.1 --port 8000');
    console.log('='.repeat(64));
    process.exit(2);
  }
  console.log(`[数据隔离] 后端数据目录：${scope.dir_name}（隔离，已确认）`);
}

(async () => {
  await requireBackendIsolated(BASE, "Agent 全流程验收");
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));
  // 诊断：记录会话创建与聊天请求里带的 conversation_id，便于定位会话归属问题
  const convTrace = [];
  page.on('request', (r) => {
    const url = r.url();
    if (url.includes('/api/conversations') && r.method() === 'POST') {
      convTrace.push(`CREATE ${r.postData() || ''}`);
    } else if (url.includes('/api/chat/stream') || url.endsWith('/api/chat')) {
      let cid = '';
      try { cid = JSON.parse(r.postData() || '{}').conversation_id || ''; } catch { /* ignore */ }
      convTrace.push(`CHAT conversation_id=${cid}`);
    }
  });

  try {
    // 0) 前置校验：后端必须运行在隔离数据目录，避免测试账号写入正式库
    const health = await page.request.get(`${BASE}/api/health`);
    const scope = ((await health.json()).data || {}).data_scope || {};
    if (!scope.isolated) {
      record('后端运行在隔离数据目录', false,
        `data_scope=${JSON.stringify(scope)}（请用 EDU_DATA_DIR 启动后端）`);
      throw new Error('数据目录未隔离，已终止（不向正式库写入测试数据）');
    }
    record('后端运行在隔离数据目录', true, scope.dir_name || '');

    // 1) 注册
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.click('button:has-text("登录 / 注册")');
    await page.click('button:has-text("还没有账号？去注册")');
    await page.fill('#username', USERNAME);
    await page.fill('#password', 'test-pass-123');
    await page.click('.ant-modal-footer button.ant-btn-primary');
    await page.waitForSelector('.ant-modal-content', { state: 'detached', timeout: 15000 });
    record('注册并登录', true, USERNAME);

    // 1.5) 让本脚本**自足**：新建一门与 SUBJECT_NAME 同名的课程，并把当前账号加入其班级
    //
    // 修复（2026-10-03）：原脚本注册**全新账号**后，立刻要求学科下拉里存在
    // SUBJECT_NAME，而新账号默认没有任何学科 → 在空数据目录里必然等到超时失败
    // （实测：`.ant-select-item-option` 8s 超时，下拉显示"暂无学科数据"）。
    // 这里用教师白名单账号（后端 TEACHER_USERS 里需包含，默认 e2e_teacher）
    // 建课建班，再用刚注册的学生账号加入，脚本即可独立运行。
    {
      const stuToken = await page.evaluate(() => localStorage.getItem('edu_user_token') || '');
      const prep = async (method, url, body, token) => {
        const headers = { 'Content-Type': 'application/json' };
        if (token) headers['X-User-Token'] = token;
        const r = await page.request[method](`${BASE}${url}`, { headers, data: body });
        let j = null;
        try { j = await r.json(); } catch (e) { /* 非 JSON 响应 */ }
        return { status: r.status(), data: (j && j.data) || null };
      };
      const tUser = process.env.E2E_TEACHER || 'e2e_teacher';
      const tPass = process.env.E2E_TEACHER_PASSWORD || 'test-pass-123';
      await prep('post', '/api/auth/register', { username: tUser, password: tPass });
      const tLogin = await prep('post', '/api/auth/login', { username: tUser, password: tPass });
      const tToken = tLogin.data && tLogin.data.token;
      const course = tToken
        ? (await prep('post', '/api/teacher/courses',
            { title: SUBJECT_NAME, description: 'Agent 验收自备课程' }, tToken)).data
        : null;
      const klass = course
        ? (await prep('post', `/api/teacher/courses/${course.id}/classes`,
            { name: 'Agent 验收班' }, tToken)).data
        : null;

      // 关键：Agent 的 generate_note / create_plan 要求**先构建该课程的知识库**
      // （后端真实护栏："请先构建该课程的知识库"）。自足前置必须一并上传资料并建库，
      // 否则 Agent 运行会以 FAILED 结束（实测如此），后续"生成自测题"也就无从出现。
      let kb = null;
      if (course && tToken) {
        const handout = [
          '离散数学演示讲义（Agent 验收用，内容可人工核对）',
          '',
          '一、集合的基本概念',
          '集合是确定的一堆对象的整体；元素与集合之间只有"属于"与"不属于"两种关系。',
          '常用数集记号：N 自然数、Z 整数、Q 有理数、R 实数。',
          '',
          '二、集合的运算',
          '并集 A∪B、交集 A∩B、差集 A−B。德摩根律：(A∪B)^c = A^c ∩ B^c。',
          '',
          '三、本讲义专用校验码',
          'AGENT-CHECK-731  （只出现在本讲义中，用于确认资料确实进入了知识库）',
        ].join('\n');
        await page.request.post(
          `${BASE}/api/teacher/courses/${course.id}/materials?visibility=students`,
          { headers: { 'X-User-Token': tToken },
            multipart: { file: { name: 'agent_demo_handout.txt',
                                 mimeType: 'text/plain',
                                 buffer: Buffer.from(handout, 'utf-8') } } });
        const rb = await prep('post', `/api/teacher/courses/${course.id}/knowledge/rebuild`, null, tToken);
        kb = rb.status === 200;
      }

      let joined = false;
      if (klass && klass.join_code && stuToken) {
        joined = (await prep('post', '/api/student/courses/join',
          { code: klass.join_code }, stuToken)).status === 200;
      }
      record('准备可选的学科（脚本自足）', !!(course && klass && joined && kb),
        `teacher=${tUser} course=${SUBJECT_NAME} join_code=${klass ? klass.join_code : '-'} `
        + `joined=${joined} knowledge_built=${kb}`);
      await page.reload({ waitUntil: 'networkidle' });
      await page.waitForTimeout(1500);
    }

    // 2) 学生页选择学科（验证学生入口可用）
    await page.click('.ant-select-selector');
    await page.waitForSelector(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
      { timeout: 8000 });
    await page.locator(
      `.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option:has-text("${SUBJECT_NAME}")`)
      .first().click();
    await page.waitForTimeout(1200);
    record('学生页选择学科', true, SUBJECT_NAME);

    // 3) 进入学习助手页，并在**本页**再选择一次学科
    //    （学科选择是各页面独立状态；这里按真实用户操作在聊天页选一次）
    await page.click('.ant-menu-item:has-text("AI学习助手")');
    await page.waitForTimeout(1200);
    // 聊天页的第一个下拉就是侧栏的学科选择器（模型下拉在其后）
    const chatSubject = page.locator('.ant-select').first();
    await chatSubject.locator('.ant-select-selector').click();
    await page.waitForSelector(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
      { timeout: 8000 });
    await page.locator(
      `.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option:has-text("${SUBJECT_NAME}")`)
      .first().click();
    await page.waitForTimeout(1200);
    const subjectShown = await page.locator('text=当前学科').first().innerText();
    record('学习助手页选择学科', subjectShown.includes(SUBJECT_NAME), subjectShown);

    await page.locator('button:has-text("学习助手"), .ant-tag:has-text("学习助手")').first().click();
    await page.waitForTimeout(400);
    const ph = await page.locator('textarea:visible').first().getAttribute('placeholder');
    record('切换到学习助手模式', !!ph && ph.includes('目标'), ph || '未获取到 placeholder');

    // 4) 模型：默认即为后端默认文本模型（DeepSeek）；如需其他模型则手动切换
    const modelSelect = page.locator('.ant-select')
      .filter({ has: page.locator('.ant-select-selection-item') }).last();
    const currentLabel = (await modelSelect.innerText().catch(() => '')).trim();
    if (currentLabel.includes(MODEL_LABEL)) {
      // 未手动选择就已是目标模型 → 说明默认值已正确切换
      record('模型默认即为目标模型（无需手动选择）', true, currentLabel);
    } else {
      await modelSelect.locator('.ant-select-selector').click();
      await page.waitForSelector(
        '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
        { timeout: 8000 });
      await page.locator(
        `.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option:has-text("${MODEL_LABEL}")`)
        .first().click();
      record('切换模型', true, `${currentLabel} → ${MODEL_LABEL}`);
    }

    // 5) 输入目标并执行（真实模型 + 真实学习记录）
    await page.locator('textarea:visible').last().fill(GOAL);
    await page.keyboard.press('Enter');
    record('已提交目标', true, GOAL);

    // 6) 等待执行进度出现
    await page.waitForSelector('text=学习助手执行过程', { timeout: 30000 });
    record('展示执行进度（可看到正在做什么）', true);
    await page.screenshot({ path: path.join(OUT, 'agent-01-progress.png'), fullPage: true });

    // 7) 等待运行结束（出现状态标签）
    const statusTag = page.locator('.ant-tag:has-text("SUCCEEDED"), .ant-tag:has-text("FAILED")').first();
    await statusTag.waitFor({ state: 'visible', timeout: AGENT_TIMEOUT });
    const status = await statusTag.innerText();
    record('运行结束并展示最终状态', status.includes('SUCCEEDED'), status);

    // 抓取执行步骤摘要（用于核对"读取状态/检查预算/安排复习"等是否真的执行）
    // 执行步骤渲染在消息气泡里：取包含"学习助手执行过程"的那条消息
    const stepsText = await page.locator('.ant-list-item:has-text("学习助手执行过程")')
      .last().innerText().catch(() => '');
    const stepsAll = await page.locator('body').innerText();
    record('包含"查看学习记录"步骤', stepsAll.includes('查看学习记录'),
      stepsText.split('\n').slice(0, 3).join(' / '));
    record('包含"检查时间预算"步骤', stepsAll.includes('检查时间预算'));
    record('展示每步结果摘要（可核对依据）',
      /预算充足|存在时间冲突/.test(stepsAll) && /生成资料|生成计划/.test(stepsAll));
    await page.screenshot({ path: path.join(OUT, 'agent-02-done.png'), fullPage: true });

    // 8) 计划与时间：约束已按目标写入，预算由程序判定
    await page.click('.ant-menu-item:has-text("我的学习")');
    await page.waitForTimeout(1200);
    // 学生页的学科选择是页面级状态，返回后需重新选择
    const stuSubject = page.locator('.ant-select').first();
    if (await stuSubject.count()) {
      const shown = await page.locator('body').innerText();
      if (!shown.includes(`当前学科`) && !/(离散数学|大学物理)/.test(
            await stuSubject.innerText().catch(() => ''))) {
        await stuSubject.locator('.ant-select-selector').click();
        await page.waitForSelector(
          '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
          { timeout: 8000 });
        await page.locator(
          `.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option:has-text("${SUBJECT_NAME}")`)
          .first().click();
        await page.waitForTimeout(1200);
      }
    }
    await page.click('.ant-tabs-tab:has-text("计划与时间")');
    await page.waitForSelector('text=时间预算检查（由后端程序判定）', { timeout: 15000 });
    const planText = await page.locator('.ant-tabs-tabpane-active').innerText();
    record('目标里的每天可用时长已落到约束', /每天可用[\s\S]{0,40}30/.test(planText)
      || planText.includes('30'), planText.split('\n').slice(0, 12).join(' / '));
    record('预算检查展示冲突或结论', /时间预算内|存在时间冲突/.test(planText));
    record('约束由程序判定并展示结论或建议',
      /建议方案|不会直接改你的计划|当前安排在你的时间预算内/.test(planText));
    await page.screenshot({ path: path.join(OUT, 'agent-03-plan.png'), fullPage: true });

    // 9) 练习反馈：真实生成的自测题 → 作答 → 后端判分
    await page.click('.ant-menu-item:has-text("AI学习助手")');
    await page.waitForTimeout(1200);
    // 聊天页学科是独立状态，需要重新选择
    const chatSubject2 = page.locator('.ant-select').first();
    if (await chatSubject2.count()) {
      await chatSubject2.locator('.ant-select-selector').click();
      await page.waitForSelector(
        '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
        { timeout: 8000 });
      await page.locator(
        `.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option:has-text("${SUBJECT_NAME}")`)
        .first().click();
      await page.waitForTimeout(1000);
    }
    // 模型选择已持久化；若当前显示的不是目标模型（例如换了浏览器），这里再选一次
    const modelAgain = page.locator('.ant-select')
      .filter({ has: page.locator('.ant-select-selection-item') }).last();
    const againLabel = (await modelAgain.innerText().catch(() => '')).trim();
    if (await modelAgain.count() && !againLabel.includes(MODEL_LABEL)) {
      await modelAgain.locator('.ant-select-selector').click();
      await page.waitForSelector(
        '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
        { timeout: 8000 });
      await page.locator(
        `.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option:has-text("${MODEL_LABEL}")`)
        .first().click();
      await page.waitForTimeout(300);
    }
    await page.locator('button:has-text("自测"), .ant-tag:has-text("自测")').first().click();
    await page.waitForTimeout(300);
    await page.locator('textarea:visible').last().fill(SUBJECT_NAME === '离散数学' ? '集合的基本概念' : GOAL);
    await page.keyboard.press('Enter');
    const submitBtn = page.locator('button:has-text("提交作答")').first();
    await submitBtn.waitFor({ state: 'visible', timeout: AGENT_TIMEOUT });
    record('真实模型生成自测题', true);
    await page.locator('.ant-radio-wrapper').first().click();
    await submitBtn.click();
    const resultAlert = page.locator('.ant-alert').filter({ hasText: /你的作答：/ }).first();
    await resultAlert.waitFor({ state: 'visible', timeout: 30000 });
    const graded = (await resultAlert.innerText()).replace(/\n/g, ' / ');
    record('后端权威判分并返回结果', /回答正确|回答错误|待自评/.test(graded), graded.slice(0, 100));

    // 10) 学习记忆：系统按真实记录给出带依据的暂时判断
    await page.click('.ant-menu-item:has-text("我的学习")');
    await page.waitForTimeout(1500);
    await page.click('.ant-tabs-tab:has-text("学习记忆")');
    await page.waitForSelector('text=学习记忆（个性化依据）', { timeout: 15000 });
    const memText = await page.locator('.ant-tabs-tabpane-active').innerText();
    record('记忆页区分三类信息', /你的明确设置|系统推断|课程资料确认/.test(memText));
    await page.screenshot({ path: path.join(OUT, 'agent-04-memory.png'), fullPage: true });

    // 11) 无页面级异常
    record('无页面级 JS 异常', pageErrors.length === 0, pageErrors.slice(0, 3).join(' ; '));
  } catch (e) {
    record('脚本执行', false, String(e && e.message ? e.message : e));
    await page.screenshot({ path: path.join(OUT, 'agent-error.png'), fullPage: true }).catch(() => {});
  } finally {
    await browser.close();
  }

  if (convTrace.length) {
    console.log('\n[会话/聊天请求轨迹]');
    convTrace.slice(-8).forEach((l) => console.log('  ' + l));
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`);
  console.log(`截图目录：${OUT}`);
  process.exit(failed.length ? 1 : 0);
})();
