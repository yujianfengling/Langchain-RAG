/**
 * 首次使用体验走查（真实浏览器：教师 + 2 名学生视角）
 *
 * 目的：在组织真实小班试用之前，以"第一次打开这个系统的老师/学生"的视角
 * 把完整教学闭环自己走一遍。重点不是"功能存不存在"，而是**第一次用的人能不能走通**：
 *   - 每步是否有清楚的文案 / 空状态 / 加载态 / 错误提示
 *   - 常见笔记本（1366×768）与手机（390×844）下布局是否可用、有无横向溢出
 *   - 两种"邀请码"（教师邀请码 T-XXXXXXXX vs 班级 6 位邀请码）会不会混淆
 *   - 页面有无 JS 异常、有无被忽略的 4xx/5xx
 *
 * 用法：
 *   node tests/e2e_first_use_walkthrough.js [frontendBase] [apiBase]
 *
 * 前置：
 *   1) 后端跑在**隔离数据目录**（脚本先自检，不满足直接退出码 2）
 *   2) 后端配置 ADMIN_USERS=<ADMIN_NAME>（用它签发一个教师邀请码）
 *   3) 前端 dev server；后端需能访问 DeepSeek（作业出题要用）
 *
 * 输出：逐条 PASS/FAIL/OBSERVE + tests/_screenshots/first-use-*.png
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const { chromium } = require('playwright');
const { launchBrowser } = require('./e2e_launch');

const BASE = process.argv[2] || 'http://localhost:3000';
const API = process.argv[3] || 'http://127.0.0.1:8000';
const OUT = path.join(__dirname, '_screenshots');
const SUFFIX = Date.now().toString(36).slice(-4);
const ADMIN_NAME = 'walk_admin';
const PW = 'test-pass-123';

const results = [];
const observations = [];

function record(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} | ${name}${detail ? ' | ' + detail : ''}`);
}
/** 体验问题：记录下来但不算失败（除非它真的阻断了流程） */
function observe(name, detail) {
  observations.push({ name, detail });
  console.log(`OBSERVE | ${name} | ${detail}`);
}

/** 现场记录（写给人工复核用：内容原文比 PASS/FAIL 更能说明"看到了什么"） */
function note(name, detail) {
  observations.push({ name, detail });
  console.log(`  · ${name}：${detail}`);
}

/** 数据隔离闸门：后端必须跑在隔离数据目录上 */
async function requireBackendIsolated() {
  let scope;
  try {
    const r = await fetch(`${API}/api/health`);
    scope = (await r.json())?.data?.data_scope || {};
  } catch (e) {
    console.log(`拒绝执行：无法读取后端健康检查（${e.message}）。请先启动后端。`);
    process.exit(2);
  }
  if (!scope.isolated) {
    console.log('='.repeat(64));
    console.log('拒绝执行：首次使用体验走查（会写入账号/课程/作答数据）');
    console.log(`后端当前数据目录：${scope.dir_name}（正式目录）`);
    console.log('请用隔离目录启动后端：');
    console.log('  $env:EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_work_data"');
    console.log(`  $env:ADMIN_USERS="${ADMIN_NAME}"`);
    console.log('  python -m uvicorn api.app:app --host 127.0.0.1 --port 8000');
    console.log('='.repeat(64));
    process.exit(2);
  }
  console.log(`[数据隔离] 后端数据目录：${scope.dir_name}（隔离，已确认）`);
}

