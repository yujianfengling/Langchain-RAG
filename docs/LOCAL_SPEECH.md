# 本地语音栈（ASR + TTS）选型、安装与验收

> 更新时间：2026-09-26
> 目标环境：Windows 笔记本 · RTX 4060 Laptop（8 GB 显存）· 32 GB 内存 · Python 3.11
> 本文不含任何真实密钥。模型文件、缓存、参考音频、录音均在仓库之外（`E:/WORKBUDDY/Game/speech_stack`）。

---

## 一、结论（选型结果）

| 能力 | 选定方案 | 版本/规模 | 说明 |
|---|---|---|---|
| **语音识别（ASR）** | **FunASR paraformer-zh**（+ fsmn-vad 断句 + ct-punc 标点） | 约 1.0 GB | 中文准确率高、自带标点（学生可直接在输入框检查修改），纯 pip 安装无需编译 |
| **语音合成（TTS）** | **CosyVoice-300M-SFT** | 约 1.2 GB（仓库 5.4 GB 含 fp16/fp32 双份） | 中文自然、数字/英文混读稳定、**自带 7 个预置音色**（无需参考音频，无真人音色授权问题） |
| 备用语音（可选提供方） | MiMo ASR/TTS | — | 代码保留；会员到期时如实报错，不再是默认路径 |

**未选 GPT-SoVITS**，原因见第三节（同机同文本实测）。

---

## 二、本机实测环境

```
GPU：NVIDIA GeForce RTX 4060 Laptop GPU（8188 MiB），驱动 591.86
torch 2.3.1+cu121（独立 venv，不影响主项目 torch 2.6.0+cu124）
主项目 venv 不安装任何语音依赖（模型推理全在独立进程）
```

显存占用（服务预热后）实测：ASR 约 2.0 GB + TTS 约 1.7 GB，**合计约 3.7 GB / 8 GB**，
与文本模型（DeepSeek 为云端调用，不占本地显存）共存无压力。

---

## 三、TTS 选型实测：CosyVoice vs GPT-SoVITS

同一批教学文本（中文课程问题 / 中英混读 / 数字与单位 / 长回答），
指标 = 首段可播放等待时间、合成耗时、RTF（合成耗时 ÷ 音频时长）、
以及**可懂度**：把合成音频再送回本地 ASR，与"实际朗读文本"比对字符错误率（CER）。
复现脚本：`python tests/speech_bakeoff.py --gptsovits`（样本与报告见 `speech_stack/samples/`）。

> 数据口径说明：下表是一次完整、无其他 GPU 占用干扰的运行（2026-09-26，报告
> `speech_stack/samples/bakeoff_report.json`）。两个引擎都是采样生成，**同文本不同次运行结果会小幅波动**，
> 因此另附上一次运行的取值区间。CER 有两个口径：
> `CER` = 转写回读 vs **实际朗读文本**（归一化后，"有没有读错我们想读的内容"）；
> `CER_vs_orig` = 转写回读 vs **原始 Markdown/数字写法**（差值主要来自有意的读法转换，如 `3.14 → 三点一四`）。

### 3.1 CosyVoice-300M-SFT（生产路径：含朗读文本归一化）

| 文本 | 合成段数 | 首段等待 | 合成总耗时 | 音频时长 | RTF | **CER** | CER_vs_orig |
|---|---|---|---|---|---|---|---|
| 中文课程问题 | 1/1（不漏句） | 3.53 s | 3.65 s | 3.60 s | 1.01 | **0.00** | 0.00 |
| 中英混读（GPU/RTX 4060/DeepSeek） | 1/1 | 6.21 s | 6.25 s | 7.23 s | 0.86 | **0.175** | 0.225 |
| 数字与单位（3.14 倍 / 8GB / 1.5 秒） | 1/1 | 4.42 s | 4.45 s | 5.49 s | 0.81 | **0.00** | 0.32 |
| 长回答（6 句，200+ 字） | 6/6（不漏句） | 3.19 s | 22.2 s | 25.8 s | 0.86 | **0.069** | 0.069 |

