/**
 * 重大计划调整「确认应用」流程 + 学生菜单教师入口 的浏览器验收（2026-10-01）
 *
 * 用法：
 *   # 1) 先准备隔离数据（会打印 seed.json）
 *   $env:EDU_DATA_DIR="D:/dev/git_text_work_data"
 *   python tests/seed_plan_proposal.py --out tests/_plan_confirm_seed.json
 *   # 2) 用**同一个** EDU_DATA_DIR 启动后端与前端
 *   node tests/e2e_plan_confirm_flow.js http://localhost:3000 http://127.0.0.1:8000 tests/_plan_confirm_seed.json
 *
 * 验收点：
 *   1. 学生侧边栏不出现教师入口（修复前有重复的「教师端」，学生可见）
 *   2. 较大调整：点「确认应用」先弹确认框，列出"改哪些任务、影响什么"
 *   3. **取消不得应用**：不发送 apply 请求、确认框关闭、状态仍为 PROPOSED
 *   4. 确认后才发送 confirm:true，任务日期真的变化、状态变 APPLIED
 *   5. 撤销后日期恢复为调整前、状态变 UNDONE
 *
 * 依赖：playwright（仓库尚未声明该依赖，见 tests/e2e_*.js 的同类说明）。
 */

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const FRONTEND = process.argv[2] || 'http://localhost:3000';
const API = process.argv[3] || 'http://127.0.0.1:8000';
const SEED_PATH = process.argv[4] || path.join(__dirname, '_plan_confirm_seed.json');

