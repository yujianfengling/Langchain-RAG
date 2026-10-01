/**
 * 语音输入端到端验收（2026-10-01 重写：回答朗读 TTS 已整体移除）
 *
 * 覆盖：
 *   1. 后端跑在隔离数据目录（闸门；否则退出码 2）
 *   2. 本地 ASR 状态可读；能力上报里不再有 TTS 字段
 *   3. 已移除的朗读端点（/api/audio/tts、/api/audio/speech、/api/audio/voices）返回 404
 *   4. 说话：录音 → 调用 /api/audio/transcribe → 文本进输入框（**不自动发送**）
 *   5. 确认后发送 → 文字回答正常流式显示
 *   6. **回答全过程不得发起任何 TTS 请求**（核心验收）
 *   7. 界面上不再有朗读入口（朗读按钮 / 自动朗读开关）
 *
 * 前置：
 *   1) 主后端（隔离数据目录）           :  $env:EDU_DATA_DIR=... python -m uvicorn api.app:app
 *   2) 本地语音服务（ASR）              :  python -m speech_service.server
 *   3) 前端 dev server                  :  cd frontend; npm run dev
 *   4) 一段 16k 单声道 WAV 充当假麦克风（**不使用真实麦克风**）
 *
 * 用法：
 *   node tests/e2e_voice_flow.js [baseUrl] [fakeMicWav] [viewW] [viewH]
 */

const fs = require('fs');
const { chromium } = require('playwright');

const BASE = process.argv[2] || 'http://localhost:3000';
const FAKE_MIC = process.argv[3] || 'D:/speech_stack/samples/fake_mic_question_16k.wav';
const VIEW_W = parseInt(process.argv[4] || '1440', 10);
const VIEW_H = parseInt(process.argv[5] || '1000', 10);
const API = process.env.E2E_API_BASE || 'http://127.0.0.1:8000';

const USERNAME = `voice_${Date.now().toString(36)}`;

const results = [];
function record(name, ok, detail) {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'} | ${name}${detail ? ' | ' + detail : ''}`);
}

async function requireBackendIsolated() {
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
    console.log('拒绝执行：后端不在隔离数据目录（本脚本会写入账号与学习记录）');
    console.log(`后端当前数据目录：${scope.dir_name}（正式目录）`);
    console.log('  $env:EDU_DATA_DIR="D:/dev/git_text_work_data"');
    console.log('  python -m uvicorn api.app:app --host 127.0.0.1 --port 8000');
    console.log('='.repeat(64));
    process.exit(2);
  }
  console.log(`[数据隔离] 后端数据目录：${scope.dir_name}（隔离，已确认）`);
}