- RTF ≈ 0.81–1.01 → **约实时或更快**，配合"逐句合成 + 边播边合成"，长回答不会卡顿
- 上一次运行（12:30）取值：首段 3.07–6.26 s、RTF 0.83–0.97、CER 0.00/0.10/0.074/0.053
  → 两次合计 CER 落在 **0.00–0.175**，稳定的只有中英文混读一项略有同音字抖动
- 显存：加载后 1.7 GB，峰值 1.86 GB；冷启动（已下载模型）18.8 s（TTS）+ 48.9 s（ASR）

### 3.2 GPT-SoVITS v2（最小可运行验证）

| 文本 | 合成总耗时 | 音频时长 | RTF | CER（本次 / 上次） |
|---|---|---|---|---|
| 中文课程问题 | 8.13 s | 3.66 s | 2.22 | 0.00 / 0.00 |
| 中英混读 | 6.20 s | 8.10 s | 0.77 | **0.35 / 0.50** |
| 数字与单位 | 1.65 s | 5.38 s | 0.31 | **0.32 / 0.40** |
| 长回答 | 6.12 s | 25.98 s | 0.24 | **0.12 / 0.16** |

识别回来的文本（节选）：`我们使用GPU和RTX四零六零显卡，运行deep CKGPTT sovi ITS模型`、
`显存占用约八GB`（丢字）、长回答中段内容错乱 —— 说明在**英文缩写、单位与长句**上读出内容被明显破坏。
显存峰值 1.26 GB，加载 17.8 s。

### 3.3 淘汰 GPT-SoVITS 的四条具体原因

1. **读出准确度明显更差且不稳定**：两次运行中英混读 CER 0.35/0.50、数字单位 0.32/0.40、
   长回答 0.12/0.16，均显著高于 CosyVoice 的 0.175/0.00/0.069。
2. **安装与重复启动成本高**：4.5 GB 预训练包 + **逐次暴露缺失依赖**（openai-whisper → pyarrow →
   pyworld → pkg_resources → opencc → fast-langdetect → split_lang → cn2an → jieba_fast →
   nltk 数据 → imageio-ffmpeg），且会把 numpy 升到 2.x、lightning 升到 2.6，
   **与 CosyVoice 的版本固定冲突**（本机需回退 numpy 1.26.4 才能同时可用）。
3. **必须有参考音频**：GPT-SoVITS 是音色克隆型 TTS，首版要合法可分发音色就得自备参考音频
   （本次评测用的参考音频还是用 CosyVoice 合成的）。CosyVoice-300M-SFT 自带预置音色，无此问题。
4. **推理路径不稳健**：未补齐 NLTK 数据时，中英混读/数字/长文本直接抛
   `LookupError: averaged_perceptron_tagger_eng`，完全不产出音频（首轮实测即如此）。

> 说明：GPT-SoVITS 在**中文单句**上质量不差（CER 0.00，本次 RTF 2.22 慢于实时），
> 若将来要做"教师音色"定制，可作为二阶段方案再评估；本轮按"可安全分发 + 稳定"优先选 CosyVoice。
>
> 另需注意：**GPU 争用会严重污染测量**。曾在 GPT-SoVITS 与 CosyVoice 同时推理的情况下测到
> CosyVoice RTF 8.17、GPT-SoVITS RTF 8.11（本机 8 GB 显存），此时数据不可用；
> 上表均为"同一时刻只有一个引擎在推理"的干净测量。

---

## 四、安装与启动（Windows，可重复执行）

### 4.1 一次性安装（独立 venv，不污染主项目环境）

```powershell
# 1) 语音栈目录（仓库之外，已被 .gitignore 忽略）
mkdir E:\WORKBUDDY\Game\speech_stack
cd E:\WORKBUDDY\Game\speech_stack

# 2) 独立虚拟环境
python -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip

# 3) 固定版本依赖（torch 2.3.1+cu121 为 CosyVoice 官方要求版本）
.\venv\Scripts\python.exe -m pip install "torch==2.3.1" "torchaudio==2.3.1" `
  --index-url https://download.pytorch.org/whl/cu121