const results = [];
function record(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} | ${name}${detail ? ' | ' + detail : ''}`);
}

async function api(method, p, token, body) {
  const res = await fetch(API + p, {
    method,
    headers: Object.assign({ 'X-User-Token': token },
      body ? { 'Content-Type': 'application/json' } : {}),
    body: body ? JSON.stringify(body) : undefined,
  });
  let json = null;
  try { json = await res.json(); } catch (e) { /* 非 JSON 响应 */ }
  return { status: res.status, body: json };
}

/** 数据隔离闸门：后端必须跑在隔离数据目录（本流程会写作答/任务数据） */
async function requireBackendIsolated(purpose) {
  let scope = null;
  try {
    const resp = await fetch(`${API}/api/health`);
    scope = ((await resp.json())?.data || {}).data_scope || {};
  } catch (e) {
    console.log(`拒绝执行：无法读取后端健康检查（${e}）。请先启动后端。`);
    process.exit(2);
  }
  if (!scope.isolated) {
    console.log('='.repeat(64));
    console.log(`拒绝执行：${purpose}`);
    console.log(`后端当前数据目录：${scope.dir_name}（正式目录）`);
    console.log('请用隔离目录启动后端，例如：');
    console.log('  $env:EDU_DATA_DIR="D:/dev/git_text_work_data"');
    console.log('  python -m uvicorn api.app:app --host 127.0.0.1 --port 8000');
    console.log('='.repeat(64));
    process.exit(2);
  }
  console.log(`[数据隔离] 后端数据目录：${scope.dir_name}（隔离，已确认）`);
}

// ---------- 确认框状态管理 ----------
// antd 的 Popconfirm 关闭后仍留在 DOM 里（隐藏）且带淡出动画。必须先确认
// "没有可见确认框"再点触发按钮，否则会把已打开的框点关，随后的点击就会落在
// 正在消失的元素上（表现为 pointer 被上层元素拦截）。
function visiblePop(page) {
  return page.locator('.ant-popconfirm:visible').last();
}
async function visiblePopCount(page) {
  return await page.locator('.ant-popconfirm:visible').count();
}
async function ensureNoPop(page) {
  for (let i = 0; i < 10; i++) {
    if (await visiblePopCount(page) === 0) return true;
    await page.keyboard.press('Escape').catch(() => {});
    await page.waitForTimeout(300);
  }
  return (await visiblePopCount(page)) === 0;
}

/** 按坐标点击：确认框锚定在页面元素上，Playwright 的"滚动进视口"会让弹层重新定位，
 *  导致点击落到被覆盖的卡片上。这里改为按几何中心点击（不做自动滚动）。 */
async function clickByBox(page, loc) {
  const box = await loc.boundingBox();
  if (!box) throw new Error('元素无几何信息，无法点击');
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

async function centerCard(page, card) {
  await card.scrollIntoViewIfNeeded().catch(() => {});
  await page.evaluate(() => window.scrollBy(0, -180));
  await page.waitForTimeout(500);
}

async function openPop(page, trigger) {
  if (!(await ensureNoPop(page))) return null;
  await trigger.click();  // 触发按钮用常规点击（卡片已居中，无需自动滚动）
  const pop = visiblePop(page);
  try {
    await pop.waitFor({ state: 'visible', timeout: 6000 });
  } catch (e) {
    return null;
  }
  await page.waitForTimeout(400); // 等动画稳定
  return pop;
}

async function openTab(page, label) {
  const direct = page.getByRole('tab', { name: label, exact: true });
  if (await direct.count() && await direct.first().isVisible().catch(() => false)) {
    await direct.first().click();
    return true;
  }
  const more = page.locator('.ant-tabs-nav-more');
  if (await more.count()) {
    await more.first().click();
    await page.waitForTimeout(300);
    const item = page.locator('.ant-tabs-dropdown-menu-item', { hasText: label });
    if (await item.count()) { await item.first().click(); return true; }
  }
  return false;
}

async function adjustmentStatus(token, seed) {
  const r = await api('GET',
    `/api/plan/adjustments?subject_id=${encodeURIComponent(seed.subject_id)}`, token);
  const data = (r.body && r.body.data) || {};
  const list = data.adjustments || data.items || (Array.isArray(data) ? data : []);
  const found = (Array.isArray(list) ? list : []).find(a => a.id === seed.adjustment_id);
  return found ? found.status : null;
}

async function taskDates(token, seed) {
  const r = await api('GET',
    `/api/student/tasks?subject_id=${encodeURIComponent(seed.subject_id)}`, token);
  const data = (r.body && r.body.data) || {};
  const all = [].concat(data.today || [], data.todo || [], data.overdue || [],
    data.completed || []);
  const map = {};
  for (const t of all) map[t.id] = t.planned_date;
  return map;
}

(async () => {
  if (!fs.existsSync(SEED_PATH)) {
    console.log(`拒绝执行：找不到 seed 文件 ${SEED_PATH}。请先运行：`
      + 'python tests/seed_plan_proposal.py --out <路径>');
    process.exit(2);
  }
  const SEED = JSON.parse(fs.readFileSync(SEED_PATH, 'utf8'));

  await requireBackendIsolated('重大计划调整确认流程验收');

  const browser = await chromium.launch().catch(async () => {
    // 回退到系统 Chrome（与既有 e2e 脚本一致）
    return chromium.launch({ channel: 'chrome' });
  });
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  await ctx.addInitScript(t => { localStorage.setItem('edu_user_token', t); },
    SEED.student_token);
  const page = await ctx.newPage();

  const applyBodies = [];
  page.on('request', req => {
    if (req.method() === 'POST' && /\/api\/plan\/adjustments\/[^/]+\/apply$/.test(req.url())) {
      applyBodies.push(req.postData() || '');
    }
  });
  const pageErrors = [];
  page.on('pageerror', e => pageErrors.push(String(e)));

  // ---------- 1. 学生菜单 ----------
  await page.goto(`${FRONTEND}/student`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);
  const studentTeacherItems = await page.locator('.ant-menu-item', { hasText: '教师' }).count();
  record('学生侧边栏不出现教师入口', studentTeacherItems === 0,
    `命中教师菜单项 ${studentTeacherItems} 个（修复前为 1）`);

  // ---------- 2. 打开「计划与时间」并定位本次种子的记录 ----------
  record('能打开「计划与时间」页签', await openTab(page, '计划与时间'));
  await page.waitForTimeout(2000);

  const card = page.locator('.ant-list-item', { hasText: SEED.marker }).first();
  const cardVisible = await card.count() > 0;
  record('界面展示"较大调整"记录', cardVisible, cardVisible ? '' : `未找到含 ${SEED.marker} 的记录`);

  if (cardVisible) {
    const cardText = (await card.innerText()).replace(/\s+/g, ' ');
    record('确认前已展示调整内容（日期变更条目）', /→/.test(cardText), cardText.slice(0, 110));
  }

  await centerCard(page, card);
  // antd 会给"两个汉字"的按钮自动插入空格（取 消 / 撤 销），因此用正则容忍空格
  const confirmBtn = card.getByRole('button').filter({ hasText: /确认应用/ }).first();
  const confirmBtnVisible = await confirmBtn.count() > 0;
  record('较大调整的按钮文案为「确认应用」', confirmBtnVisible);

  // ---------- 3. 取消不得应用 ----------
  if (confirmBtnVisible) {
    const pop = await openPop(page, confirmBtn);
    record('点击后弹出确认框（先展示影响再确认）', !!pop);
    if (pop) {
      const popText = (await pop.innerText()).replace(/\s+/g, ' ');
      record('确认框内列出将调整的任务与日期', /→/.test(popText), popText.slice(0, 150));
      await clickByBox(page, pop.getByRole('button').filter({ hasText: /取\s*消/ }).first());
      await page.waitForTimeout(1200);

      const remaining = await visiblePopCount(page);
      record('取消后确认框关闭', remaining === 0, `仍可见确认框 ${remaining} 个`);
      record('取消后不发送 apply 请求', applyBodies.length === 0,
        `apply 请求数=${applyBodies.length}`);
      const st = await adjustmentStatus(SEED.student_token, SEED);
      record('取消后状态仍为 PROPOSED（未被应用）', st === 'PROPOSED', `status=${st}`);
    }
  }

  // ---------- 4. 确认后才应用 ----------
  if (confirmBtnVisible) {
    const pop = await openPop(page, confirmBtn);
    if (pop) {
      const ok = pop.getByRole('button').filter({ hasText: /确认应用/ }).first();
      const okCount = await ok.count();
      await clickByBox(page, okCount ? ok : pop.locator('.ant-btn-primary').first());
      await page.waitForTimeout(3000);

      let confirmFlag = null;
      if (applyBodies.length) {
        try { confirmFlag = JSON.parse(applyBodies[applyBodies.length - 1]).confirm; }
        catch (e) { confirmFlag = 'unparsable'; }
      }
      record('确认后发送 confirm:true', confirmFlag === true,
        `apply 请求体=${applyBodies[applyBodies.length - 1] || '(无)'}`);

      const stApply = await adjustmentStatus(SEED.student_token, SEED);
      record('应用后状态为 APPLIED', stApply === 'APPLIED', `status=${stApply}`);

      const dates = await taskDates(SEED.student_token, SEED);
      const changed = SEED.task_ids.filter(
        id => dates[id] && dates[id] !== SEED.planned_dates_before[id]).length;
      record('应用后任务日期真的改变（全部重排，且不再等于原日期）',
        changed === SEED.task_ids.length,
        `变更 ${changed}/${SEED.task_ids.length}；日期=${JSON.stringify(dates)}`);

      // ---------- 5. 撤销 ----------
      await page.waitForTimeout(1200);
      const undoBtn = card.getByRole('button').filter({ hasText: /撤\s*销/ }).first();
      const undoVisible = await undoBtn.count() > 0;
      record('应用后出现「撤销」入口', undoVisible);
      if (undoVisible) {
        const upop = await openPop(page, undoBtn);
        if (upop) {
          await clickByBox(page, upop.locator('.ant-btn-primary').first());
          await page.waitForTimeout(3000);

          const stUndo = await adjustmentStatus(SEED.student_token, SEED);
          record('撤销后状态为 UNDONE', stUndo === 'UNDONE', `status=${stUndo}`);

          const dates2 = await taskDates(SEED.student_token, SEED);
          const restored = SEED.task_ids.filter(
            id => dates2[id] === SEED.planned_dates_before[id]).length;
          record('撤销后任务日期恢复为调整前', restored === SEED.task_ids.length,
            `恢复 ${restored}/${SEED.task_ids.length}；日期=${JSON.stringify(dates2)}`);
        } else {
          record('撤销确认框可打开', false);
        }
      }
    } else {
      record('确认应用确认框可打开', false);
    }
  }

  record('页面无 JS 异常', pageErrors.length === 0, pageErrors.slice(0, 2).join(' ; '));

  // ---------- 6. 教师侧菜单仍应有一个入口 ----------
  const tctx = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  await tctx.addInitScript(t => { localStorage.setItem('edu_user_token', t); }, SEED.teacher_token);
  const tpage = await tctx.newPage();
  await tpage.goto(`${FRONTEND}/student`, { waitUntil: 'domcontentloaded' });
  await tpage.waitForTimeout(2500);
  const teacherItems = await tpage.locator('.ant-menu-item', { hasText: '教师' }).count();
  record('教师侧边栏仍有且仅有一个教师入口', teacherItems === 1,
    `命中教师菜单项 ${teacherItems} 个（应为 1）`);

  await browser.close();

  const failed = results.filter(r => !r.ok);
  console.log(`\n合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`);
  process.exit(failed.length ? 1 : 0);
})().catch(e => { console.error('脚本异常：', e); process.exit(1); });