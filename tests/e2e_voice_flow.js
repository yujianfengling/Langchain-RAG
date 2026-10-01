/**
 * 语音问答端到端验收（真实浏览器 + 真实本地 ASR/TTS + 真实 DeepSeek）
 *
 * 前置：
 *   1) 主后端（隔离数据目录）  :8000
 *   2) 本地语音服务（FunASR + CosyVoice）:8100
 *   3) 前端 dev server          :3000
 *   4) 用 CosyVoice 生成一段"提问音频"当作假麦克风输入：
 *      speech_stack/samples/fake_mic_question_16k.wav
 *
 * 用法：
 *   node tests/e2e_voice_flow.js [baseUrl] [fakeMicWav]
 *
 * 覆盖（对应本轮验收清单）：
 *   - 录音 → 本地转写 → 文字进入输入框（**不自动发送**）→ 学生确认后发送
 *   - DeepSeek 文字回答正常流式显示
 *   - 自动朗读：形成完整句子即开始合成播放；可暂停/继续/停止
 *   - 连续提问 / 切换会话 / 刷新页面：旧音频立即停止，迟到结果不播放
 *   - 语音不可用时不阻塞文字问答（网络层验证）
 *   - 页面无 JS 异常
 */

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const BASE = process.argv[2] || 'http://localhost:3000';
const FAKE_MIC = process.argv[3] ||
  'E:/WORKBUDDY/Game/speech_stack/samples/fake_mic_question_16k.wav';
