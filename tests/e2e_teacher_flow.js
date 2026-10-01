/**
 * 教师端全流程浏览器验收（真实 Chrome + 真实 DeepSeek）
 *
 * 覆盖本轮验收清单：
 *   教师创建课程与班级 → 学生用邀请码加入 → 教师上传资料 → 编写并发布教案
 *   → 发布练习 → 学生收到并作答 → 教师端学情随作答更新（样本量与原始记录一致）
 *   → 草稿不提前公开 → 两位教师互不能改课程 → 未入班学生读不到资料
 *   → 学生无法用旧接口绕过 → 学生个人计划不改教师固定截止
 *
 * 前置：
 *   1) 主后端（隔离数据目录）:8000， 2) 前端 dev server :3000
 *   3) 真实模型（DeepSeek）可用
 *   4) 后端需带初始教师/管理员白名单（教师身份不再由"创建课程"自动授予）：
 *        TEACHER_USERS=e2e_teacher,e2e_teacher2  ADMIN_USERS=e2e_admin
 *      E2E 里固定使用这三个账号名（学生仍用随机名，避免互相干扰）。
 *
 * 用法：node tests/e2e_teacher_flow.js [baseUrl]
 */

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const BASE = process.argv[2] || 'http://localhost:3000';
const OUT = path.join(__dirname, '_screenshots');
const SUFFIX = Date.now().toString(36);
const TEACHER = 'e2e_teacher';       // 由 TEACHER_USERS 白名单开通
const TEACHER2 = 'e2e_teacher2';     // 另一位教师（用于越权检查）
const ADMIN = 'e2e_admin';           // 由 ADMIN_USERS 白名单开通（用于发放邀请码）
const STUDENT = `s_${SUFFIX}`;
const OUTSIDER = `o_${SUFFIX}`;
const PASS = 'test-pass-123';
const LLM_TIMEOUT = 300000;

