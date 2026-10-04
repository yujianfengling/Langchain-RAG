# 部署与使用（Windows 单机版）

> 本文只写**在本机实测通过**的部署方式。
> 上次实测：2026-10-02，Windows + Python 3.12 + Node 18。

## 一、这套系统是什么形态

前端会被构建成静态产物，**由后端直接托管**。所以正式使用时：

**一个进程 = 完整平台**（不需要另外装 nginx，也不需要单独开前端服务）。

- 本机访问：`http://localhost:8000`
- 学生用手机：连**同一个 WiFi**，打开启动时显示的局域网地址（形如 `http://172.19.x.x:8000`）

## 二、首次部署（只做一次）

### 1. 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| Windows | 10/11 或 Windows Server | 本文面向 Windows |
| Python | 3.10+（实测 3.12） | 后端 |
| Node.js | 18+ | **仅用于构建前端**，构建完就不再需要 |

### 2. 装依赖

```powershell
cd <项目目录>
.\install.ps1
```

如果提示"禁止运行脚本"（PowerShell 执行策略限制），改用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

### 3. 配置模型 Key（必做）

复制模板并填写：

```powershell
Copy-Item config\env.example .env
notepad .env
```

**最少要填** `DEEPSEEK_API_KEY`（文本问答与出题都靠它）。
可选：`TAVILY_API_KEY`（知识库资料不足时联网补题）、`DASHSCOPE_API_KEY`（图片识别/通义视觉）。

> `.env` 已在 `.gitignore` 中，**不会被提交**，也不要把 Key 写进任何被跟踪的文件。

### 4. 确认本地 embedding 模型在位（必做）

知识库检索依赖本地模型 **BAAI/bge-small-zh-v1.5**（约 92MB，位于项目 `models/` 下）。

**它不在 git 里**（`.gitignore` 忽略了 `models/`）——所以用 git 拉代码部署时，
必须把 `models/` 目录**一起复制**过去，否则知识库与检索不可用。

- 模型在位时程序会**自动发现**它（2026-10-02 起支持，无需手工配置路径）。
- 要把模型放到别处，可在 `.env` 里显式指定
  `EMBEDDING_LOCAL_DIR=<模型快照目录>`（快照目录是
  `models/bge-small-zh-v1.5/snapshots/` 下的那个哈希目录）。
- 代码以 `local_files_only=True` 加载：模型缺失时**不会静默联网下载**，
  而是明确报"不可用"——这是有意的，免得部署时卡在一个漫长的下载上还不知道。

## 三、启动（日常就用这个）

**双击 `start_production.bat`** 即可。它会：

1. 检查环境与 `.env`
2. 构建前端（已构建过会自动跳过，加 `-SkipBuild` 可强制跳过）
3. 检查端口占用
4. 启动服务，并打印本机地址 + 局域网地址

常用参数：

```bat
start_production.bat                 :: 默认 8000 端口
start_production.bat -SkipBuild      :: 跳过前端构建（改过后端、没动前端时用）
start_production.bat -Port 8080      :: 换端口
start_production.bat -Open           :: 启动后自动打开浏览器
```

> **为什么是 .bat 而不是直接跑 .ps1**：这台机器（以及不少教学机）默认**禁止运行 PowerShell 脚本**，
> 直接跑 `.ps1` 会报"因为在此系统上禁止运行脚本"。`.bat` 内部用
> `-ExecutionPolicy Bypass` 绕开这个限制，双击即可。

停止服务：在窗口里按 `Ctrl+C`。

### 学生端 / 教师端分开端口（可选）

如果希望**学生和老师用不同的地址**（学生地址可以随便发，教师地址自己留着），
用这两个脚本，各自一个进程、一个端口：

| 入口 | 脚本 | 默认端口 | 访问根路径时 |
|---|---|---|---|
| 学生端 | `start_student.bat` | **8010** | 自动跳到 `/student` |
| 教师端 | `start_teacher.bat` | **8020** | 自动跳到 `/teacher` |

换端口：`start_student.bat -Port 8030`。

