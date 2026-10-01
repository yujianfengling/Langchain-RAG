/**
 * 响应式走查（390 / 800 / 1366 三个宽度，真实浏览器）
 *
 * 目的：试用前确认"手机上学生能不能自己走完"，以及教师端在 800px 下会不会被溢出挡住按钮。
 *
 * 覆盖（对应本轮要求）：
 *   学生端：登录/注册、加入课程、查看作业、逐题作答、错题本、复习任务、学习助手
 *   教师端：800px 与 1366px 下关键操作按钮是否在可视区内、页面是否横向溢出
 *   两个"可疑宽度"：390（手机）、800（小笔记本/平板横屏）；1366（演示用笔记本）作对照
 *
 * 数据准备：脚本自己用管理员账号走一遍受控开通（邀请码→教师→课程→班级→讲义→AI 出题→发布），
 * 这样不依赖上一个脚本遗留的数据；全部写进**隔离数据目录**（启动前会自检，不满足直接退出 2）。
 *
 * 用法：
 *   node tests/e2e_responsive_walkthrough.js [frontendBase] [apiBase]
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const { chromium } = require('playwright');

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
function observe(name, detail) {
  observations.push({ name, detail });
  console.log(`OBSERVE | ${name} | ${detail}`);
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
    console.log('拒绝执行：响应式走查（会在后端写入账号/课程/作答数据）');
    console.log(`后端当前数据目录：${scope.dir_name}（正式目录）`);
    console.log('请用隔离目录启动后端后重试（EDU_DATA_DIR + ADMIN_USERS）。');
    console.log('='.repeat(64));
    process.exit(2);
  }
  console.log(`[数据隔离] 后端数据目录：${scope.dir_name}（隔离，已确认）`);
}

async function api(method, p, { token, body } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['X-User-Token'] = token;
  const resp = await fetch(`${API}${p}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  });
  let json = null;
  try { json = await resp.json(); } catch { /* 非 JSON */ }
  return { status: resp.status, json };
}

/** 用管理员通道准备一份"老师已发布的作业"，返回 {joinCode, courseTitle, assignmentTitle} */
async function bootstrapData(page) {
  const reg = await api('POST', '/api/auth/register',
    { body: { username: ADMIN_NAME, password: PW } });
  let adminToken = reg.json?.data?.token;
  if (!adminToken) {
    const login = await api('POST', '/api/auth/login',
      { body: { username: ADMIN_NAME, password: PW } });
    adminToken = login.json?.data?.token;
  }
  if (!adminToken) throw new Error('管理员账号不可用（后端需配置 ADMIN_USERS=' + ADMIN_NAME + '）');

  const inv = await api('POST', '/api/teacher/invites', { token: adminToken, body: { max_uses: 1 } });
  const inviteCode = inv.json?.data?.code;
  if (!inviteCode) throw new Error('管理员签发教师邀请码失败：' + JSON.stringify(inv.json));

  const tName = `resp_teacher_${SUFFIX}`;
  const tReg = await api('POST', '/api/auth/register', { body: { username: tName, password: PW } });
  const teacherToken = tReg.json?.data?.token;
  await api('POST', '/api/teacher/apply-invite', { token: teacherToken, body: { code: inviteCode } });

  const course = await api('POST', '/api/teacher/courses', {
    token: teacherToken, body: { title: `响应式走查课程（${SUFFIX}）` },
  });
  const courseId = course.json?.data?.id;
  if (!courseId) throw new Error('建课失败：' + JSON.stringify(course.json));

  // 讲义（上传后后端会自动建课程知识库）
  const md = `# 二叉树\n\n## 三种遍历\n- 前序：根 → 左 → 右\n- 中序：左 → 根 → 右\n- 后序：左 → 右 → 根\n\n`
    + `## 二叉搜索树\n左子树所有结点小于根，右子树所有结点大于根。\n`;
  const fd = new FormData();
  fd.append('file', new Blob([md], { type: 'text/markdown' }), '响应式走查讲义.md');
  const up = await fetch(`${API}/api/teacher/courses/${courseId}/materials?visibility=students`, {
    method: 'POST', headers: { 'X-User-Token': teacherToken }, body: fd,
  });
  const upJson = await up.json().catch(() => null);
  global.KB_READY = ((upJson?.data?.kb?.stats?.status) === 'ready');

  const klass = await api('POST', `/api/teacher/courses/${courseId}/classes`, {
    token: teacherToken, body: { name: `响应式走查班（${SUFFIX}）` },
  });
  const classId = klass.json?.data?.id;
  const joinCode = klass.json?.data?.join_code;

  const asg = await api('POST', `/api/teacher/courses/${courseId}/assignments`, {
    token: teacherToken,
    body: { class_id: classId, title: `响应式走查练习（${SUFFIX}）`, assign_type: 'quiz', estimated_minutes: 10 },
  });
  const asgId = asg.json?.data?.id;
  const gen = await api('POST', `/api/teacher/courses/${courseId}/assignments/generate-questions`, {
    token: teacherToken, body: { assignment_id: asgId, count: 3 },
  });
  if (gen.status === 200) {
    await api('POST', `/api/teacher/assignments/${asgId}/publish`, { token: teacherToken });
  }
  return { joinCode, classId, courseTitle: course.json.data.title,
           assignmentTitle: asg.json?.data?.title, genStatus: gen.status,
           teacherName: tName, teacherToken };
}

