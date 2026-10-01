# 本地语音栈（ASR）选型、安装与验收

> 更新时间：2026-09-26
> 目标环境：Windows 笔记本 · RTX 4060 Laptop（8 GB 显存）· 32 GB 内存 · Python 3.11
> 本文不含任何真实密钥。模型文件、缓存、参考音频、录音均在仓库之外（`E:/WORKBUDDY/Game/speech_stack`）。

---

## 一、结论（选型结果）

| 能力 | 选定方案 | 版本/规模 | 说明 |
|---|---|---|---|
| **语音识别（ASR）** | **FunASR paraformer-zh**（+ fsmn-vad 断句 + ct-punc 标点） | 约 1.0 GB | 中文准确率高、自带标点（学生可直接在输入框检查修改），纯 pip 安装无需编译 |
| 备用语音（可选提供方） | MiMo ASR | — | 代码保留；会员到期时如实报错，不再是默认路径 |

---

## 二、本机实测环境

```
GPU：NVIDIA GeForce RTX 4060 Laptop GPU（8188 MiB），驱动 591.86
torch 2.3.1+cu121（独立 venv，不影响主项目 torch 2.6.0+cu124）
主项目 venv 不安装任何语音依赖（模型推理全在独立进程）
```

显存占用（服务预热后）实测：ASR 约 2.0 GB，与文本模型（DeepSeek 为云端调用，不占本地显存）共存无压力。

---

## 三、安装与启动（Windows，可重复执行）

### 3.1 一次性安装（独立 venv，不污染主项目环境）

```powershell
# 1) 语音栈目录（仓库之外，已被 .gitignore 忽略）
mkdir E:\WORKBUDDY\Game\speech_stack
cd E:\WORKBUDDY\Game\speech_stack

# 2) 独立虚拟环境
python -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip

# 3) 固定版本依赖
.\venv\Scripts\python.exe -m pip install "torch==2.3.1" "torchaudio==2.3.1" `
  --index-url https://download.pytorch.org/whl/cu121
.\venv\Scripts\python.exe -m pip install "numpy==1.26.4" "transformers==4.51.3" `
  modelscope HyperPyYAML conformer diffusers lightning onnxruntime librosa soundfile `
  wetext inflect x-transformers omegaconf hydra-core networkx gdown protobuf rich wget matplotlib `
  "pyarrow==18.1.0" pyworld "setuptools<81" openai-whisper funasr
# ffmpeg（Windows 无系统 ffmpeg 时）
.\venv\Scripts\python.exe -m pip install imageio-ffmpeg
```

### 3.2 模型下载

```powershell
cd E:\WORKBUDDY\Game\speech_stack

# ASR 模型（FunASR 首次加载会自动下载到 MODELSCOPE_CACHE，约 1 GB）
$env:MODELSCOPE_CACHE="E:\WORKBUDDY\Game\speech_stack\models\asr"
..\venv\Scripts\python.exe -c "from funasr import AutoModel; AutoModel(model='paraformer-zh', vad_model='fsmn-vad', punc_model='ct-punc', device='cuda:0', disable_update=True, hub='ms')"
```

### 3.3 启动（每次开机）

```powershell
# 终端一：本地语音服务（模型只加载一次，常驻显存）
cd E:\WORKBUDDY\Game\git_text
$env:SPEECH_STACK_DIR="E:\WORKBUDDY\Game\speech_stack"
$env:SPEECH_WARMUP_ON_START="1"
E:\WORKBUDDY\Game\speech_stack\venv\Scripts\python.exe -m speech_service.server
# 就绪判断：curl http://127.0.0.1:8100/health  → engines.asr.state=ready

# 终端二：主后端（语音默认走本地；SPEECH_BACKEND=auto）
python -m uvicorn api.app:app --host 127.0.0.1 --port 8000

# 终端三：前端
cd frontend && npm run dev
```