> 两端是**同一个应用**，这里分的是**入口**（根路径直接落到对应页面），
> 真正的权限**始终由登录身份决定**——学生账号在任何一个端口都看不到「教师工作台」。
> 不需要分开就用 `start_production.bat`（默认 8000），两端同址。

> 端口被占用时启动脚本会直接报出占用进程的 PID，换一个端口即可。

## 四、老师怎么用（一分钟版）

### 先开通教师身份（进不去「教师工作台」就看这里）

**教师端没有独立网址**——和学生端是同一个地址，只是登录后侧边栏多一个「教师工作台」。
教师身份**不会自动获得**，需要开通（改 `.env` 后**重启服务**）：

**方式一（最简单）**：在 `.env` 里直接指定老师账号（多个用逗号分隔）

```
TEACHER_USERS=老师的登录名
```

**方式二（管理员签发邀请码）**：在 `.env` 里指定管理员

```
ADMIN_USERS=管理员的登录名
```

管理员登录 → **设置 → 教师身份** → 「生成一个教师邀请码」→ 得到 `T-XXXXXXXX`；
把它给老师，老师在同一个位置填码即可开通（一次性码）。

> 登录名就是注册时填的用户名。开通后侧边栏会**立刻**出现「教师工作台」。

### 日常操作

1. 浏览器打开平台 → 用**已开通教师身份**的账号登录
2. 教师工作台 → **建课**（例如"数据结构与算法"）
3. 课程详情 → **建班** → 把界面上的**班级邀请码**（6 位）发给学生
4. 上传**课程资料**（PDF / Word / Markdown）→ 系统自动处理并建立课程知识库
   - 处理完成的状态是「已就绪」
5. 生成**教案**，或直接**新建作业** → 点「AI 出题」（会依据你上传的课程资料出题）
6. 检查题目 → **发布**

学生侧：打开同一个地址 → 注册/登录 → 「我的课程」输入班级邀请码入班 → 做作业。

> 学生做完作业后，错题本、复习安排、掌握度会**自动**更新，不需要老师手动操作。

## 五、数据与备份

所有数据在项目下的 `data/` 目录：

| 内容 | 位置 |
|---|---|
| 学科与课程资料 | `data/subjects/`、`data/subjects.json` |
| 向量/BM25 索引 | `data/vector_store/`、`data/bm25_index/` |
| 账号 | `data/auth.db` |
| 学习记录（作答/错题/掌握度/复习） | `data/learning_loop.db`、`data/learning.db`、`data/user_agent.db` |
| 教师端（课程/班级/作业） | `data/teaching.db` |
| 识图缓存 | `data/cache/` |

**备份**：整个 `data/` 目录复制走即可。
**注意**：用环境变量 `EDU_DATA_DIR` 可以把数据放到别处（例如另一块磁盘）；部署时若设了它，备份要备份那个目录。

## 六、常见问题

**Q: 端口被占用**
启动脚本会提示占用进程的 PID。换端口：`start_production.bat -Port 8080`。

**Q: 页面打开是 JSON，不是界面**
说明 `frontend/dist` 不存在（前端没构建）。跑一次 `start_production.bat`（不加 `-SkipBuild`）。

**Q: 问答报"模型不可用"**
`.env` 里的 `DEEPSEEK_API_KEY` 没配或已失效。查看 `http://localhost:8000/health/ready`
里的 `external_capabilities.llm`，能看到最近一次失败原因。

**Q: 上传的 PDF 是扫描件，识别不出内容**
在 `.env` 里设置 `VISION_MODE=vision`，并配置 `DASHSCOPE_API_KEY`。
`VISION_MODE=ocr` 会**完全跳过**图片识别，扫描件里的正文就永远进不了知识库。

**Q: 学生手机打不开**
1) 手机和电脑要连**同一个** WiFi；2) Windows 防火墙可能拦了端口，
需要在"入站规则"里放行 8000；3) 用启动时打印的那个局域网 IP。

**Q: 改了代码想生效**
改了前端：重新跑 `start_production.bat`（重新构建）。只改了后端：重启服务即可。

---

## 七、可选功能（不装不影响文字问答）