(async () => {
  await requireBackendIsolated();
  fs.mkdirSync(OUT, { recursive: true });
  // 浏览器：优先用系统 Chrome（与老师真实使用环境最接近）；没有就回退到
  // playwright 自带的 chromium——否则没装 Chrome 的机器连走查都跑不起来，
  // 而"能不能在自己机器上验收"本身也是交付能力的一部分。
  let browser;
  try {
    browser = await launchBrowser({ headless: true });
  } catch (e) {
    console.log('[提示] 未找到系统 Chrome，回退到 playwright 自带 chromium');
    browser = await chromium.launch({ headless: true });
  }
  const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  const page = await context.newPage();

  const pageErrors = [];
  const failedRequests = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));
  page.on('response', (r) => {
    // 只记录"没人处理"的失败：被界面捕获并提示的不算静默失败
    if (r.status() >= 400 && r.url().includes('/api/')) {
      failedRequests.push(`${r.status()} ${r.request().method()} ${r.url().split('/api/')[1]}`);
    }
  });

  // 采集 AI 出题的请求体与响应码（用于定位"接口契约"类问题，而不是只看界面文案）
  const genBodies = [];
  const genStatuses = [];
  const pubStatuses = [];
  page.on('request', (r) => {
    if (r.url().includes('generate-questions')) genBodies.push(r.postData() || '');
  });
  page.on('response', (r) => {
    if (r.url().includes('generate-questions')) genStatuses.push(r.status());
    if (/\/assignments\/[^/]+\/publish/.test(r.url())) pubStatuses.push(r.status());
  });

  const shot = (n) => page.screenshot({ path: path.join(OUT, `first-use-${n}.png`), fullPage: true });
  const text = () => page.evaluate(() => document.body.innerText);

  /** 等待正文出现某段文字（用于判断界面是否给出反馈） */
  const waitText = async (re, timeout = 20000) => {
    try {
      await page.waitForFunction(
        (src) => new RegExp(src).test(document.body.innerText), re.source,
        { timeout });
      return true;
    } catch { return false; }
  };

  /** 窄屏横向溢出检测 */
  const overflow = () => page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
  }));

  // ---------- 界面操作小工具 ----------
  const openAuth = async () => {
    await btn("登录 / 注册").first().click();
    await page.waitForSelector('.ant-modal-content', { timeout: 10000 });
  };
  const doRegister = async (username) => {
    await openAuth();
    await btn("还没有账号？去注册").first().click();
    await page.fill('#username', username);
    await page.fill('#password', PW);
    await page.locator('.ant-modal-footer button.ant-btn-primary').first().click();
    await page.waitForSelector('.ant-modal-content', { state: 'detached', timeout: 20000 });
  };
  /** 注册；若用户名已存在（脚本可重跑）则改为登录 */
  const ensureAccount = async (username) => {
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await openAuth();
    await page.click('button:has-text("还没有账号？去注册")');
    await page.fill('#username', username);
    await page.fill('#password', PW);
    await page.click('.ant-modal-footer button.ant-btn-primary');
    await page.waitForTimeout(3000);
    if (!(await page.locator('.ant-modal-content').count())) return 'registered';
    const t = await text();
    if (!/已存在/.test(t)) throw new Error(`注册未完成：${t.slice(0, 120)}`);
    await btn("已有账号？去登录").first().click();
    await page.fill('#username', username);
    await page.fill('#password', PW);
    await page.click('.ant-modal-footer button.ant-btn-primary');
    await page.waitForSelector('.ant-modal-content', { state: 'detached', timeout: 20000 });
    return 'logged-in';
  };
  const doLogout = async () => {
    const out = btn("退出登录");
    if (await out.count()) { await out.first().click(); await page.waitForTimeout(1500); }
  };
  const doLogin = async (username) => {
    await openAuth();
    await page.fill('#username', username);
    await page.fill('#password', PW);
    await page.click('.ant-modal-footer button.ant-btn-primary');
    await page.waitForSelector('.ant-modal-content', { state: 'detached', timeout: 20000 });
  };
  const gotoMenu = async (label) => {
    await page.locator(`.ant-menu-item:has-text("${label}")`).first().click();
    await page.waitForTimeout(1200);
  };
  const gotoTeacherPanel = async (label) => {
    await page.locator(`.ant-menu-item:has-text("${label}")`).last().click();
    await page.waitForTimeout(1500);
  };
  const dialog = (titleRe) => page.locator('.ant-modal-content').filter({ hasText: titleRe }).last();
  /**
   * antd 会在"恰好两个汉字"的按钮里插入空格（"创建" → "创 建"），
   * 直接用 has-text("创建") 会匹配不到。这里按字符插入 \s* 再匹配。
   */
  const btnRe = (label) => new RegExp(
    label.split('').map((c) => c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s*'));
  const btn = (label, scope) => (scope || page).getByRole('button', { name: btnRe(label) });

  // ================================================================
  // 1) 管理员：在界面里签发一个教师邀请码
  //    （管理员是后台角色，不参与试用；这里用它拿到"老师收到的那个码"）
  // ================================================================
  try {
    const how = await ensureAccount(ADMIN_NAME);
    record('管理员账号可通过界面登录/注册', true, how);
    await gotoMenu('设置');
    await waitText(/教师身份/, 10000);
    // 直接"等这段文案出现"，而不是"取一次全文再正则匹配"——后者会因渲染时机误报：
    // 实测按钮确实存在、也点得动，但一次性取到的 innerText 里还没有它，于是验收假红。
    const hasIssueEntry = await waitText(/生成一个教师邀请码/, 10000);
    const card = await text();
    record('管理员在「设置 → 教师身份」看到签发入口',
      hasIssueEntry, (card.match(/一次性[^\n]*/) || [''])[0].slice(0, 60));

    const issueBtn = btn("生成一个教师邀请码").first();
    // policy 是异步拉取的，而按钮 disabled={!policy?.can_issue}——
    // 在 policy 到达前 isEnabled() 必然返回 false，会误报"权限被收紧"。
    // 所以轮询等它变为可用，而不是取一次快照。
    let enabled = false;
    for (let i = 0; i < 20; i += 1) {
      enabled = await issueBtn.isEnabled();
      if (enabled) break;
      await page.waitForTimeout(300);
    }
    record('管理员签发按钮可用（未被权限收紧误伤）', enabled);
    await issueBtn.click();
    const okAlert = await waitText(/教师邀请码：/, 20000);
    record('界面能生成教师邀请码并显示出来', okAlert);
    const tText = await text();
    const m = tText.match(/教师邀请码：\s*(T-[A-Z0-9]+)/);
    global.TEACHER_INVITE = m ? m[1] : null;
    record('拿到教师邀请码（T- 前缀）', !!global.TEACHER_INVITE, global.TEACHER_INVITE || '未捕获');
    // 签发后应能看到台账与撤销入口（列表是签发后再查一次的，稍等）
    const ledgerOk = await waitText(/未使用的教师邀请码/, 15000);
    const ledgerText = await text();
    record('签发后出现邀请码台账（含撤销入口）',
      ledgerOk && /撤\s*销/.test(ledgerText),
      (ledgerText.match(/未使用的教师邀请码[^\n]*/) || [''])[0]);
    await shot('01-admin-settings');
    await doLogout();
  } catch (e) {
    record('【第 1 步】管理员签发教师邀请码', false, String(e.message || e));
    await shot('01-admin-error').catch(() => {});
  }

  // ================================================================
  // 2) 首次使用的老师：注册 → 凭码开通 → 建课 → 建班 → 上传资料
  // ================================================================
  const TEACHER = `walk_teacher_${SUFFIX}`;
  const STUDENT_A = `walk_stu_a_${SUFFIX}`;
  const STUDENT_B = `walk_stu_b_${SUFFIX}`;
  let CLASS_CODE = null;
  let COURSE_TITLE = `数据结构与算法（走查 ${SUFFIX}）`;

  try {
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await doRegister(TEACHER);
    await gotoMenu('设置');
    await waitText(/教师身份/, 10000);

    // 2.1 学生身份下的提示是否说清楚了"找谁要码"
    const beforeText = await text();
    record('未开通教师时，设置页说明了开通路径与两种邀请码的区别',
      /创建课程需要教师身份/.test(beforeText)
      && /没有教师邀请码？/.test(beforeText)
      && /班级邀请码/.test(beforeText),
      (/注意：教师邀请码[^\n]*/.test(beforeText) ? '有区别说明' : '缺"与班级邀请码不同"的说明'));
    // 未开通时侧边栏不应出现教师工作台
    const menuItems = await page.locator('.ant-menu-item').allInnerTexts();
    record('学生身份看不到「教师工作台」入口',
      !menuItems.some((t) => t.includes('教师工作台')), menuItems.join('/'));
    await shot('02a-teacher-before-activation');

    // 2.2 凭码开通
    await page.fill('input[placeholder="填写教师邀请码（形如 T-XXXXXXXX）"]', global.TEACHER_INVITE || '');
    await btn("开通教师身份").first().click();
    const activated = await waitText(/你已具备教师身份|教师身份已开通/, 20000);
    record('凭教师邀请码在界面开通教师身份', activated,
      (await text()).match(/教师身份已开通[^\n]*/)?.[0] || '');
    await page.waitForTimeout(2500);   // 等 /auth/me 回来后菜单刷新
    const menuAfter = await page.locator('.ant-menu-item').allInnerTexts();
    record('开通后侧边栏立刻出现「教师工作台」',
      menuAfter.some((t) => t.includes('教师工作台')), menuAfter.join('/'));

    // 2.3 建课
    await gotoMenu('教师工作台');
    await page.waitForTimeout(1500);
    const noCourse = await text();
    record('教师工作台在无课程时给出空状态与「新建课程」指引',
      /你还没有课程|请先选择或创建一门课程|还没有课程/.test(noCourse),
      (noCourse.match(/你还没有课程[^\n]*/) || [''])[0]);
    await shot('02b-teacher-empty');

    await btn("新建课程").first().click();
    const cm = dialog(/新建课程/);
    await cm.locator('input#title, input').first().fill(COURSE_TITLE);
    await btn("创建", cm).last().click();
    const created = await waitText(/课程《.*》已创建|已创建/, 30000);
    record('教师新建课程成功（自动跳到班级管理）', created);
    await page.waitForTimeout(2500);

    // 2.4 建班（拿班级邀请码）
    const classPanel = await text();
    record('新建课程后自动进入「班级管理」，并给出建班指引',
      /班级管理/.test(classPanel) && /新建班级/.test(classPanel));
    await btn("新建班级").first().click();
    const bm = dialog(/新建班级/);
    await bm.locator('input').first().fill(`走查班 ${SUFFIX}`);
    await btn("创建", bm).last().click();
    const classOk = await waitText(/班级已创建/, 20000);
    const classText = await text();
    const cmatch = classText.match(/班级邀请码\s*([A-Z0-9]{4,10})/) || classText.match(/，班级邀请码\s*([A-Z0-9]+)/);
    CLASS_CODE = cmatch ? cmatch[1] : null;
    record('建班成功并在界面上直接看到「班级邀请码」', classOk && !!CLASS_CODE,
      CLASS_CODE ? `班级邀请码=${CLASS_CODE}` : '未从界面读到邀请码');
    record('班级邀请码文案已与学生入班场景一致（都叫"班级邀请码"）',
      /班级邀请码/.test(classText));
    await shot('02c-teacher-class');

    // 2.5 上传课程资料（真实文件，供出题与检索使用）
    await gotoTeacherPanel('课程资料');
    await page.waitForTimeout(1200);
    const matEmpty = await text();
    record('课程资料为空时给出"先上传资料"的说明',
      /该课程还没有资料/.test(matEmpty),
      (matEmpty.match(/该课程还没有资料[^\n]*/) || [''])[0]);
    const doc = path.join(os.tmpdir(), `walk_material_${SUFFIX}.md`);
    fs.writeFileSync(doc, `# 二叉树\n\n## 定义\n二叉树是每个结点最多有两个子树的树结构。\n\n`
      + `## 三种遍历\n- 前序遍历：根 → 左 → 右\n- 中序遍历：左 → 根 → 右\n- 后序遍历：左 → 右 → 根\n\n`
      + `## 二叉搜索树\n左子树所有结点小于根，右子树所有结点大于根；查找、插入、删除平均 O(log n)。\n`,
      'utf8');
    await page.locator('.ant-upload input[type="file"]').first()
      .setInputFiles(doc).catch(async () => {
        await page.locator('input[type="file"]').first().setInputFiles(doc);
      });
    const uploaded = await waitText(/已就绪|处理失败/, 180000);
    // 只看表格「处理状态」列，避免把空状态提示里的"处理失败"字样误判成失败
    const matRow = page.locator('.ant-table-tbody tr.ant-table-row').first();
    await matRow.waitFor({ timeout: 30000 }).catch(() => {});
    let rowStatus = '';
    for (let i = 0; i < 20 && !rowStatus; i += 1) {
      const cells = await matRow.locator('td').allInnerTexts().catch(() => []);
      rowStatus = cells.map((t) => t.trim())
        .find((t) => /已就绪|处理中|待处理|处理失败/.test(t)) || '';
      if (!rowStatus) await page.waitForTimeout(1000);
    }
    record('上传课程资料（Markdown）并进入处理流程', uploaded, `处理状态=${rowStatus}`);
    record('上传的资料达到「已就绪」', /已就绪/.test(rowStatus), rowStatus || '未读到状态列');
    // 上传后应自动构建知识库；否则 AI 检索为空、出题报「课程资料为空」（本轮实测到的顺序陷阱）
    const kbOk = await waitText(/课程知识库可用（已收录 [1-9]/, 180000);
    const matText = await text();
    record('上传后课程知识库自动可用（无需老师手动重建）', kbOk,
      (matText.match(/课程知识库[^\n]*/) || [''])[0]);
    await shot('02d-teacher-materials');

    // 2.6 教案：手写草稿 → 发布
    await gotoTeacherPanel('教案');
    await page.waitForTimeout(1200);
    const planEmpty = await text();
    record('教案为空时给出「生成教案 / 手写草稿」两种入口',
      /还没有教案/.test(planEmpty) && /手写草稿/.test(planEmpty));
    await btn("手写草稿").first().click();
    const pm = dialog(/新建手写教案草稿/);
    await pm.locator('input').first().fill(`第 1 讲 二叉树（走查 ${SUFFIX}）`);
    await btn("创建", pm).last().click();
    const planCreated = await waitText(/教案|草稿/, 20000);
    await page.waitForTimeout(2000);
    record('手写教案草稿创建成功并进入编辑器', planCreated);
    await shot('02e-teacher-plan');
  } catch (e) {
    record('【第 2 步】教师建课建班/资料/教案', false, String(e.message || e));
    await shot('02-error').catch(() => {});
  }

  // ================================================================
  // 3) 教师：发布作业（AI 出题 → 发布）
  // ================================================================
  let ASSIGNMENT_TITLE = `第 1 次练习：二叉树（${SUFFIX}）`;
  try {
    await gotoTeacherPanel('作业与练习');
    await page.waitForTimeout(1200);
    await btn("新建作业").first().click();
    const am = dialog(/新建作业草稿|编辑作业/);
    // 所属班级
    await am.locator('.ant-select-selector').first().click();
    await page.waitForSelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
      { timeout: 8000 });
    await page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option').first().click();
    await am.locator('input#title').fill(ASSIGNMENT_TITLE);
    await btn("创建草稿", am).first().click();
    const draftOk = await waitText(/草稿/, 25000);
    record('教师新建作业草稿', draftOk);
    await page.waitForTimeout(2000);

    // AI 出题：**必须先选择挂到草稿作业**，否则题目只预览、不落库（本轮实测到的陷阱）
    await btn("AI 出题").first().click();
    const gm = dialog(/AI 依据课程资料出题/);
    const draftSel = gm.locator('.ant-select-selector').first();
    await draftSel.click();
    await page.waitForSelector(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
      { timeout: 8000 });
    await page.locator(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option').first().click();
    await page.waitForTimeout(600);
    await btn("开始出题", gm).first().click();
    const genStatus = await (async () => {
      for (let i = 0; i < 180; i += 1) {
        if (genStatuses.length) return genStatuses[genStatuses.length - 1];
        await page.waitForTimeout(1000);
      }
      return null;
    })();
    await page.waitForTimeout(4000);
    const genPayload = genBodies[genBodies.length - 1] || '(未捕获请求体)';
    const gmText = await gm.innerText().catch(() => '');
    record('AI 依据课程资料出题并写入草稿作业', genStatus === 200 && /生成结果/.test(gmText),
      `请求=${genPayload} 响应=${genStatus} ${(gmText.match(/生成结果[^\n]*/) || [''])[0]}`);
    await shot('03a-teacher-assignment');
    // AI 出题模态没有底部按钮，用 Esc 关闭，否则会挡住后续操作
    await page.keyboard.press('Escape');
    await page.waitForTimeout(1500);
    // 出题后作业应真的有题目（否则发布必然失败）
    const asgRow = await page.locator('.ant-table-tbody tr.ant-table-row').first().locator('td').allInnerTexts()
      .catch(() => []);
    const qCount = Number((asgRow.find((t) => /^\d+$/.test(t.trim())) || '0').trim());
    record('草稿作业里确实有题目（题目数 > 0）', qCount > 0, `题目数=${qCount} 行=${JSON.stringify(asgRow.map((t) => t.trim()).filter(Boolean))}`);

    // 发布
    const pubBtn = btn("发布").first();
    if (await pubBtn.count()) {
      await pubBtn.click();
      const pc = page.locator('.ant-popover:visible, .ant-modal-content').filter({ hasText: /发布/ }).last();
      const okBtn = btn("发布", pc).last();
      if (await okBtn.count()) await okBtn.click();
      else await btn("发布").last().click();
      // 用接口结果判断，而不是"页面出现了『已发布』字样"（面板里本来就有这类说明文字）
      let pubStatus = null;
      for (let i = 0; i < 30; i += 1) {
        if (pubStatuses.length) { pubStatus = pubStatuses[pubStatuses.length - 1]; break; }
        await page.waitForTimeout(1000);
      }
      await page.waitForTimeout(2500);
      const rowAfterPub = await page.locator('.ant-table-tbody tr.ant-table-row').first()
        .locator('td').allInnerTexts().catch(() => []);
      record('发布作业给学生', pubStatus === 200 && rowAfterPub.some((t) => /已发布/.test(t)),
        `接口=${pubStatus} 行=${JSON.stringify(rowAfterPub.map((t) => t.trim()).filter(Boolean))}`);
    } else {
      record('发布作业给学生', false, '未找到发布按钮');
    }
    await shot('03b-teacher-published');
  } catch (e) {
    record('【第 3 步】教师建作业并发布', false, String(e.message || e));
    await shot('03-error').catch(() => {});
  }

  // ================================================================
  // 4) 两名学生：入班 → 作答 → 错题本 / 复习
  // ================================================================
  /** 关掉页面上所有可见弹窗。
   *
   *  为什么需要：两个学生共用同一个 page，如果上一个学生的作答弹窗没关掉，
   *  下一个学生进来时页面上就有**两层遮罩**——后面的点击会被 .ant-modal-wrap
   *  拦截（实测学生B 因此 30 秒超时）。所以每个学生流程开始前先清场。
   */
  const closeAllModals = async () => {
    for (let i = 0; i < 5; i += 1) {
      const wraps = page.locator('.ant-modal-wrap:visible');
      if ((await wraps.count()) === 0) return;
      const closeBtn = page.locator('.ant-modal-content:visible')
        .getByRole('button', { name: /关\s*闭|取\s*消|知\s*道\s*了/ }).last();
      if (await closeBtn.count()) {
        // force: 正是为了绕过"被另一层遮罩拦截"的检查
        await closeBtn.click({ timeout: 3000, force: true }).catch(() => {});
      } else {
        await page.keyboard.press('Escape').catch(() => {});
      }
      await page.waitForTimeout(400);
    }
  };

  const studentRun = async (name, label) => {
    await closeAllModals();
    try {
      await doLogout();
      await page.goto(BASE, { waitUntil: 'networkidle' });
      await doRegister(name);
      // 确认真的进入登录态，否则后面每一步都会以"元素找不到"的形式连环失败
      if (!(await page.locator('button:has-text("退出登录")').count())) {
        throw new Error('注册后未进入登录态');
      }
      await gotoMenu('我的学习');
      await page.waitForTimeout(2500);
      // 首次进入默认停在「今日任务」，确认那里给了去「我的课程」的入口
      const firstText = await text();
      record(`[${label}] 首次进入（今日任务为空）时能一眼找到加入课程的入口`,
        /去「我的课程」用班级邀请码加入|我的课程/.test(firstText));
      await page.locator('.ant-tabs-tab:has-text("我的课程")').first().click();
      await page.waitForTimeout(2500);
      const joinBox = page.locator('input[placeholder*="班级邀请码"]');
      const hasJoin = await joinBox.count();
      record(`[${label}] 「我的课程」提供了用班级邀请码入班的输入框`, hasJoin > 0);
      const emptyHint = await text();
      record(`[${label}] 未加入课程时有空状态指引`,
        /还没有加入任何课程|向老师索取班级邀请码/.test(emptyHint),
        (emptyHint.match(/还没有加入任何课程.*/) || [''])[0].slice(0, 60));

      await joinBox.first().fill(CLASS_CODE || '');
      await btn("加入课程").first().click();
      const joined = await waitText(/已加入|已经在.*班/, 25000);
      record(`[${label}] 用班级邀请码入班成功`, joined,
        ((await text()).match(/已加入[^\n]*/) || [''])[0]);

      // 作业出现
      await page.waitForTimeout(2500);
      const listText = await text();
      const hasHomework = /老师布置的作业/.test(listText);
      record(`[${label}] 入班后能看到老师布置的作业`, hasHomework,
        (listText.match(/老师布置的作业[^\n]*/) || [''])[0]);
      if (!hasHomework) return;

      // 进入作答：每题选第一个选项并提交
      await btn("进入作答").first().click();
      await page.waitForSelector('.ant-modal-content', { timeout: 15000 });
      await page.waitForTimeout(1500);
      const judgeTip = await text();
      record(`[${label}] 作答弹窗说明"由后端判分、交卷前不显示答案"`,
        /作答由后端判分/.test(judgeTip));
      let answered = 0;
      // 作答弹窗里每道题是一个 Card：未作答的题有「提交本题」按钮 + 可选选项；
      // 已作答的题按钮会被判分结果 Alert 取代、选项变 disabled。
      for (let i = 0; i < 10; i += 1) {
        const answerModal = page.locator('.ant-modal-content')
          .filter({ hasText: '作答由后端判分' }).last();
        const submit = btn('提交本题', answerModal).first();
        if (!(await submit.count())) break;
        // 找到第一道还没作答的题（其选项未被禁用）
        const groups = answerModal.locator('.ant-radio-group');
        const gCount = await groups.count();
        let target = null;
        let targetCount = 0;
        for (let g = 0; g < gCount; g += 1) {
          const grp = groups.nth(g);
          const enabled = await grp.locator('input[type=radio]').first().isEnabled()
            .catch(() => false);
          if (enabled) {
            target = grp;
            targetCount = await grp.locator('.ant-radio-wrapper').count();
            break;
          }
        }
        if (!target || !targetCount) break;
        // 第 1 题选 A（多半正确），其余故意选靠后的选项，制造错题供错题本/复习验证
        const idx = i === 0 ? 0 : Math.min(targetCount - 1, 1 + (i % 2));
        await target.locator('.ant-radio-wrapper').nth(idx).click();
        await submit.click();
        const judged = await waitText(/回答正确|回答错误/, 25000);
        if (!judged) break;
        answered += 1;
        await page.waitForTimeout(1200);
      }
      record(`[${label}] 能逐题提交并立即看到判分结果`, answered > 0, `已作答 ${answered} 题`);
      await shot(`04-${label}-answer`);

      // 标记完成
      const doneBtn = btn('标记完成', page.locator('.ant-modal-content')).first();
      if (await doneBtn.count()) {
        await doneBtn.click();
        await waitText(/已标记完成|完成/, 20000);
      }
      // 关闭作答弹窗。交给 closeAllModals 统一处理：
      // "标记完成"会弹出确认框，与作答弹窗叠在一起，直接点"关闭"会被上层
      // 遮罩拦截（实测 locator.click 30 秒超时，而作答其实已经全部完成）。
      // closeAllModals 用 force + Esc 逐层收，稳定得多。
      await closeAllModals();
      await page.waitForTimeout(2000);

      // 错题本 / 复习 / 掌握度：只看当前标签页的内容区，避免把标签名本身当成内容
      const tabPane = () => page.locator('.ant-tabs-tabpane-active').last();
      const openTab = async (name) => {
        const tab = page.locator(`.ant-tabs-tab:has-text("${name}")`).first();
        if (!(await tab.count())) return false;
        await tab.click();
        await page.waitForTimeout(2500);
        return true;
      };

      if (await openTab('错题本')) {
        const wt = await tabPane().innerText().catch(() => '');
        const hasWrong = !/暂无错题/.test(wt);
        record(`[${label}] 错题本能反映刚才答错的题`, hasWrong,
          hasWrong ? `有错题记录（${(wt.match(/错题本[^\n]*/) || [''])[0].slice(0, 40)}）`
            : '错题本为空');
        note(`${label} 错题本内容`, wt.replace(/\n/g, ' ').slice(0, 120));
        await shot(`04-${label}-wrongbook`);
      }
      if (await openTab('复习安排')) {
        const rt = await tabPane().innerText().catch(() => '');
        const hasReview = !/暂无待复习的知识点/.test(rt);
        record(`[${label}] 错题后能看到自动安排的复习`, hasReview,
          hasReview ? `有复习任务（${(rt.match(/复习安排[\s\S]{0,60}/) || [''])[0].replace(/\n/g, ' ')}）`
            : '复习安排为空');
        note(`${label} 复习安排内容`, rt.replace(/\n/g, ' ').slice(0, 120));
      }
      if (await openTab('掌握度')) {
        const mt = await tabPane().innerText().catch(() => '');
        note(`${label} 掌握度内容`, mt.replace(/\n/g, ' ').slice(0, 120));
        record(`[${label}] 掌握度在有作答证据后能看到估计值`,
          /待评估|暂估|已掌握|未掌握|掌握/.test(mt), mt.replace(/\n/g, ' ').slice(0, 60));
      }
    } catch (e) {
      record(`【第 4 步】${label} 学习流程`, false, String(e.message || e));
      await shot(`04-${label}-error`).catch(() => {});
    }
  };

  await studentRun(STUDENT_A, '学生A');
  await studentRun(STUDENT_B, '学生B');

  // ================================================================
  // 5) 教师查看学情
  // ================================================================
  try {
    await doLogout();
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await doLogin(TEACHER);
    await gotoMenu('教师工作台');
    await page.waitForTimeout(2000);
    await gotoTeacherPanel('学情分析');
    await page.waitForTimeout(1500);
    const sel = page.locator('.ant-select-selector').first();
    if (await sel.count()) {
      await sel.click();
      await page.waitForSelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
        { timeout: 8000 }).catch(() => {});
      const opt = page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option').first();
      if (await opt.count()) await opt.click();
    }
    await page.waitForTimeout(3000);
    const at = await text();
    record('教师能在「学情分析」看到参与情况等指标',
      /参与情况|任务完成情况|知识点作答情况/.test(at),
      (at.match(/参与情况[\s\S]{0,60}/) || [''])[0].replace(/\n/g, ' ').slice(0, 70));
    // 样本不足必须明确说明，而不是显示 0/空表
    if (/数据不足/.test(at)) {
      record('样本不足时给出明确说明（而不是假装有结论）', true,
        (at.match(/数据不足[^\n]*/) || [''])[0].slice(0, 70));
    } else {
      observe('学情分析未显示"数据不足"', '可能样本量已达标，请人工确认数字与作答是否对得上');
    }
    await shot('05-teacher-analytics');
  } catch (e) {
    record('【第 5 步】教师查看学情', false, String(e.message || e));
    await shot('05-error').catch(() => {});
  }

  // ================================================================
  // 6) 屏幕尺寸：常见笔记本（1366×768）与手机（390×844）
  // ================================================================
  // 用 URL 直达而不是点侧边栏：窄屏下 antd 侧边栏会收起，点菜单本身会超时
  const ROUTES = [
    ['我的学习', '/student'],
    ['AI学习助手', '/chat'],
    ['设置', '/settings'],
    ['教师工作台', '/teacher'],
    ['知识库', '/knowledge'],
    ['学习计划', '/learning'],
  ];
  /**
   * 量一下当前页面的横向溢出情况。
   *
   * 注意：必须等内容真正渲染完再量。否则会量到"还在加载中的空壳页面"，
   * 得到"没有溢出"的假象（本项目第一次量窄屏时就踩过这个坑）。
   */
  const measure = async (tag) => {
    await page.waitForFunction(
      () => document.body.innerText.replace(/\s/g, '').length > 400,
      null, { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(1200);
    const o = await overflow();
    const bad = o.scrollW > o.clientW + 4;
    return { bad, detail: `scrollWidth=${o.scrollW} clientWidth=${o.clientW}`, tag };
  };

  try {
    await page.setViewportSize({ width: 1366, height: 768 });
    await doLogout();
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await doLogin(TEACHER);

    // 6.1 常见笔记本（1366×768）——这是试用/演示的实际环境，必须全部无横向溢出
    for (const [title, path] of ROUTES) {
      await page.goto(BASE + path, { waitUntil: 'networkidle' });
      await page.waitForTimeout(2500);
      const m = await measure(`laptop-${title}`);
      record(`1366×768：${title} 无横向溢出`, !m.bad, m.detail);
      await shot(`06-laptop-${path.replace(/[^\w]/g, '')}`);
    }

    // 6.1b 介于两者之间的宽度（小笔记本 / 平板横屏 800px）：实测侧边栏仍然占 220px，
    //      内容被挤到 981px → 横向溢出。这里如实记录（属已知局限，见文档）。
    const midBad = [];
    for (const [title, path] of ROUTES) {
      await page.setViewportSize({ width: 800, height: 700 });
      await page.goto(BASE + path, { waitUntil: 'networkidle' });
      const m = await measure(`800-${title}`);
      if (m.bad) midBad.push(`${title}（${m.detail}）`);
    }
    record('800px 宽度下已实测并记录结果', true,
      midBad.length ? `横向溢出 ${midBad.length}/${ROUTES.length} 个页面` : '无横向溢出');
    if (midBad.length) {
      observe('800px 宽度（小笔记本/平板横屏）页面横向溢出',
        `${midBad.join('；')} —— 建议试用统一用 ≥1280px 的屏幕`);
    }

    // 6.2 手机（390×844）：≤900px 改为"顶栏 + 抽屉导航"，
    //     这里既量溢出，也确认每页都能打开抽屉导航（本轮新增的移动端导航）。
    const phoneInfo = [];
    for (const [title, path] of ROUTES) {
      await page.setViewportSize({ width: 390, height: 844 });   // 必须显式设置，否则沿用上一轮的宽度
      await page.goto(BASE + path, { waitUntil: 'networkidle' });
      await page.waitForTimeout(2200);
      const m = await measure(`phone-${title}`);
      const menuBtn = page.locator('button[aria-label="打开导航菜单"]').first();
      let drawerOk = false;
      if (await menuBtn.count()) {
        await menuBtn.click();
        await page.waitForTimeout(700);
        drawerOk = (await page.locator('.ant-drawer .ant-menu-item').count()) > 0;
        await page.keyboard.press('Escape');
        await page.waitForTimeout(400);
      }
      phoneInfo.push({ title, ...m, drawerOk });
      await shot(`06-phone-${path.replace(/[^\w]/g, '')}`);
    }
    const overflowPages = phoneInfo.filter((i) => i.bad).map((i) => `${i.title}（${i.detail}）`);
    const navOk = phoneInfo.filter((i) => i.drawerOk).length;
    record('手机窄屏（390px）：每页都能打开抽屉导航', navOk === ROUTES.length,
      `可导航 ${navOk}/${ROUTES.length}；横向溢出 ${overflowPages.length}/${ROUTES.length}`);
    if (overflowPages.length) {
      observe('手机窄屏页面横向溢出（需左右拖动）', overflowPages.join('；'));
    }

    // 6.3 学生手机端最关心的一步：能不能输入班级邀请码
    //     （先在桌面宽度登录，再切到手机宽度测量：390px 下侧边栏隐藏，登录按钮点不到）
    await page.setViewportSize({ width: 1366, height: 768 });
    await doLogout();
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await doLogin(STUDENT_A);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(BASE + '/student', { waitUntil: 'networkidle' });
    await page.waitForTimeout(2500);
    await page.locator('.ant-tabs-tab:has-text("我的课程")').first().click().catch(() => {});
    await page.waitForTimeout(2000);
    const joinBox = page.locator('input[placeholder*="班级邀请码"]').first();
    const visible = await joinBox.isVisible().catch(() => false);
    record('手机屏幕上「入班输入框」可见', visible,
      visible ? '' : '不可见，需用电脑入班');
    await shot('06-phone-student-join');
  } catch (e) {
    record('【第 6 步】屏幕尺寸检查', false, String(e.message || e));
  }

  await browser.close();

  // ================================================================
  // 汇总
  // ================================================================
  console.log('\n===== 体验观察（不阻断流程，但影响首次使用体验）=====');
  if (!observations.length) console.log('  （无）');
  observations.forEach((o, i) => console.log(`  ${i + 1}. ${o.name}：${o.detail}`));

  const realFailures = [...new Set(failedRequests)];
  if (realFailures.length) {
    console.log('\n===== 界面产生的 4xx/5xx 请求（需确认是否被界面正确提示）=====');
    realFailures.slice(0, 20).forEach((l) => console.log('  ' + l));
  }
  if (pageErrors.length) {
    console.log('\n===== 页面 JS 异常 =====');
    [...new Set(pageErrors)].slice(0, 10).forEach((l) => console.log('  ' + l));
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}，体验观察 ${observations.length} 条`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error('走查脚本异常终止：', e);
  process.exit(3);
});