### 3.4 关键环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `SPEECH_BACKEND` | `auto` | `local`（只用本地）/ `mimo`（只用 MiMo）/ `auto`（本地优先，本地失败才试 MiMo） |
| `LOCAL_SPEECH_URL` | `http://127.0.0.1:8100` | 本地语音服务地址 |
| `LOCAL_SPEECH_TIMEOUT` | `120` | 主后端调用语音服务的超时（秒） |
| `SPEECH_QUEUE_WAIT_SECONDS` | `20` | ASR 排队等待上限（超出返回"服务正忙"） |
| `SPEECH_STACK_DIR` | 仓库同级 `speech_stack` | 模型根目录 |
| `SPEECH_DEVICE` | `auto` | `cuda` / `cpu` |

---

## 四、服务接口（`speech_service/server.py`，独立进程）

| 接口 | 说明 |
|---|---|
| `GET /health` | 引擎状态 / 设备 / 显存；**推理在线程池执行**，推理过程中也能秒回 |
| `POST /warmup` | 预热（幂等） |
| `POST /asr` | multipart 音频 → 文本（含标点） |

主后端对外接口（`api/routes/audio.py`）：`/api/audio/transcribe`、`/api/audio/warmup`、`/api/audio/status`。

---

## 五、验收结果（真实运行）

| 验收 | 命令 | 结果 |
|---|---|---|
| 语音输入全链路（真实 Chrome + 假麦克风 + 真实 ASR + DeepSeek） | `node tests/e2e_voice_flow.js http://localhost:3000` | **22/22** |
| 自动测试 | `python -m pytest tests/ -q` | **485 passed** |
| 原有回归（学习闭环 / Agent / 默认模型） | 见 `PROJECT_STATUS.md` | 全绿，正式库 6 个 DB 零写入（逐库 SHA256 + 行数比对一致） |

测得的关键体验数字（本机）：

- ASR：4.4 s 录音 → **0.2–1.2 s** 出文字（同一 4.21 s 音频连续 3 次转写结果完全一致，耗时 212–258 ms），
  识别后进入输入框，**不自动发送**

### 验收中发现并已修复的问题

1. **E2E 脚本残留变量**：上一轮移除调试日志时漏删引用，脚本在打印结果前会抛 `ReferenceError`
   （表现为"没有输出、退出码非 0"）。已修复并重跑通过。

### 已知问题与限制（如实记录）

1. **ASR 同音字**：如"根、左、右"可能识别为"跟左右"。已通过"转写结果先入输入框、学生确认后发送"缓解。
   （验收脚本里转写结果尾部多出的一个字，是假麦克风设备把 4.21 s 样本循环播放导致录进了下一轮开头，
   属测试装置产物：同一文件直接调用 ASR 连续 3 次结果完全一致且无多余字。）
2. **首次启动慢**：模型加载约 49 s（ASR）；服务启动即预热，就绪前 `/health` 会显示 `loading`。
3. 语音服务当前为**单机单实例**，未做多用户并发调度（推理按类型串行）；
   8 GB 显存下不建议提高并发。

## 六、手机宽度下的语音实测（2026-09-27）

> 脚本：`tests/e2e_voice_flow.js`（同一脚本支持传视口参数）
> 命令：`node tests/e2e_voice_flow.js http://localhost:3000 <假麦克风wav> 390 844`

**旧文档所述：手机宽度 24/24 通过；桌面 1440×1000 对照 22/22 通过**（该轮脚本含朗读相关检查，相关检查已随功能移除）。

| 检查项 | 手机宽度（390×844） |
|---|---|
| 录音按钮在可视区内 | ✅ x=24 w=38（视口 390） |
| 录音 → 本地转写（FunASR） | ✅ 200，延迟 0.2–0.6 秒，转写文本正确进入输入框 |
| 误识别不会自动提交 | ✅ 消息数不变，需学生确认后发送 |

### 这次**没有**验证的（不要当成已验证）

- **不是真机**：这是「移动宽度 + Chromium + 假麦克风音频文件」的浏览器实测，**没有用真实手机、真实麦克风**。
- 未验证移动浏览器差异（iOS Safari / Android Chrome）、麦克风权限弹窗、4G/Wi-Fi 弱网。