.\venv\Scripts\python.exe -m pip install "numpy==1.26.4" "transformers==4.51.3" `
  modelscope HyperPyYAML conformer diffusers lightning onnxruntime librosa soundfile `
  wetext inflect x-transformers omegaconf hydra-core networkx gdown protobuf rich wget matplotlib `
  "pyarrow==18.1.0" pyworld "setuptools<81" openai-whisper funasr
# ffmpeg（Windows 无系统 ffmpeg 时）
.\venv\Scripts\python.exe -m pip install imageio-ffmpeg
```

### 4.2 模型下载

```powershell
# CosyVoice 代码（含 Matcha-TTS 子模块，必须单独 clone）
cd E:\WORKBUDDY\Game\speech_stack
git clone --depth 1 https://github.com/FunAudioLLM/CosyVoice.git
cd CosyVoice
git clone --depth 1 https://github.com/shivammehta25/Matcha-TTS.git third_party/Matcha-TTS
# 预置音色 SFT 模型（用 ModelScope）
..\venv\Scripts\python.exe -c "from modelscope import snapshot_download; snapshot_download('iic/CosyVoice-300M-SFT', local_dir='pretrained_models/CosyVoice-300M-SFT')"

# ASR 模型（FunASR 首次加载会自动下载到 MODELSCOPE_CACHE，约 1 GB）
$env:MODELSCOPE_CACHE="E:\WORKBUDDY\Game\speech_stack\models\asr"
..\venv\Scripts\python.exe -c "from funasr import AutoModel; AutoModel(model='paraformer-zh', vad_model='fsmn-vad', punc_model='ct-punc', device='cuda:0', disable_update=True, hub='ms')"
```

### 4.3 启动（每次开机）

```powershell
# 终端一：本地语音服务（模型只加载一次，常驻显存）
cd E:\WORKBUDDY\Game\git_text
$env:SPEECH_STACK_DIR="E:\WORKBUDDY\Game\speech_stack"
$env:SPEECH_WARMUP_ON_START="1"
E:\WORKBUDDY\Game\speech_stack\venv\Scripts\python.exe -m speech_service.server
# 就绪判断：curl http://127.0.0.1:8100/health  → engines.asr.state=ready 且 engines.tts.state=ready

# 终端二：主后端（语音默认走本地；SPEECH_BACKEND=auto）
python -m uvicorn api.app:app --host 127.0.0.1 --port 8000

# 终端三：前端
cd frontend && npm run dev
```

### 4.4 关键环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `SPEECH_BACKEND` | `auto` | `local`（只用本地）/ `mimo`（只用 MiMo）/ `auto`（本地优先，本地失败才试 MiMo） |
| `LOCAL_SPEECH_URL` | `http://127.0.0.1:8100` | 本地语音服务地址 |
| `LOCAL_SPEECH_TIMEOUT` | `120` | 主后端调用语音服务的超时（秒） |
| `SPEECH_QUEUE_WAIT_SECONDS` | `20` | ASR 排队等待上限（超出返回"服务正忙"） |
| `SPEECH_TTS_QUEUE_WAIT_SECONDS` | `90` | TTS 排队等待上限（长回答多句，给更宽窗口） |
| `SPEECH_STACK_DIR` | 仓库同级 `speech_stack` | 模型根目录 |
| `SPEECH_TTS_VOICE` | `中文女` | 默认音色（可选：中文男 / 日语男 / 粤语女 / 英文女 / 英文男 / 韩语女） |
| `SPEECH_DEVICE` | `auto` | `cuda` / `cpu` |

---

## 五、服务接口（`speech_service/server.py`，独立进程）

| 接口 | 说明 |
|---|---|
| `GET /health` | 引擎状态 / 设备 / 显存；**推理在线程池执行**，合成过程中也能秒回 |
| `POST /warmup` | 预热（幂等） |
| `GET /voices` | 可用音色（CosyVoice 预置音色） |
| `POST /asr` | multipart 音频 → 文本（含标点） |
| `POST /tts` | 单段文本 → WAV(base64) |
| `POST /tts/segments` | 多段文本 → 逐段 WAV(base64)，**顺序由服务端保证**；客户端断开即停止后续合成 |

主后端对外接口（`api/routes/audio.py`）：`/api/audio/transcribe`、`/api/audio/tts`（逐句朗读主路径）、
`/api/audio/speech`（兼容旧调用）、`/api/audio/voices`、`/api/audio/warmup`、`/api/audio/status`。