(async () => {
  if (!fs.existsSync(FAKE_MIC)) {
    console.log(`缺少假麦克风音频：${FAKE_MIC}\n（本脚本不使用真实麦克风；请提供 16k 单声道 WAV 路径）`);
    process.exit(2);
  }
  await requireBackendIsolated();

  // 能力上报与被移除的端点
  const health = await (await fetch(`${API}/api/health`)).json();
  const caps = health?.data?.capabilities || {};
  record('本地 ASR 能力可读', !!caps.local_asr,
    JSON.stringify(caps.local_asr || {}).slice(0, 120));
  record('能力上报中不再有 TTS 字段', !caps.local_tts && !caps.mimo_tts,
    `local_tts=${JSON.stringify(caps.local_tts)}`);
  for (const [method, path] of [['POST', '/api/audio/tts'], ['POST', '/api/audio/speech'],
                                ['GET', '/api/audio/voices']]) {
    const r = await fetch(`${API}${path}`, { method });
    record(`已移除端点 ${path} 返回 404`, r.status === 404, `HTTP ${r.status}`);
  }

  const browser = await chromium.launch({
    headless: true,
    args: [
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      `--use-file-for-fake-audio-capture=${FAKE_MIC}`,
    ],
  });
  const context = await browser.newContext({ viewport: { width: VIEW_W, height: VIEW_H } });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));

  // 请求记录：既要抓到转写调用，也要证明"回答不触发 TTS"
  const transcribeCalls = [];
  const ttsCalls = [];
  const chatCalls = [];
  page.on('request', (req) => {
    const u = req.url();
    if (u.includes('/api/audio/transcribe')) transcribeCalls.push(u);
    if (/\/api\/audio\/(tts|speech|voices)/.test(u)) ttsCalls.push(u);
    if (u.includes('/api/chat/stream') || /\/api\/chat(\?|$)/.test(u)) chatCalls.push(u);
  });

  // 注册并登录
  await page.goto(`${BASE}/student`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  // 注意：.app-topbar 只在窄屏（≤900px）显示；宽屏下它里面的按钮虽然存在但不可见，
  // 必须先判可见性再点，否则会一直等待一个隐藏元素。
  const topLogin = page.locator('.app-topbar button:has-text("登录 / 注册")').first();
  if (await topLogin.count() && await topLogin.isVisible().catch(() => false)) {
    await topLogin.click();
  } else {
    await page.click('button:has-text("登录 / 注册")');
  }
  await page.click('button:has-text("还没有账号？去注册")');
  await page.fill('#username', USERNAME);
  await page.fill('#password', 'test-pass-123');
  await page.click('.ant-modal-footer button.ant-btn-primary');
  await page.waitForSelector('.ant-modal-content', { state: 'detached', timeout: 15000 });
  record('注册并登录成功', true, USERNAME);

  // 进入 AI 学习助手
  if (VIEW_W <= 900) {
    await page.locator('button[aria-label="打开导航菜单"]').first().click();
    await page.locator('.ant-drawer .ant-menu-item:has-text("AI学习助手")').first().click();
  } else {
    await page.click('.ant-menu-item:has-text("AI学习助手")');
  }
  await page.waitForTimeout(800);

  // 全新隔离库没有学科 → 自建一个并选中（否则发送按钮禁用，无法验证"回答不触发 TTS"）
  const SUBJECT_NAME = '语音验收学科';
  const token = await page.evaluate(() => localStorage.getItem('edu_user_token'));
  const created = await fetch(
    `${API}/api/subjects?subject_id=e2e_voice_subject&name=${encodeURIComponent(SUBJECT_NAME)}`,
    { method: 'POST', headers: { 'X-User-Token': token } });
  record('自建验收学科', created.status === 200, `HTTP ${created.status}`);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);
  const subjSel = page.locator('.ant-select').first();
  if (await subjSel.count()) {
    await subjSel.locator('.ant-select-selector').click();
    const opt = page.locator(
      `.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option:has-text("${SUBJECT_NAME}")`);
    if (await opt.count()) {
      await opt.first().click();
      record('已选中验收学科', true, SUBJECT_NAME);
    } else {
      record('已选中验收学科', false, '下拉里没有该学科');
    }
  }

  // 界面：不再有朗读入口
  const soundIcons = await page.locator('.anticon-sound').count();
  record('界面上已无朗读（喇叭）入口', soundIcons === 0, `喇叭图标数=${soundIcons}`);
  const autoReadText = await page.locator('text=/自动(语音回复|朗读)/').count();
  record('界面上已无"自动朗读/自动语音回复"开关', autoReadText === 0, `命中=${autoReadText}`);

  // 录音按钮
  const audioBtn = page.locator('button:has(.anticon-audio)').first();
  const hasMic = await audioBtn.count() > 0;
  const box = hasMic ? await audioBtn.boundingBox() : null;
  record('录音按钮存在且在可视区内',
    hasMic && !!box && box.y >= 0 && box.y + box.height <= VIEW_H + 1,
    box ? `y=${Math.round(box.y)} h=${Math.round(box.height)}` : '未找到录音按钮');

  if (hasMic) {
    const msgBefore = await page.locator('.ant-avatar').count();
    await audioBtn.click();                                            // 开始录音
    await page.waitForTimeout(2500);
    await page.locator('button:has(.anticon-audio)').first().click();  // 结束录音
    await page.waitForTimeout(5000);

    record('录音后调用了转写接口 /api/audio/transcribe', transcribeCalls.length > 0,
      transcribeCalls.slice(-1)[0] || '未捕获到请求');

    const typed = (await page.locator('textarea:visible').last().inputValue().catch(() => '')) || '';
    record('转写文本进入输入框', typed.trim().length > 0, `输入框=「${typed.slice(0, 40)}」`);
    const msgAfter = await page.locator('.ant-avatar').count();
    record('误识别不会自动提交问题', msgAfter === msgBefore, `${msgBefore} → ${msgAfter}`);

    await page.locator('textarea:visible').last().press('Enter');
    await page.waitForTimeout(15000);
    record('确认后发送问题（发起对话请求）', chatCalls.length > 0, chatCalls.slice(-1)[0] || '');

    const bodyText = await page.locator('body').innerText();
    record('文字回答正常流式显示', bodyText.length > 100, `页面文本长度=${bodyText.length}`);

    // 核心验收：回答全过程没有任何 TTS 请求
    record('回答全过程未发起任何 TTS 请求', ttsCalls.length === 0,
      ttsCalls.length ? `捕获到：${ttsCalls.slice(0, 3).join(' ; ')}` : '0 个');
  }

  record('页面无 JS 异常', pageErrors.length === 0, pageErrors.slice(0, 2).join(' ; '));

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error('脚本异常：', e); process.exit(1); });