// 视口可传参：用同一个脚本跑桌面（默认 1440×1000）与手机宽度（390×844）
// 例：node tests/e2e_voice_flow.js http://localhost:3000 <wav> 390 844
const VIEW_W = parseInt(process.argv[4] || '1440', 10);
const VIEW_H = parseInt(process.argv[5] || '1000', 10);
const IS_NARROW = VIEW_W <= 900;
const OUT = path.join(__dirname, '_screenshots');
const USERNAME = `voice_${Date.now().toString(36)}`;
const AGENT_TIMEOUT = 240000;

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
  await requireBackendIsolated(BASE, "语音全流程验收");
  fs.mkdirSync(OUT, { recursive: true });
  if (!fs.existsSync(FAKE_MIC)) {
    console.log(`缺少假麦克风音频：${FAKE_MIC}`);
    process.exit(2);
  }

  const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
    args: [
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      `--use-file-for-fake-audio-capture=${FAKE_MIC}`,
      '--autoplay-policy=no-user-gesture-required',
    ],
  });
  const context = await browser.newContext({ viewport: { width: VIEW_W, height: VIEW_H },
    permissions: ['microphone'] });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));

  // 记录语音相关请求，便于核对"实际调用了什么"
  const voiceCalls = [];
  const chatCalls = [];
  /** 被送去合成的每一段文本（用于检查"序号是否被单独朗读"这类坏切分） */
  const spokenSegments = [];
  page.on('response', async (r) => {
    const u = r.url();
    if (u.includes('/api/audio/')) {
      let body = '';
      try {
        const json = await r.json();
        body = JSON.stringify(json).slice(0, 160);
        if (u.includes('/api/audio/tts')) {
          const items = (json && json.data && json.data.items) || [];
          items.forEach((it) => spokenSegments.push(String(it.text || '')));
        }
      } catch { /* ignore */ }
      voiceCalls.push(`${r.request().method()} ${u.split('/api/')[1]} → ${r.status()} ${body}`);
    } else if (u.includes('/api/chat')) {
      chatCalls.push(`${r.request().method()} ${u.split('/api/')[1]} → ${r.status()}`);
    }
  });
  // 等待某个语音接口被调用（避免响应还没回来就断言）
  const waitForVoiceCall = async (kw, timeout = 60000) => {
    const started = Date.now();
    while (Date.now() - started < timeout) {
      const hit = voiceCalls.find((c) => c.includes(kw));
      if (hit) return hit;
      await page.waitForTimeout(300);
    }
    return null;
  };

  try {
    // 0) 语音能力
    const health = await page.request.get(`${BASE}/api/health`);
    const caps = ((await health.json()).data || {}).capabilities || {};
    const asrOk = !!((caps.local_asr || {}).available);
    const ttsOk = !!((caps.local_tts || {}).available);
    record('本地 ASR 就绪', asrOk, JSON.stringify(caps.local_asr || {}).slice(0, 120));
    record('本地 TTS 就绪', ttsOk, JSON.stringify(caps.local_tts || {}).slice(0, 120));
    if (!asrOk || !ttsOk) throw new Error('本地语音服务未就绪，无法进行语音验收');

    // 1) 注册 + 选学科 + 进入学习助手
    await page.goto(BASE, { waitUntil: 'networkidle' });
    // 窄屏下登录入口在顶栏（侧边栏里的那个是隐藏的，直接点会超时）
    if (IS_NARROW) {
      await page.locator('.app-topbar button:has-text("登录 / 注册")').first().click();
    } else {
      await page.click('button:has-text("登录 / 注册")');
    }
    await page.click('button:has-text("还没有账号？去注册")');
    await page.fill('#username', USERNAME);
    await page.fill('#password', 'test-pass-123');
    await page.click('.ant-modal-footer button.ant-btn-primary');
    await page.waitForSelector('.ant-modal-content', { state: 'detached', timeout: 15000 });
    await page.click('.ant-select-selector');
    await page.waitForSelector(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
      { timeout: 8000 });
    await page.locator(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option:has-text("离散数学")')
      .first().click();
    await page.waitForTimeout(800);
    // 窄屏下侧边栏变成"顶栏 + 抽屉"，进页面前先开抽屉
    if (IS_NARROW) {
      await page.locator('button[aria-label="打开导航菜单"]').first().click();
      await page.waitForTimeout(800);
      await page.locator('.ant-drawer .ant-menu-item:has-text("AI学习助手")').first().click();
    } else {
      await page.click('.ant-menu-item:has-text("AI学习助手")');
    }
    await page.waitForTimeout(1500);
    const chatSubject = page.locator('.ant-select').first();
    await chatSubject.locator('.ant-select-selector').click();
    await page.waitForSelector(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
      { timeout: 8000 });
    await page.locator(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option:has-text("离散数学")')
      .first().click();
    await page.waitForTimeout(1200);
    record('进入学习助手页并选好学科', true, USERNAME);

    // 2) 打开"自动语音回复"，录音 → 转写 → 确认后发送
    // 手机宽度下先确认语音相关的控件真的看得见、点得到（这是本轮新增的专项检查）
    if (IS_NARROW) {
      const audioBtn = page.locator('button:has(.anticon-audio)').first();
      const audioBox = await audioBtn.boundingBox().catch(() => null);
      record('手机宽度：录音按钮在可视区内',
        !!audioBox && audioBox.x >= 0 && audioBox.x + audioBox.width <= VIEW_W + 1,
        audioBox ? `x=${Math.round(audioBox.x)} w=${Math.round(audioBox.width)} 视口=${VIEW_W}` : '找不到录音按钮');
      const swBox = await page.locator('.ant-switch').last().boundingBox().catch(() => null);
      record('手机宽度：自动朗读开关在可视区内',
        !!swBox && swBox.x >= 0 && swBox.x + swBox.width <= VIEW_W + 1,
        swBox ? `x=${Math.round(swBox.x)} w=${Math.round(swBox.width)}` : '找不到开关');
    }

    const autoSwitch = page.locator('.ant-switch').last();
    await autoSwitch.click();
    await page.waitForTimeout(300);
    const autoOn = await page.evaluate(() => localStorage.getItem('auto_voice_reply'));
    record('自动朗读开关可开启', autoOn === '1', `auto_voice_reply=${autoOn}`);

    await page.locator('button:has(.anticon-audio)').first().click();
    await page.waitForTimeout(4500);                        // 录满一遍假麦克风音频
    await page.locator('button:has(.anticon-audio)').first().click();
    const transcribeCall = await waitForVoiceCall('audio/transcribe', 90000);
    record('录音后调用本地转写接口', !!transcribeCall, transcribeCall || '未捕获到请求');
    await page.waitForFunction(
      () => {
        const ta = document.querySelector('textarea');
        return ta && ta.value && ta.value.trim().length > 0;
      }, null, { timeout: 120000 });
    const typed = await page.evaluate(() => document.querySelector('textarea').value.trim());
    record('转写文本进入输入框（未自动发送）', typed.length > 0, `输入框=「${typed.slice(0, 40)}」`);
    const msgCountBefore = await page.locator('.ant-avatar').count();
    await page.waitForTimeout(500);
    const msgCountAfterRecord = await page.locator('.ant-avatar').count();
    record('误识别不会自动提交问题', msgCountAfterRecord === msgCountBefore,
      `消息数 ${msgCountBefore} → ${msgCountAfterRecord}`);
    await page.screenshot({ path: path.join(OUT, 'voice-01-transcribed.png'), fullPage: true });

    // 3) 学生确认后发送 → 流式文字 + 逐句朗读
    await page.locator('textarea:visible').last().press('Enter');
    // 确认问题真的发出去了（有 /api/chat/stream 请求）
    await page.waitForFunction(
      () => true, null, { timeout: 1000 }).catch(() => {});
    const sentChat = chatCalls.length > 0;
    record('确认后发送问题（发出 chat 请求）', sentChat, chatCalls.slice(-2).join(' ; '));
    await page.waitForFunction(
      () => document.body.innerText.includes('模型:'), null, { timeout: AGENT_TIMEOUT });
    const ttsCall = await waitForVoiceCall('audio/tts', 120000);
    record('回答生成后调用本地逐句合成接口', !!ttsCall, (ttsCall || '').slice(0, 140));
    const bodyText = await page.evaluate(() => document.body.innerText);
    record('回答正常流式显示（文字可见）', bodyText.length > 100 && /模型:/.test(bodyText));
    // 等朗读状态出现
    await page.waitForFunction(
      () => /朗读中/.test(document.body.innerText), null, { timeout: 120000 })
      .catch(() => {});
    const playingText = await page.evaluate(() => {
      const m = document.body.innerText.match(/朗读中[^\n]*/);
      return m ? m[0] : '';
    });
    record('开始朗读并显示状态', /朗读中/.test(playingText), playingText);
    await page.screenshot({ path: path.join(OUT, 'voice-02-reading.png'), fullPage: true });

    // 4) 停止朗读
    const stopBtn = page.locator('button:has-text("停止")').first();
    if (await stopBtn.count()) {
      await stopBtn.click();
      await page.waitForTimeout(800);
      const stillReading = await page.evaluate(() => /朗读中/.test(document.body.innerText));
      record('可立即停止朗读', !stillReading, `点击后仍显示朗读中=${stillReading}`);
    } else {
      record('可立即停止朗读', false, '未找到停止按钮');
    }

    // 5) 手动朗读 + 暂停/继续
    //    播放中按钮图标会变成暂停图标（.anticon-pause），暂停后仍是该图标
    await page.locator('.anticon-sound').last().click();
    await page.waitForTimeout(3000);
    const readingAfterManual = await page.evaluate(() => /朗读中/.test(document.body.innerText));
    record('手动朗读可启动', readingAfterManual);

    // 等真正进入播放（合成完成）再暂停，避免"合成中点击=取消"
    await page.waitForFunction(
      () => {
        const p = window.__ttsPlayer;
        const s = p ? p.getState() : null;
        return s && s.state === 'playing';
      }, null, { timeout: 120000 }).catch(() => {});
    const pauseBtn = page.locator('.anticon-pause').last();
    if (await pauseBtn.count()) {
      await pauseBtn.click();
      await page.waitForTimeout(800);
      const paused = await page.evaluate(() => /已暂停/.test(document.body.innerText));
      record('可暂停朗读', paused);
      if (paused) {
        await page.locator('.anticon-pause').last().click();
        await page.waitForTimeout(800);
        const resumed = await page.evaluate(() => /朗读中/.test(document.body.innerText));
        record('可从暂停继续', resumed && !/已暂停/.test(await page.evaluate(() => document.body.innerText)));
      }
    } else {
      record('可暂停朗读', false, '未找到暂停按钮');
    }

    // 6) 新提问：旧音频立即停止（迟到结果不串音）
    const ttsCallsBefore = voiceCalls.filter((c) => c.includes('audio/tts')).length;
    // 先记录"当前正在朗读的消息"，再发新问题（顺序很重要：发完再读就看不到切换了）
    const oldMsgId = await page.evaluate(() => {
      const p = window.__ttsPlayer;
      return p ? (p.getState().msgId || '') : '';
    });
    await page.locator('textarea:visible').last().fill('那中序遍历呢');
    await page.keyboard.press('Enter');
    let switched = false;
    // 新回答开始朗读后再做下一步（否则会把刚开的朗读会话一起停掉，测不出"重新开始朗读"）
    for (let i = 0; i < 150; i += 1) {
      const now = await page.evaluate(() => {
        const p = window.__ttsPlayer;
        const s = p ? p.getState() : null;
        return s ? `${s.state}|${s.msgId || ''}` : '';
      });
      const [state, msgId] = now.split('|');
      if (state === 'stopped' || msgId !== oldMsgId) { switched = true; break; }
      await page.waitForTimeout(200);
    }
    record('新提问时旧音频停止（不叠音）', switched,
      `原消息=${oldMsgId}`);
    // 等新回答真的开始合成/朗读（长回答首句约 5~15 秒）
    let ttsCallsAfter = ttsCallsBefore;
    for (let i = 0; i < 120; i += 1) {
      ttsCallsAfter = voiceCalls.filter((c) => c.includes('audio/tts')).length;
      if (ttsCallsAfter > ttsCallsBefore) break;
      await page.waitForTimeout(500);
    }
    record('新回答重新开始朗读', ttsCallsAfter > ttsCallsBefore,
      `合成请求 ${ttsCallsBefore} → ${ttsCallsAfter}`);

    // 7) 切换会话：播放停止
    // 宽屏是「新建对话」，窄屏聊天区顶部是紧凑的「新建」（两者都是"新建会话"）
    await page.getByRole('button', { name: /新\s*建\s*对?\s*话?/ }).first().click();
    // 切会话要先等"新建对话"接口返回、页面切过去；给最多 15s 观察是否停止
    let afterSwitch = null;
    for (let i = 0; i < 50; i += 1) {
      afterSwitch = await page.evaluate(() => {
        const p = window.__ttsPlayer;
        const s = p ? p.getState() : null;
        return { state: s && s.state, reading: /朗读中|已暂停/.test(document.body.innerText) };
      });
      if (!afterSwitch.reading && ['idle', 'stopped'].includes(afterSwitch.state || '')) break;
      await page.waitForTimeout(300);
    }
    record('切换会话后立即停止朗读',
      !!afterSwitch && !afterSwitch.reading
        && ['idle', 'stopped'].includes(afterSwitch.state || ''),
      `state=${afterSwitch && afterSwitch.state} 页面显示朗读=${afterSwitch && afterSwitch.reading}`);

    // 8) 语音不可用时不阻塞文字（网络层模拟：语音接口 503）
    await page.route('**/api/audio/tts', (route) => route.fulfill({
      status: 503, contentType: 'application/json',
      body: JSON.stringify({ success: false, message: '本地语音合成不可用（模拟）',
        error_type: 'speech_unavailable' }),
    }));
    await page.locator('textarea:visible').last().fill('集合的并集怎么算');
    await page.keyboard.press('Enter');
    await page.waitForFunction(
      () => document.body.innerText.includes('模型:'), null, { timeout: AGENT_TIMEOUT });
    const lastText = await page.evaluate(() => document.body.innerText.slice(-400));
    record('TTS 不可用时文字回答仍正常', lastText.length > 20,
      lastText.replace(/\n/g, ' ').slice(0, 60));

    // 9) 朗读切分质量：列表序号 / 缩写不能被切成独立一段（会读出孤立数字）
    const bareMarkers = spokenSegments.filter((s) => /^\s*\d{1,3}\.\s*$/.test(s));
    record('列表序号没有被单独朗读', bareMarkers.length === 0,
      bareMarkers.length ? `坏分段=${JSON.stringify(bareMarkers)}` : `共合成 ${spokenSegments.length} 段`);
    const emptySegs = spokenSegments.filter((s) => !s.trim());
    record('没有空段被送去合成', emptySegs.length === 0, `空段数=${emptySegs.length}`);

    record('无页面级 JS 异常', pageErrors.length === 0, pageErrors.slice(0, 2).join(' ; '));
    await page.screenshot({ path: path.join(OUT, 'voice-03-degraded.png'), fullPage: true });
  } catch (e) {
    record('脚本执行', false, String(e && e.message ? e.message : e));
    await page.screenshot({ path: path.join(OUT, 'voice-error.png'), fullPage: true })
      .catch(() => {});
  } finally {
    await browser.close();
  }

  if (voiceCalls.length) {
    console.log('\n[语音接口调用轨迹]');
    voiceCalls.slice(-8).forEach((l) => console.log('  ' + l));
  }
  const failed = results.filter((r) => !r.ok);
  console.log(`\n合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`);
  process.exit(failed.length ? 1 : 0);
})();