---

## 六、朗读文本处理（`services/tts_text.py`）

| 处理 | 行为 |
|---|---|
| Markdown / 标题 / 列表符号 | 去掉标记，保留正文 |
| 代码块 / 表格 | 替换为"这里有一段代码/一张表格，请看屏幕" |
| 公式（`$...$`、`\frac{}{}`、符号密集句） | 替换为"这里有一个公式，请看屏幕上的公式"，**不逐字符朗读** |
| 数字与单位 | `3.14 → 三点一四`、`95% → 百分之九十五`、`8GB → 八 G B`、`1.5GB/s → 一点五 G B 每秒`、`65.5℃ → 六十五点五摄氏度` |
| 型号 / 年份 | `RTX 4060 → R T X 四零六零`、`2026年 → 二零二六年` |
| 英文缩写 | `GPU → G P U`、`API → A P I` |
| 句子切分 | 按句切段；**拼接结果等于原文（不漏字、不重复）**；超长句在逗号等次级边界续切；列表序号（`3.`）与后文合并 |
| 超长截断 | 只有调用方显式传 `max_chars` 才截断，且返回 `truncated / omitted_chars`，**不静默丢弃** |

> 背景：CosyVoice 自带的文本正则化前端（wetext）在本机会静默失效（日志 `no frontend is available`），
> 此时 `3.14 倍` 会被读成 `3。14倍` 之类的乱码。因此在**业务侧**做了这层归一化，并用 ASR 回读验证：
> "数字与单位"这条的 CER 为 **0.00**（读出的内容与归一化后文本完全一致），而 `CER_vs_orig` 为 **0.32** ——
> 这个差值正说明朗读时确实把 `3.14 / 8GB / 1.5 秒` 转换成了中文读法，且转换后读对了。
> 中英混读同理：CER 0.175 / `CER_vs_orig` 0.225，差值是 `GPU → G P U` 这类拼读转换。

---

## 七、验收结果（真实运行）

| 验收 | 命令 | 结果 |
|---|---|---|
| 语音全链路（真实 Chrome + 假麦克风 + 真实 ASR/TTS + DeepSeek） | `node tests/e2e_voice_flow.js http://localhost:3000` | **22/22** |
| 选型实测与样本（含 GPT-SoVITS 对比） | `python tests/speech_bakeoff.py --gptsovits` | 见第三节 |
| 自动测试（含朗读文本/切分/归一化） | `python -m pytest tests/ -q` | **485 passed** |
| 原有回归（学习闭环 / Agent / 默认模型） | 见 `PROJECT_STATUS.md` | 全绿，正式库 6 个 DB 零写入（逐库 SHA256 + 行数比对一致） |

测得的关键体验数字（本机）：

- ASR：4.4 s 录音 → **0.2–1.2 s** 出文字（同一 4.21 s 音频连续 3 次转写结果完全一致，耗时 212–258 ms），
  识别后进入输入框，**不自动发送**
- TTS：短句首段 **3.1–4.7 s** 可播放；长回答逐句合成，第一句先播、后续边合成边排队
- 端到端（确认问题 → 听到第一句）：约 **8–15 s**（含 DeepSeek 流式首句时间）

### 验收中发现并已修复的问题

1. **列表序号被单独朗读**（真机日志实证）：流式切分把 `要点：\n3.` 中的 `3.` 切成独立一段送去合成，
   音频里会读出孤立的数字（日志中确实出现 `items[0].text == "3."`）。已在
   `frontend/src/utils/ttsPlayer.ts::takeCompleteSentences` 增加"列表序号不算句子边界"的判定，
   并把缩写（`e.g.` / `etc.` / 单字母加点）一并排除；后端 `services/tts_text` 侧也新增断言
   （`tests/test_tts_text.py::test_no_segment_is_bare_list_marker`），E2E 增加"列表序号没有被单独朗读"检查项。
2. **E2E 脚本残留变量**：上一轮移除调试日志时漏删引用，脚本在打印结果前会抛 `ReferenceError`
   （表现为"没有输出、退出码非 0"）。已修复并重跑通过。