(async () => {
  await requireBackendIsolated();
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });

  // ---------- 数据准备（用 API，避免把准备过程混进"界面操作"结论里）----------
  const prep = await bootstrapData();
  console.log(`[准备] 课程=${prep.courseTitle} 班级邀请码=${prep.joinCode} AI 出题=${prep.genStatus}`);
  record('准备：老师建课/上传讲义/建班/AI 出题并发布作业',
    !!prep.joinCode && prep.genStatus === 200, `出题接口=${prep.genStatus} 班级码=${prep.joinCode}`);
  record('准备：上传讲义后课程知识库自动就绪（回归上一轮修复）', global.KB_READY === true,
    global.KB_READY ? 'kb.status=ready' : 'kb 未就绪');

  const WIDTHS = [390, 800, 1366];
  const meas = async (page, tag) => {
    await page.waitForFunction(
      () => document.body.innerText.replace(/\s/g, '').length > 300, null, { timeout: 20000 }
    ).catch(() => {});
    await page.waitForTimeout(1200);
    const o = await page.evaluate(() => ({
      scrollW: document.documentElement.scrollWidth,
      clientW: document.documentElement.clientWidth,
    }));
    return { bad: o.scrollW > o.clientW + 4, ...o, tag };
  };

  /**
   * 切换「我的学习」的标签页。
   *
   * 窄屏下 antd 会把放不下的标签收进「更多」下拉。此时直接点 `.ant-tabs-tab`
   * 会点到被裁掉的隐藏标签上——**看起来点了、其实没切换**（本轮就踩过：
   * 断言写成"不包含『暂无错题』"于是假通过）。这里统一走"更多 → 菜单项"的正常路径。
   */
  const openTab = async (page, label) => {
    /** 当前激活的标签名（antd 只会给一个 tab 加 ant-tabs-tab-active） */
    const activeLabel = () => page.evaluate(() => {
      const t = document.querySelector('.ant-tabs-nav .ant-tabs-tab-active');
      return t ? t.innerText.replace(/\s+/g, '') : '';
    });
    /** 该标签是否被收进「更多」下拉 */
    const isCollapsed = () => page.evaluate((lb) => {
      const more = document.querySelector('.ant-tabs-nav-more');
      if (!more) return false;
      const nav = document.querySelector('.ant-tabs-nav-list');
      const navRect = nav ? nav.getBoundingClientRect() : null;
      const tab = [...document.querySelectorAll('.ant-tabs-nav .ant-tabs-tab')]
        .find((t) => t.innerText.includes(lb));
      if (!tab || !navRect) return false;
      const r = tab.getBoundingClientRect();
      return r.right > navRect.right + 1 || r.left < navRect.left - 1;
    }, label);

    const want = label.replace(/\s+/g, '');
    // 最多试 3 次：窄屏下第一次点下拉有时不生效（脚本自己踩过），必须确认真的切过去了
    for (let attempt = 0; attempt < 3; attempt += 1) {
      if (await isCollapsed()) {
        await page.locator('.ant-tabs-nav-more').click();
        await page.waitForTimeout(800);
        const item = page.locator(`.ant-tabs-dropdown-menu-item:has-text("${label}")`).first();
        if (await item.count()) await item.click();
      } else {
        await page.locator(`.ant-tabs-nav .ant-tabs-tab:has-text("${label}")`).first().click();
      }
      for (let i = 0; i < 12; i += 1) {
        await page.waitForTimeout(500);
        const cur = await activeLabel();
        if (cur.startsWith(want) || cur.includes(want)) {
          await page.waitForTimeout(1200);   // 等内容加载完
          return page.locator('.ant-tabs-tabpane-active').last();
        }
      }
    }
    observe(`切到「${label}」标签没有生效`, `尝试 3 次后当前标签=${await activeLabel()}`);
    return page.locator('.ant-tabs-tabpane-active').last();
  };

  /** 元素是否真的可点（在视口内且未被遮挡） */
  const clickable = async (page, locator) => {
    try {
      const el = locator.first();
      if (!(await el.count())) return { ok: false, why: '找不到元素' };
      const box = await el.boundingBox();
      if (!box) return { ok: false, why: '不可见' };
      const vw = page.viewportSize().width;
      if (box.x < 0 || box.x + box.width > vw + 1) {
        return { ok: false, why: `超出视口（x=${Math.round(box.x)} w=${Math.round(box.width)} vw=${vw}）` };
      }
      return { ok: true };
    } catch (e) {
      return { ok: false, why: String(e.message || e) };
    }
  };

  for (const w of WIDTHS) {
    console.log(`\n================ ${w}px ================`);
    const ctx = await browser.newContext({ viewport: { width: w, height: 844 } });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(String(e)));
    const tag = `resp-${w}`;
    const shot = (n) => page.screenshot({ path: path.join(OUT, `${tag}-${n}.png`), fullPage: true });

    const student = `resp_s_${w}_${SUFFIX}`;
    try {
      // ---- 1) 登录/注册可达 ----
      await page.goto(BASE, { waitUntil: 'networkidle' });
      const isNarrow = w <= 900;
      const loginEntry = isNarrow
        ? page.locator('.app-topbar button:has-text("登录 / 注册")').first()
        : page.locator('.ant-layout-sider button:has-text("登录 / 注册")').first();
      const loginOk = await clickable(page, loginEntry);
      record(`${w}px：登录/注册入口可点`, loginOk.ok, loginOk.why || '');
      await loginEntry.click();
      await page.waitForSelector('.ant-modal-content', { timeout: 10000 });
      await page.locator('button:has-text("还没有账号？去注册")').first().click();
      await page.fill('#username', student);
      await page.fill('#password', PW);
      const submitBtn = page.locator('.ant-modal-footer button.ant-btn-primary').first();
      const submitOk = await clickable(page, submitBtn);
      record(`${w}px：注册弹窗的提交按钮在可视区内`, submitOk.ok, submitOk.why || '');
      await submitBtn.click();
      await page.waitForSelector('.ant-modal-content', { state: 'detached', timeout: 20000 });
      record(`${w}px：注册成功并进入登录态`, true, student);

      // ---- 2) 导航 ----
      if (isNarrow) {
        const menuBtn = page.locator('button[aria-label="打开导航菜单"]');
        const menuOk = await clickable(page, menuBtn);
        record(`${w}px：窄屏顶栏「菜单」按钮可点`, menuOk.ok, menuOk.why || '');
        await menuBtn.click();
        await page.waitForTimeout(800);
        const items = await page.locator('.ant-drawer .ant-menu-item').allInnerTexts();
        record(`${w}px：抽屉里能看到全部菜单项`, items.length >= 5, items.join('/'));
        await page.locator('.ant-drawer .ant-menu-item:has-text("我的学习")').first().click();
      } else {
        await page.locator('.ant-menu-item:has-text("我的学习")').first().click();
      }
      await page.waitForTimeout(2500);
      const m0 = await meas(page, `${tag}-student`);
      record(`${w}px：我的学习页无横向溢出`, !m0.bad, `scrollWidth=${m0.scrollW} clientWidth=${m0.clientW}`);
      await shot('01-student-home');

      // ---- 3) 加入课程 ----
      await openTab(page, '我的课程');
      await page.waitForTimeout(1200);
      const joinBox = page.locator('input[placeholder*="班级邀请码"]').first();
      const joinOk = await clickable(page, joinBox);
      record(`${w}px：入班输入框在可视区内`, joinOk.ok, joinOk.why || '');
      await joinBox.fill(prep.joinCode);
      const joinBtn = page.locator('button:has-text("加入课程")').first();
      const joinBtnOk = await clickable(page, joinBtn);
      record(`${w}px：入班按钮在可视区内`, joinBtnOk.ok, joinBtnOk.why || '');
      await joinBtn.click();
      const joined = await page.waitForFunction(
        () => /已加入|已经在/.test(document.body.innerText), null, { timeout: 25000 }
      ).then(() => true).catch(() => false);
      record(`${w}px：用班级邀请码入班成功`, joined);
      const m1 = await meas(page, `${tag}-courses`);
      record(`${w}px：我的课程页无横向溢出`, !m1.bad, `scrollWidth=${m1.scrollW} clientWidth=${m1.clientW}`);
      await shot('02-student-joined');

      // ---- 4) 查看作业 + 逐题作答 ----
      await page.waitForTimeout(2000);
      const enterBtn = page.locator('button:has-text("进入作答")').first();
      const enterOk = await clickable(page, enterBtn);
      record(`${w}px：「进入作答」按钮在可视区内`, enterOk.ok, enterOk.why || '');
      if (enterOk.ok) {
        await enterBtn.click();
        await page.waitForSelector('.ant-modal-content', { timeout: 15000 });
        await page.waitForTimeout(1500);
        const modalBox = await page.locator('.ant-modal-content').last().boundingBox();
        const vw = page.viewportSize().width;
        record(`${w}px：作答弹窗不超出屏幕`,
          !!modalBox && modalBox.x >= -1 && modalBox.x + modalBox.width <= vw + 1,
          modalBox ? `弹窗 x=${Math.round(modalBox.x)} w=${Math.round(modalBox.width)} 视口=${vw}` : '无弹窗');

        let answered = 0;
        for (let i = 0; i < 6; i += 1) {
          const am = page.locator('.ant-modal-content').filter({ hasText: '作答由后端判分' }).last();
          const submit = am.getByRole('button', { name: /提\s*交\s*本\s*题/ }).first();
          if (!(await submit.count())) break;
          const groups = am.locator('.ant-radio-group');
          const gCount = await groups.count();
          let target = null;
          for (let g = 0; g < gCount; g += 1) {
            const grp = groups.nth(g);
            if (await grp.locator('input[type=radio]').first().isEnabled().catch(() => false)) {
              target = grp; break;
            }
          }
          if (!target) break;
          const subOk = await clickable(page, submit);
          if (!subOk.ok) {
            observe(`${w}px 作答弹窗里的「提交本题」不在可视区`, subOk.why);
            break;
          }
          await target.locator('.ant-radio-wrapper').first().click();
          await submit.click();
          const judged = await page.waitForFunction(
            () => /回答正确|回答错误/.test(document.body.innerText), null, { timeout: 25000 }
          ).then(() => true).catch(() => false);
          if (!judged) break;
          answered += 1;
          await page.waitForTimeout(1000);
        }
        record(`${w}px：能逐题作答并看到判分`, answered > 0, `已作答 ${answered} 题`);
        await shot('03-answer-modal');
        const closeBtn = page.locator('.ant-modal-content').last()
          .getByRole('button', { name: /关\s*闭/ }).first();
        if (await closeBtn.count()) { await closeBtn.click(); await page.waitForTimeout(1500); }
        else { await page.keyboard.press('Escape'); await page.waitForTimeout(1200); }
      }

      // ---- 5) 错题本 / 复习任务 ----
      const wrongPane = await openTab(page, '错题本');
      const wrongText = await wrongPane.innerText().catch(() => '');
      // 正向证据：必须是错题本自己的卡片，且真的有题目内容
      // （不能靠"没有『暂无错题』"反推——切标签失败时那样会假通过）
      const wrongOk = /错题本（按题目聚合/.test(wrongText)
        && (/你的作答/.test(wrongText) || /作答 \d+ 次/.test(wrongText));
      record(`${w}px：错题本能看到刚答错的题（回归上一轮修复）`, wrongOk,
        wrongText.replace(/\n/g, ' ').slice(0, 70));
      const m2 = await meas(page, `${tag}-wrong`);
      record(`${w}px：错题本页无横向溢出`, !m2.bad, `scrollWidth=${m2.scrollW} clientWidth=${m2.clientW}`);
      // 表格比容器宽时必须是"容器内可横向滚动"，而不是被裁掉看不见
      const tbl = await page.evaluate(() => {
        const pane = document.querySelector('.ant-tabs-tabpane-active');
        const t = pane && pane.querySelector('.ant-table-content, .ant-table-body');
        if (!t) return { hasTable: false };
        const cs = getComputedStyle(t);
        const before = t.scrollLeft;
        t.scrollLeft = 9999;
        const reachable = t.scrollLeft > 0;
        t.scrollLeft = before;
        return { hasTable: true, scrollW: t.scrollWidth, clientW: t.clientWidth,
                 overflowX: cs.overflowX, reachable, cells: pane.querySelectorAll('.ant-table-tbody td').length };
      });
      if (tbl.hasTable) {
        record(`${w}px：错题本表格内容可达（不被裁掉）`,
          tbl.reachable || tbl.scrollW <= tbl.clientW + 2,
          `表格 ${tbl.scrollW}/${tbl.clientW} overflowX=${tbl.overflowX} 可横向滚动=${tbl.reachable} 单元格=${tbl.cells}`);
      }
      await shot('04-wrongbook');

      const revPane = await openTab(page, '复习安排');
      const revText = await revPane.innerText().catch(() => '');
      const hasReview = /错题自动安排的复习/.test(revText) && /到期/.test(revText);
      record(`${w}px：复习任务能看到（回归上一轮修复）`, hasReview,
        revText.replace(/\n/g, ' ').slice(0, 70));
      const reviewBtn = page.getByRole('button', { name: /去\s*复\s*习/ }).first();
      if (await reviewBtn.count()) {
        const rOk = await clickable(page, reviewBtn);
        record(`${w}px：「去复习」按钮在可视区内`, rOk.ok, rOk.why || '');
      }
      await shot('05-review');

      const masteryPane = await openTab(page, '掌握度');
      const masteryText = await masteryPane.innerText().catch(() => '');
      record(`${w}px：掌握度能看到该知识点的估计值（回归切页刷新）`,
        /知识点掌握度/.test(masteryText) && /(暂估|已掌握|未掌握|待评估)/.test(masteryText),
        masteryText.replace(/\n/g, ' ').slice(0, 70));
      const m2b = await meas(page, `${tag}-mastery`);
      record(`${w}px：掌握度页无横向溢出`, !m2b.bad, `scrollWidth=${m2b.scrollW} clientWidth=${m2b.clientW}`);
      const mtbl = await page.evaluate(() => {
        const pane = document.querySelector('.ant-tabs-tabpane-active');
        const t = pane && pane.querySelector('.ant-table-content, .ant-table-body');
        if (!t) return { hasTable: false };
        const before = t.scrollLeft;
        t.scrollLeft = 9999;
        const reachable = t.scrollLeft > 0;
        t.scrollLeft = before;
        return { hasTable: true, scrollW: t.scrollWidth, clientW: t.clientWidth, reachable,
                 cells: pane.querySelectorAll('.ant-table-tbody td').length };
      });
      record(`${w}px：掌握度表格内容可达（宽表可横向滚动，不是被裁掉）`,
        !mtbl.hasTable || mtbl.reachable || mtbl.scrollW <= mtbl.clientW + 2,
        mtbl.hasTable
          ? `表格 ${mtbl.scrollW}/${mtbl.clientW} 可横向滚动=${mtbl.reachable} 单元格=${mtbl.cells}`
          : '该页无表格');
      await shot('05b-mastery');

      // ---- 6) 学习助手 ----
      if (isNarrow) {
        await page.locator('button[aria-label="打开导航菜单"]').click();
        await page.waitForTimeout(700);
        await page.locator('.ant-drawer .ant-menu-item:has-text("AI学习助手")').first().click();
      } else {
        await page.locator('.ant-menu-item:has-text("AI学习助手")').first().click();
      }
      await page.waitForTimeout(2500);
      const ta = page.locator('textarea:visible').last();
      const taOk = await clickable(page, ta);
      record(`${w}px：学习助手输入框在可视区内`, taOk.ok, taOk.why || '');
      // 学习助手需要先选学科，否则发送按钮是禁用的（这里按界面真实路径选一次）
      const chatSubj = page.locator('.ant-select').first();
      if (await chatSubj.count()) {
        const sel = chatSubj.locator('.ant-select-selector').first();
        const subjOk = await clickable(page, sel);
        record(`${w}px：学习助手的「学科」选择器在可视区内`, subjOk.ok, subjOk.why || '');
        if (subjOk.ok) {
          await sel.click();
          await page.waitForTimeout(800);
          const opt = page.locator(
            '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option').first();
          if (await opt.count()) { await opt.click(); await page.waitForTimeout(1200); }
        }
      }
      await ta.fill('二叉树的中序遍历顺序是什么？');
      const sendBtn = page.locator('button:has-text("发送")').first();
      const sendOk = await clickable(page, sendBtn);
      record(`${w}px：发送按钮在可视区内`, sendOk.ok, sendOk.why || '');
      record(`${w}px：发送按钮可选（选好学科后不再禁用）`,
        await sendBtn.isEnabled().catch(() => false));
      const m3 = await meas(page, `${tag}-chat`);
      record(`${w}px：学习助手页无横向溢出`, !m3.bad, `scrollWidth=${m3.scrollW} clientWidth=${m3.clientW}`);
      await shot('06-chat');

      // 手机端最关键的一步：真的提一个问题，看回答能不能出来（真模型，只在小屏跑一次）
      if (isNarrow && w === 390) {
        await sendBtn.click();
        const answered = await page.waitForFunction(
          () => /模型:/.test(document.body.innerText), null, { timeout: 240000 }
        ).then(() => true).catch(() => false);
        record(`${w}px：学习助手能真的提问并拿到回答（真实模型）`, answered,
          answered ? '页面出现"模型:"标识' : '等待 240 秒仍无回答');
        await shot('07-chat-answered');
        const m4 = await meas(page, `${tag}-chat-answered`);
        record(`${w}px：回答出来后页面仍无横向溢出`, !m4.bad,
          `scrollWidth=${m4.scrollW} clientWidth=${m4.clientW}`);
      }

      record(`${w}px：页面无 JS 异常`, pageErrors.length === 0, pageErrors.slice(0, 2).join(' ; '));
    } catch (e) {
      record(`${w}px：学生流程执行`, false, String(e.message || e));
      await shot('99-error').catch(() => {});
    }
    await ctx.close();
  }

  // ---------- 教师端：800px 与 1366px（不做手机适配，但不能挡住按钮）----------
  for (const w of [800, 1366]) {
    console.log(`\n============ 教师端 ${w}px ============`);
    const ctx = await browser.newContext({ viewport: { width: w, height: 900 } });
    const page = await ctx.newPage();
    const tag = `resp-teacher-${w}`;
    const shot = (n) => page.screenshot({ path: path.join(OUT, `${tag}-${n}.png`), fullPage: true });
    try {
      await page.goto(BASE, { waitUntil: 'networkidle' });
      const entry = w <= 900
        ? page.locator('.app-topbar button:has-text("登录 / 注册")').first()
        : page.locator('.ant-layout-sider button:has-text("登录 / 注册")').first();
      await entry.click();
      await page.fill('#username', prep.teacherName);
      await page.fill('#password', PW);
      await page.locator('.ant-modal-footer button.ant-btn-primary').first().click();
      await page.waitForSelector('.ant-modal-content', { state: 'detached', timeout: 20000 });

      if (w <= 900) {
        await page.locator('button[aria-label="打开导航菜单"]').click();
        await page.waitForTimeout(700);
        await page.locator('.ant-drawer .ant-menu-item:has-text("教师工作台")').first().click();
      } else {
        await page.locator('.ant-menu-item:has-text("教师工作台")').first().click();
      }
      await page.waitForTimeout(3000);
      const m = await meas(page, `${tag}-dashboard`);
      record(`${w}px：教师工作台无横向溢出`, !m.bad, `scrollWidth=${m.scrollW} clientWidth=${m.clientW}`);

      // 关键操作按钮必须可点（不能被溢出推到屏幕外）
      const refresh = page.locator('button:has-text("刷新")').first();
      const rOk = await clickable(page, refresh);
      record(`${w}px：教师端「刷新」按钮在可视区内`, rOk.ok, rOk.why || '');

      // 逐个面板检查（电脑端为主，800px 不能挡住操作按钮）。
      // 注意：教师端的 6 个面板在**页面内部**的菜单里（不是全局抽屉）。
      const PANELS = [
        ['班级管理', '新建班级', '重置邀请码'],
        ['课程资料', '重建知识库', '删除'],
        ['教案', '生成教案', null],
        ['作业与练习', '新建作业', '预览题目'],
        ['学情分析', '刷新', null],
      ];
      let panelIdx = 0;
      for (const [panel, keyBtn, rowBtn] of PANELS) {
        panelIdx += 1;
        const item = page.locator(`.ant-menu-item:has-text("${panel}")`).last();
        const itemOk = await clickable(page, item);
        record(`${w}px：教师端「${panel}」入口在可视区内`, itemOk.ok, itemOk.why || '');
        if (!itemOk.ok) continue;
        await item.click();
        await page.waitForTimeout(2500);
        const mm = await meas(page, `${tag}-${panel}`);
        record(`${w}px：教师端「${panel}」无横向溢出`, !mm.bad,
          `scrollWidth=${mm.scrollW} clientWidth=${mm.clientW}`);
        const kb = page.getByRole('button', {
          name: new RegExp(keyBtn.split('').join('\\s*')),
        }).first();
        if (await kb.count()) {
          const kbOk = await clickable(page, kb);
          record(`${w}px：教师端「${panel}」关键按钮「${keyBtn}」在可视区内`, kbOk.ok, kbOk.why || '');
        }
        // 表格行内操作按钮（在横向滚动容器里）也必须能点到
        if (rowBtn) {
          const rb = page.getByRole('button', {
            name: new RegExp(rowBtn.split('').join('\\s*')),
          }).first();
          if (await rb.count()) {
            let rbOk = await clickable(page, rb);
            if (!rbOk.ok) {
              // 先尝试把表格容器滚到最右再判断（表格比容器宽时的正常操作方式）
              await page.evaluate(() => {
                document.querySelectorAll('.ant-table-content, .ant-table-body')
                  .forEach((el) => { el.scrollLeft = el.scrollWidth; });
              });
              await page.waitForTimeout(600);
              rbOk = await clickable(page, rb);
            }
            record(`${w}px：教师端「${panel}」行内按钮「${rowBtn}」可点到`, rbOk.ok, rbOk.why || '');
          }
        }
        await shot(`panel${panelIdx}-${panel}`);
      }
    } catch (e) {
      record(`${w}px：教师端流程执行`, false, String(e.message || e));
      await shot('99-error').catch(() => {});
    }
    await ctx.close();
  }

  await browser.close();

  console.log('\n===== 体验观察 =====');
  if (!observations.length) console.log('  （无）');
  observations.forEach((o, i) => console.log(`  ${i + 1}. ${o.name}：${o.detail}`));

  const failed = results.filter((r) => !r.ok);
  console.log(`\n合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}，观察 ${observations.length} 条`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error('响应式走查异常终止：', e);
  process.exit(3);
});