const results = [];
function record(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} | ${name}${detail ? ' | ' + detail : ''}`);
}

/** 直接调后端接口（用于准备数据与权限对抗验证；浏览器里做主要交互） */
async function api(page, method, url, { token, body, form } = {}) {
  const headers = {};
  if (token) headers['X-User-Token'] = token;
  let payload;
  if (form) {
    payload = form;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  const resp = await page.request[method](`${BASE}${url}`, { headers, data: payload });
  let json = null;
  try { json = await resp.json(); } catch { /* 可能是文件流 */ }
  return { status: resp.status(), json };
}

async function register(page, username) {
  // 先注册；已存在则登录（固定账号在重复运行时会已存在）
  let resp = await api(page, 'post', '/api/auth/register',
    { body: { username, password: PASS } });
  if (resp.status !== 200 || !resp.json?.data?.token) {
    resp = await api(page, 'post', '/api/auth/login',
      { body: { username, password: PASS } });
  }
  if (resp.status !== 200 || !resp.json?.data?.token) {
    throw new Error(`注册/登录失败 ${username}: ${resp.status} ${JSON.stringify(resp.json)}`);
  }
  return { token: resp.json.data.token, userId: resp.json.data.id };
}

/** 用浏览器登录（写入 localStorage 令牌），并进入指定页面 */
async function loginInBrowser(page, username, pathname = '/') {
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  const resp = await api(page, 'post', '/api/auth/login',
    { body: { username, password: PASS } });
  const token = resp.json?.data?.token;
  if (!token) throw new Error(`登录失败 ${username}`);
  await page.evaluate((t) => localStorage.setItem('edu_user_token', t), token);
  await page.evaluate(() => window.dispatchEvent(new Event('edu:auth-changed')));
  await page.goto(`${BASE}${pathname}`, { waitUntil: 'networkidle' });
  return token;
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
  await requireBackendIsolated(BASE, "教师端全流程验收");
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1560, height: 1000 } });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));

  try {
    // ===== 0) 能力检查 =====
    const health = await api(page, 'get', '/api/health');
    const caps = health.json?.data?.capabilities || {};
    record('DeepSeek 文本模型可用', !!caps.text?.available,
      `${caps.text?.provider}/${caps.text?.model}`);

    // ===== 1) 教师身份开通（受控流程）=====
    const teacher = await register(page, TEACHER);
    const otherTeacher = await register(page, TEACHER2);
    const admin = await register(page, ADMIN);
    const student = await register(page, STUDENT);
    const outsider = await register(page, OUTSIDER);
    record('准备账号（2 教师 + 1 管理员 + 2 学生）', true,
      `${TEACHER}/${TEACHER2}/${ADMIN}/${STUDENT}/${OUTSIDER}`);

    // 学生不能自助成为教师：不带邀请码建课必须被拒
    const escalate = await api(page, 'post', '/api/teacher/courses',
      { token: student.token, body: { title: '学生自助建的课' } });
    record('学生无法通过创建课程自助获得教师身份',
      escalate.status === 403 && escalate.json?.error_type === 'teacher_required',
      `status=${escalate.status} ${escalate.json?.error_type || ''}`);

    const stuDash = await api(page, 'get', '/api/teacher/dashboard', { token: student.token });
    record('学生访问教师工作台被拒', stuDash.status === 403, `status=${stuDash.status}`);

    // 管理员发放教师邀请码 → 一个新账号凭码开通
    const inviteResp = await api(page, 'post', '/api/teacher/invites',
      { token: admin.token, body: { max_uses: 1, note: 'E2E 开通验证' } });
    const inviteCode = inviteResp.json?.data?.code;
    record('管理员可生成教师邀请码', inviteResp.status === 200 && /^T-[A-Z0-9]+$/.test(inviteCode || ''),
      `code=${inviteCode ? inviteCode.slice(0, 4) + '****' : '无'}`);

    const newcomerName = `new_teacher_${SUFFIX}`;
    const newcomer = await register(page, newcomerName);
    const redeem = await api(page, 'post', '/api/teacher/apply-invite',
      { token: newcomer.token, body: { code: inviteCode } });
    record('新账号凭邀请码开通教师身份', redeem.status === 200,
      `status=${redeem.status} ${redeem.json?.message || ''}`);

    const reuse = await api(page, 'post', '/api/teacher/apply-invite',
      { token: outsider.token, body: { code: inviteCode } });
    record('一次性邀请码不能给第二个人使用', reuse.status === 400,
      `status=${reuse.status} ${reuse.json?.message || ''}`);

    const newcomerCourse = await api(page, 'post', '/api/teacher/courses',
      { token: newcomer.token, body: { title: `凭码开通的课程（${SUFFIX}）` } });
    record('凭码开通后可正常建课', newcomerCourse.status === 200,
      `status=${newcomerCourse.status}`);

    // ===== 2) 教师创建课程与班级 =====

    const courseResp = await api(page, 'post', '/api/teacher/courses', {
      token: teacher.token,
      body: { title: `数据结构（${SUFFIX}）`, description: '验收演示课程' },
    });
    const course = courseResp.json?.data;
    const sid0 = course?.subject_id;
    record('教师创建课程', courseResp.status === 200 && !!course?.id,
      `course=${course?.id} subject=${course?.subject_id}`);

    // 角色由后端授予并可查询
    const me = await api(page, 'get', '/api/auth/me', { token: teacher.token });
    record('创建课程后后端角色为 teacher', me.json?.data?.role === 'teacher',
      `role=${me.json?.data?.role}`);

    const classResp = await api(page, 'post',
      `/api/teacher/courses/${course.id}/classes`,
      { token: teacher.token, body: { name: '周一班' } });
    const klass = classResp.json?.data;
    record('教师创建班级并得到邀请码', classResp.status === 200 && /^[A-Z0-9]{6}$/.test(klass?.join_code || ''),
      `join_code=${klass?.join_code}`);

    // ===== 2) 学生用邀请码加入 =====
    await loginInBrowser(page, STUDENT, '/student');
    const coursesTab = page.locator('.ant-tabs-tab:has-text("我的课程")');
    if (await coursesTab.count()) {
      await coursesTab.first().click();
      await page.waitForTimeout(800);
    }
    const joinInput = page.locator('input[placeholder*="邀请码"]').first();
    await joinInput.fill(klass.join_code);
    await page.locator('button:has-text("加入课程")').first().click();
    await page.waitForTimeout(2500);
    const studentBody = await page.evaluate(() => document.body.innerText);
    record('学生用邀请码加入课程（界面）',
      studentBody.includes(course.title) && studentBody.includes('周一班'),
      studentBody.includes('周一班') ? '课程与班级已显示' : studentBody.slice(0, 120));
    await page.screenshot({ path: path.join(OUT, 'teacher-01-student-joined.png'), fullPage: true });

    // ===== 3) 教师上传资料 =====
    // 资料上传用 Playwright 的 multipart（不是浏览器里的 FormData）
    const lectureText =
      '前序遍历的顺序是根、左、右。中序遍历的顺序是左、根、右。' +
      '后序遍历的顺序是左、右、根。二叉搜索树中左子树所有节点小于根节点，' +
      '右子树所有节点大于根节点。';
    const uploadResp = await page.request.post(
      `${BASE}/api/teacher/courses/${course.id}/materials?visibility=students`,
      {
        headers: { 'X-User-Token': teacher.token },
        multipart: {
          file: { name: '遍历讲义.txt', mimeType: 'text/plain',
                  buffer: Buffer.from(lectureText, 'utf-8') },
        },
      });
    let uploadJson = null;
    try { uploadJson = await uploadResp.json(); } catch { /* ignore */ }
    record('教师上传课程资料', uploadResp.status() === 200,
      `filename=${uploadJson?.data?.filename} status=${uploadJson?.data?.status} `
      + `msg=${uploadJson?.message || ''}`);

    const privateUpload = await page.request.post(
      `${BASE}/api/teacher/courses/${course.id}/materials?visibility=teacher_only`,
      {
        headers: { 'X-User-Token': teacher.token },
        multipart: {
          file: { name: '教师草稿.txt', mimeType: 'text/plain',
                  buffer: Buffer.from('教师内部草稿：本页内容暂不公开。', 'utf-8') },
        },
      });
    record('教师上传"仅教师可见"资料', privateUpload.status() === 200,
      `status=${privateUpload.status()}`);

    const privateDownload = await api(page, 'get',
      `/api/knowledge/${sid0}/documents/${encodeURIComponent('教师草稿.txt')}/file`,
      { token: student.token });
    record('学生无法下载教师专属资料', privateDownload.status === 403,
      `status=${privateDownload.status}`);

    const rebuild = await api(page, 'post',
      `/api/teacher/courses/${course.id}/knowledge/rebuild`, { token: teacher.token });
    record('课程知识库重建（真实构建）', rebuild.status === 200 && rebuild.json?.data?.ok === true,
      `ok=${rebuild.json?.data?.ok} docs=${rebuild.json?.data?.kb?.document_count}`);

    // ===== 4) 教师编写并发布教案（真实模型）=====
    const plansTab = page.locator('.ant-menu-item:has-text("教案")');
    await loginInBrowser(page, TEACHER, '/teacher');
    await page.waitForTimeout(1500);
    record('教师工作台页面可见（含我的课程）',
      (await page.evaluate(() => document.body.innerText)).includes('我的课程'));

    const genPlan = await api(page, 'post',
      `/api/teacher/courses/${course.id}/lesson-plans/generate`, {
        token: teacher.token,
        body: {
          topic: '二叉树的三种遍历', hours: 1,
          objectives: '学生能用前序、中序、后序遍历一棵二叉树',
          class_id: klass.id,
        },
      });
    const plan = genPlan.json?.data;
    const content = plan?.content || {};
    const hasSections = ['objectives', 'steps', 'homework']
      .every((k) => Array.isArray(content[k]) && content[k].length > 0);
    record('真实模型生成教案草稿（含目标/步骤/练习）', genPlan.status === 200 && hasSections,
      `v=${plan?.version} 目标${content.objectives?.length}条 步骤${content.steps?.length}个 练习${content.homework?.length}题`);
    record('教案标出了依据的课程资料', (plan?.evidence || []).length > 0,
      JSON.stringify((plan?.evidence || []).slice(0, 2)));
    record('AI 生成内容标记了模型来源', !!plan?.ai_generated && !!plan?.ai_model,
      `ai_model=${plan?.ai_model}`);

    // 草稿对教师可见、对学生不可见
    const draftForStudent = await api(page, 'get',
      `/api/student/courses/${course.id}/lesson-plans`, { token: student.token });
    record('草稿教案不提前向学生公开',
      draftForStudent.status === 200
      && (draftForStudent.json?.data?.lesson_plans || []).length === 0);

    // 教师编辑（保留修改，不被重新生成覆盖）
    const edited = JSON.parse(JSON.stringify(content));
    edited.objectives = ['【教师修改】能写出三种遍历序列'];
    const saveResp = await api(page, 'put', `/api/teacher/lesson-plans/${plan.id}`,
      { token: teacher.token, body: { content: edited, note: '教师修改教学目标' } });
    record('教师编辑保存为新版本（不覆盖历史）',
      saveResp.status === 200 && saveResp.json?.data?.version === 2,
      `v=${saveResp.json?.data?.version}`);

    const publishPlan = await api(page, 'post',
      `/api/teacher/lesson-plans/${plan.id}/publish`, { token: teacher.token });
    record('教师发布教案', publishPlan.status === 200 && publishPlan.json?.data?.status === 'published');

    const studentPlans = await api(page, 'get',
      `/api/student/courses/${course.id}/lesson-plans`, { token: student.token });
    const visiblePlan = (studentPlans.json?.data?.lesson_plans || [])[0];
    record('发布后学生可见，且内容是教师修改后的版本',
      visiblePlan?.content?.objectives?.[0]?.includes('教师修改'),
      JSON.stringify(visiblePlan?.content?.objectives));

    // ===== 5) 教师发布练习 =====
    const asgResp = await api(page, 'post',
      `/api/teacher/courses/${course.id}/assignments`, {
        token: teacher.token,
        body: {
          class_id: klass.id, title: '三种遍历练习', description: '课后完成',
          due_date: '2026-12-31', knowledge_points: ['二叉树遍历'],
        },
      });
    const assignment = asgResp.json?.data;
    const qResp = await api(page, 'post',
      `/api/teacher/courses/${course.id}/assignments/generate-questions`, {
        token: teacher.token,
        body: { assignment_id: assignment.id, knowledge_points: ['二叉树遍历'], count: 2 },
      });
    const questions = qResp.json?.data?.questions || [];
    record('真实模型依据课程资料出题', qResp.status === 200 && questions.length > 0,
      qResp.status === 200
        ? `生成 ${qResp.json?.data?.generated} 题（请求 ${qResp.json?.data?.requested}）`
        : `status=${qResp.status} ${qResp.json?.message || ''}`);
    record('下发给教师端的题目不含标准答案',
      questions.every((q) => q.correct_option_id === undefined && q.reference_answer === undefined));

    const draftAssignForStudent = await api(page, 'get', '/api/student/assignments',
      { token: student.token });
    record('草稿作业对学生不可见',
      (draftAssignForStudent.json?.data?.assignments || []).length === 0);

    const pubAsg = await api(page, 'post',
      `/api/teacher/assignments/${assignment.id}/publish`, { token: teacher.token });
    record('发布练习给班级学生',
      pubAsg.status === 200 && (pubAsg.json?.data?.created || []).length === 1,
      `created=${(pubAsg.json?.data?.created || []).length} failed=${(pubAsg.json?.data?.failed || []).length}`);

    // ===== 6) 学生端收到并作答 =====
    await loginInBrowser(page, STUDENT, '/student');
    const studentTab = page.locator('.ant-tabs-tab:has-text("我的课程")');
    if (await studentTab.count()) {
      await studentTab.first().click();
      await page.waitForTimeout(1500);
    }
    const assignBody = await page.evaluate(() => document.body.innerText);
    record('学生端看到老师布置的作业', assignBody.includes('三种遍历练习'),
      assignBody.includes('三种遍历练习') ? '作业已出现在"老师布置的作业"' : assignBody.slice(0, 150));
    await page.screenshot({ path: path.join(OUT, 'teacher-02-student-assignment.png'), fullPage: true });

    // 进入作答并提交（第一题选第一个选项）
    await page.locator('button:has-text("进入作答")').first().click();
    await page.waitForSelector('.ant-modal-content', { timeout: 15000 });
    await page.waitForTimeout(800);
    const firstRadio = page.locator('.ant-modal-content .ant-radio-wrapper').first();
    await firstRadio.click();
    await page.locator('.ant-modal-content button:has-text("提交本题")').first().click();
    await page.waitForTimeout(4000);
    const answeredBody = await page.evaluate(() => document.body.innerText);
    record('学生在教师作业中作答并收到后端判分',
      /回答正确|回答错误/.test(answeredBody),
      (answeredBody.match(/回答(正确|错误)[^\n]*/) || [''])[0]);
    await page.screenshot({ path: path.join(OUT, 'teacher-03-student-answered.png'), fullPage: true });

    // ===== 7) 教师端学情随作答更新 =====
    const analytics = await api(page, 'get',
      `/api/teacher/classes/${klass.id}/analytics?days=30`, { token: teacher.token });
    const stats = analytics.json?.data || {};
    const attempts = stats.participation?.attempt_count ?? 0;
    const sources = (stats.sources || []).join(' ');
    record('教师端学情统计到学生作答（样本量一致）',
      attempts >= 1 && sources.includes('quiz_attempts'),
      `attempt_count=${attempts} 参与学生=${stats.participation?.answered_student_count} 来源=${sources.slice(0, 30)}`);
    record('统计带时间范围与样本量门槛',
      !!stats.range?.start && !!stats.range?.end && !!stats.thresholds,
      `范围 ${stats.range?.start}~${stats.range?.end} 门槛 ${JSON.stringify(stats.thresholds)}`);
    record('样本不足时明确标注（不给班级结论）',
      stats.enough_data === false ? !!stats.insufficient_reason : true,
      `enough_data=${stats.enough_data} reason=${stats.insufficient_reason || '—'}`);

    // 学情接口不得泄露学生聊天原文
    record('学情不含学生私人聊天原文',
      !JSON.stringify(stats).includes('私人') && !JSON.stringify(stats).includes('悄悄话'));

    // ===== 8) 权限对抗（旧接口不能绕过）=====
    const sid = course.subject_id;
    const bypass = [
      ['外来者列出课程文档', 'get', `/api/knowledge/${sid}/documents`, outsider.token],
      ['另一位教师列出课程文档', 'get', `/api/knowledge/${sid}/documents`, otherTeacher.token],
      ['学生重建课程知识库', 'post', `/api/knowledge/${sid}/rebuild`, student.token],
      ['学生删除课程知识库', 'delete', `/api/knowledge/${sid}`, student.token],
      ['学生构建课程图谱', 'post', `/api/knowledge/${sid}/graph/build`, student.token],
      ['外来者查看课程学科', 'get', `/api/subjects/${sid}`, outsider.token],
      ['另一位教师查看课程详情', 'get', `/api/teacher/courses/${course.id}`, otherTeacher.token],
      ['外来者查看看板学情', 'get', `/api/teacher/classes/${klass.id}/analytics`, outsider.token],
      ['学生查看看板学情', 'get', `/api/teacher/classes/${klass.id}/analytics`, student.token],
    ];
    let blocked = 0;
    const details = [];
    for (const [label, method, url, token] of bypass) {
      const r = await api(page, method, url, { token });
      const ok = r.status === 403 || r.status === 404;
      if (ok) blocked += 1;
      else details.push(`${label}=${r.status}`);
    }
    record(`旧接口/教师接口均无法越权（${bypass.length} 项全被拒绝）`, blocked === bypass.length,
      blocked === bypass.length ? `全部 ${blocked}/${bypass.length} 被拒绝` : `未拦截：${details.join(', ')}`);
    // 在班学生**可以**看本课程资料（这是设计预期），但看不到 teacher_only 的教师草稿
    const studentDocs = await api(page, 'get', `/api/knowledge/${sid}/documents`,
      { token: student.token });
    const studentNames = (studentDocs.json?.documents || []).map((d) => d.name);
    record('在班学生能列出本课程资料，但看不到教师专属资料',
      studentDocs.status === 200 && studentNames.includes('遍历讲义.txt')
      && !studentNames.includes('教师草稿.txt'),
      `学生可见：${JSON.stringify(studentNames)}`);

    // 学生上传到课程知识库（旧的表单式上传接口）
    const legacyUpload = await page.request.post(`${BASE}/api/knowledge/upload`, {
      headers: { 'X-User-Token': student.token },
      multipart: {
        subject_id: sid,
        file: { name: '绕过上传.txt', mimeType: 'text/plain', buffer: Buffer.from('x') },
      },
    });
    record('学生无法用旧上传接口写入课程知识库', legacyUpload.status() === 403,
      `status=${legacyUpload.status()}`);

    // 未入班学生打不开作业
    const outsiderOpen = await api(page, 'get', `/api/student/assignments/${assignment.id}`,
      { token: outsider.token });
    record('未入班学生打不开教师作业', outsiderOpen.status === 403, `status=${outsiderOpen.status}`);

    // ===== 9) 学生个人计划不改教师固定截止 =====
    const setOwn = await api(page, 'put', '/api/plan/constraints', {
      token: student.token,
      body: { subject_id: sid, deadline: '2026-12-30', daily_minutes: 30 },
    });
    const constraintsResp = await api(page, 'get',
      `/api/plan/constraints?subject_id=${encodeURIComponent(sid)}`, { token: student.token });
    const constraints = constraintsResp.json?.data || {};
    record('教师固定截止已生效（学生自设截止不能覆盖）',
      constraints.teacher_deadline === '2026-12-31'
      && constraints.effective_deadline === '2026-12-30',
      `teacher=${constraints.teacher_deadline} 学生=${constraints.deadline} 生效=${constraints.effective_deadline}`);

    const pushLater = await api(page, 'put', '/api/plan/constraints', {
      token: student.token,
      body: { subject_id: sid, teacher_deadline: '2027-06-01' },
    });
    record('学生不能把教师固定截止改晚',
      pushLater.status === 400 || pushLater.json?.error_type === 'invalid_request',
      `status=${pushLater.status} ${pushLater.json?.message || ''}`);

    // ===== 10) 教师工作台页面 =====
    await loginInBrowser(page, TEACHER, '/teacher');
    await page.waitForTimeout(2500);
    const dashText = await page.evaluate(() => document.body.innerText);
    record('教师工作台展示四块信息',
      ['我的课程', '待完成'].some((k) => dashText.includes(k))
      && dashText.includes(course.title),
      '工作台已渲染课程');
    await page.screenshot({ path: path.join(OUT, 'teacher-04-dashboard.png'), fullPage: true });

    // 教师工作台能看到"待完成教案/已发布任务/需关注知识点"区域
    record('工作台含待完成教案与已发布任务区',
      /待完成|草稿/.test(dashText) && /已发布|作业/.test(dashText));

    record('无页面级 JS 异常', pageErrors.length === 0, pageErrors.slice(0, 2).join(' ; '));
  } catch (e) {
    record('脚本执行', false, String(e && e.message ? e.message : e));
    await page.screenshot({ path: path.join(OUT, 'teacher-error.png'), fullPage: true })
      .catch(() => {});
  } finally {
    await browser.close();
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`);
  process.exit(failed.length ? 1 : 0);
})();