3. **行内引用标记被读成"一"**：`[1]` 这类来源序号会被数字归一化变成 `[一]` 并读出来。
   已在 `strip_markdown` 中直接移除（`[1]`/`[1,2]`/`[1-3]`/`【1】` 等），并加测试
   （`test_inline_citation_markers_not_read`）。

### 已知问题与限制（如实记录）

1. **主观自然度未经人工试听**：本轮由自动化验证（ASR 回读 CER、时长、无漏句、无截断）替代主观试听，
   样本已生成在 `speech_stack/samples/*.wav`，请人工试听后再定是否调音色/语速。
2. **ASR 同音字**：如"根、左、右"可能识别为"跟左右"。已通过"转写结果先入输入框、学生确认后发送"缓解。
   （验收脚本里转写结果尾部多出的一个字，是假麦克风设备把 4.21 s 样本循环播放导致录进了下一轮开头，
   属测试装置产物：同一文件直接调用 ASR 连续 3 次结果完全一致且无多余字。）
3. **RTX 4060 Laptop 8 GB**：CosyVoice 与 ASR 常驻约 3.7 GB；**不要同时跑第二个大模型**
   （实测：与 GPT-SoVITS 同时推理时两者 RTF 都恶化到 8 以上，会触发超时）。
4. **首次启动慢**：模型加载约 49 s（ASR）+ 19 s（TTS）；服务启动即预热，就绪前 `/health` 会显示 `loading`。
5. 语音服务当前为**单机单实例**，未做多用户并发调度（推理按类型串行）；
   8 GB 显存下不建议提高并发。
6. **停顿与语速未调优**：当前按后端句读切分自然停顿，未做音色/语速个性化（需人工试听后决定）。

## 九、手机宽度下的语音实测（2026-09-27）

> 脚本：`tests/e2e_voice_flow.js`（同一脚本支持传视口参数）
> 命令：`node tests/e2e_voice_flow.js http://localhost:3000 <假麦克风wav> 390 844`

**结果：手机宽度 24/24 通过；桌面 1440×1000 对照 22/22 通过。**

| 检查项 | 手机宽度（390×844） |
|---|---|
| 录音按钮在可视区内 | ✅ x=24 w=38（视口 390） |
| 自动朗读开关在可视区内 | ✅ x=24 w=34 |
| 录音 → 本地转写（FunASR） | ✅ 200，延迟 0.2–0.6 秒，转写文本正确进入输入框 |
| 误识别不会自动提交 | ✅ 消息数不变，需学生确认后发送 |
| 回答 → 本地逐句合成（CosyVoice） | ✅ 200 |
| 逐句朗读 / 立即停止 / 手动朗读 / 暂停 / 继续 | ✅ 全部通过 |
| 新提问时旧音频停止、新回答重新朗读 | ✅ 不叠音 |
| 切换会话立即停止朗读 | ✅ |
| TTS 不可用时文字问答仍正常（降级） | ✅ |
| 朗读切分质量（序号/空段） | ✅ 无空段、序号未被单独朗读 |

### 这次**没有**验证的（不要当成已验证）

- **不是真机**：这是「移动宽度 + Chromium + 假麦克风音频文件」的浏览器实测，**没有用真实手机、真实麦克风、真实扬声器**。
- 未验证移动浏览器差异（iOS Safari / Android Chrome）、麦克风权限弹窗、移动端自动播放策略、锁屏或切后台后的播放行为、蓝牙耳机路由、4G/Wi-Fi 弱网。
- **音质未由人耳验收**：程序只验证了链路与切分，人耳试听仍是「未进行」。

### 运维提醒（实测遇到）

- 连续多次发起合成时，本地语音服务会排队，实测出现过
  `语音合成不可用：本地语音服务失败（语音服务正忙（等待超过 20 秒）…）`，伴随 `POST /api/audio/tts → 502`。
  同一批检查在服务空闲后重跑即全部通过 → **属于并发/排队问题，不是手机端缺陷**。
- 该 502 的错误文案里还提到 MiMo 语音回退失败（外部凭证不可用）。**回退链路当前不可用，不要依赖它**；
  主链路（本地 FunASR + CosyVoice）正常。
- 试用时请**避免多人同时点朗读**，一次只让学生点一次。