| 功能 | 需要配什么 | 说明 |
|---|---|---|
| 联网补题 | `TAVILY_API_KEY` | 知识库资料不足时从网上找素材，再由本地模型重新出题核对 |
| 图片识别（扫描版课件） | `DASHSCOPE_API_KEY` + `VISION_MODE=vision` | 没配时，图片/扫描件里的正文**进不了知识库** |
| 语音输入（录音转文字） | 本地 speech_service（见下） | 只做"录音 → 文字"；**讲题朗读已取消**，系统不再朗读回答 |

### 语音输入（ASR）怎么开

讲题朗读（TTS）已从项目中**完全移除**，语音只用于把学生说的话转成文字：
录音 → 转写 → 文字进入输入框 → **学生确认后**才发送。
不装它，文字问答、AI 出题、学习闭环（错题本/复习/掌握度）都正常。

1. 装轻量依赖（**不需要 PyTorch**）：
   `python -m pip install sherpa-onnx onnxruntime soundfile`
2. 在 `.env` 选轻量引擎与缓存目录（**缓存目录必须是纯英文路径**）：
   `SPEECH_ASR_ENGINE=sensevoice`、`SPEECH_MODEL_CACHE_DIR=D:/speech_models`
3. 另开一个终端启动语音服务（独立进程，默认端口 8100）：
   `python -m speech_service.server`
4. 主后端指向它：`SPEECH_BACKEND=local`、`LOCAL_SPEECH_URL=http://127.0.0.1:8100`
5. 验证：`curl http://127.0.0.1:8100/health` 看 `engines.asr.state` 是否为 `ready`；
   或打开「设置 → 语音」看"本地语音识别"标签是否为绿色。

说明：

- ASR 模型**首次真正录音时**才自动下载（压缩包约 156 MB，落地约 229 MB）；
  启动后端、打开网页、健康检查都**不会**触发下载。
- 离线机房：`SPEECH_MODEL_OFFLINE=1`（缺模型时只提示"尚未下载"，不联网）。
- 已有模型目录：`SPEECH_ASR_MODEL_DIR=<目录>`（命中则完全不下载）。
- 语音服务没启动时，设置页会明确提示"本地语音识别未就绪"，**文字问答不受影响**。
- 旧的 `speech_stack`（FunASR 全量 + CosyVoice）不再是必需组件；如已安装，
  仍可用 `SPEECH_ASR_ENGINE=funasr` 走旧路径。

---

## 八、附：给开发者的端到端验收（首次使用走查）

项目自带一套**真实浏览器**的走查脚本，模拟"老师 + 2 名学生"把教学闭环走一遍，
并逐条给出 PASS/FAIL，还会截图到 `tests/_screenshots/`（已 gitignore）。

前置：后端必须跑在**隔离数据目录**（脚本会自检，不满足直接退出码 2），
并且配 `ADMIN_USERS=walk_admin`（脚本要签发一个教师邀请码）。

```powershell
# 1) 起后端（隔离目录 + 管理员白名单）
$env:EDU_DATA_DIR="D:\项目\RAG\walkthrough_data"
$env:ADMIN_USERS="walk_admin"
.\venv\Scripts\python.exe -m uvicorn api.app:app --host 127.0.0.1 --port 8000

# 2) 跑走查（前端由后端托管，所以两个地址都填 8000）
$env:NODE_PATH="$PWD\frontend\node_modules"
node tests/e2e_first_use_walkthrough.js http://127.0.0.1:8000 http://127.0.0.1:8000
```

- `playwright` 声明在 `frontend/package.json` 的 devDependencies 里，所以要先在
  `frontend/` 下 `npm install`（上面用 `NODE_PATH` 把它指给 `tests/` 下的脚本用）。
- 脚本优先用**系统 Chrome**，没有会自动回退到 playwright 自带的 chromium
  （否则没装 Chrome 的机器根本跑不了验收）。
- 2026-10-02 实测结果：**54 项，通过 52**。完整教学闭环（建课→建班→传资料→AI 出题→
  发布→学生入班→作答判分→错题本→自动复习→掌握度→学情分析）全部通过。
