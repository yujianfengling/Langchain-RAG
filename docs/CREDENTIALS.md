# 凭据暴露评估与处置（2026-09-26）

> **本文不含任何真实密钥内容**，只描述涉及的文件、提交、范围与处置步骤。
> 本轮**未**执行 force push、未改写 Git 历史、未停用任何团队在用的密钥。

---

## 一、结论（先看这里，2026-09-27 复核）

| 项 | 结论 |
|---|---|
| 当前代码是否还有内置密钥 | **没有**。`config/models.py::_BUILTIN_API_KEYS` 已改为空字典 `{}` |
| 当前**已跟踪文件**里是否有真实明文密钥 | **没有**（2026-09-27 全量扫描确认）。`tests/test_qwen_vision_reliability.py:406/425` 出现的 `sk-…` 是**安全测试用的假串**（该用例断言日志里不得出现密钥），不是真实密钥 |
| 真实密钥现在放在哪 | 只在**未跟踪**的 `.env`（已确认被 `.gitignore` 忽略）：本次扫描到 2 处，属本机配置 |
| 对外部署是否阻塞 | **是**。历史提交中的旧密钥仍可达，必须先在供应商后台轮换再对外部署 |
| 是否曾经进入 Git 历史 | **是**。历史提交中确实存在真实值，仅从代码里删掉**不足以**消除风险 |
| 受影响的服务 | **MiMo**（1 个 Key，被 vision/asr/tts 复用）与 **DeepSeek**（1 个 Key） |
| 未受影响 | 通义千问（历史里该字段为空字符串，从未写入真实值） |
| 是否需要更换 | **需要**。建议按第四节步骤轮换（尤其 MiMo 与 DeepSeek 两个 Key） |
| 本轮已做的止损 | 代码中移除；`.env` / `config/local_secrets.json` 未跟踪；示例文件改为占位符；新增自动化检查防回归 |

---

## 二、暴露位置与范围

### 2.1 历史提交中的真实值

| 文件 | 提交 | 内容形态 | 说明 |
|---|---|---|---|
| `config/models.py` | `85ee0a7` 更好的AI老师 | 新增 5 行含密钥形态 | 首次引入 `_BUILTIN_API_KEYS` |
| `config/models.py` | `a650db2` 加入视觉模型qwen | 同上 | 增加 `qwen-vision` / `mimo-vision` |
| `config/models.py` | `f00c2fa` 呀嘞呀嘞 | 同上 | 增加 `mimo-asr` / `mimo-tts` |
| `config/env.example` | `85ee0a7` / `a650db2` / `f00c2fa` | 新增 2 行 | `# MIMO_API_KEY=…` / `# DEEPSEEK_API_KEY=…`（**被注释掉**，但值仍是真实密钥） |

`_BUILTIN_API_KEYS` 中曾出现的条目（仅列提供方与形态）：

| 提供方 | 形态 | 是否真实值 |
|---|---|---|
| `mimo` | `tp-` 前缀，长度 51 | **是** |
| `deepseek` | `sk-` 前缀，长度 35 | **是** |
| `mimo-vision` / `mimo-asr` / `mimo-tts` | `tp-` 前缀，长度 51（与 `mimo` 同值，同一 Key 复用） | **是** |
| `qwen` / `qwen-vision` | 空字符串 | 否（从未写入真实值） |

### 2.2 暴露范围评估

- 这些提交**已在本地仓库历史中**。若该仓库**曾推送到任何远端**（含个人 fork、内部 GitLab、网盘归档），
  则密钥应视为已泄露 —— 拿到仓库的人可用 `git log -p` 直接读出。
- `.env` **从未被 Git 跟踪**（`git ls-files .env` 无结果），其中的密钥未进入历史；
  但注意 `.env` 在本机是明文，不要随目录打包外发。
- 本轮**未做**远端历史清理；也不建议在团队共用分支上 force push（会打断所有协作者）。

---

## 三、当前代码的读取顺序（已改）

```
1) 环境变量                      （如 DEEPSEEK_API_KEY / MIMO_API_KEY）
2) 未跟踪的本地配置              config/local_secrets.json（已在 .gitignore 中）
3) 都没有                        → 返回空字符串，并给出"未配置密钥"的可理解提示
```

- 代码里**不再存在**任何内置/回退密钥（`_BUILTIN_API_KEYS = {}`）。
- 相关实现：`config/models.py` 中 `_load_local_secrets()` / `_local_secret()` /
  `get_model_api_key()` / `reload_local_secrets()`。
- 示例配置（**只含占位符**）：
  - `config/env.example` —— 变量清单与说明，密钥写成 `<your-deepseek-api-key>` 之类占位符
  - `config/local_secrets.example.json` —— 结构示例，值支持 `env:VAR_NAME` 形式引用环境变量
- `.gitignore` 已补充：`config/local_secrets.json`、`.env`、`*.pem`、`*.key`、
  以及语音栈目录/模型缓存/录音/合成缓存（`speech_stack/`、`models_cache/`、`*.pth`、`*.onnx`、`*.safetensors` 等）
- `.gitignore` 另补 `*.db-wal` / `*.db-shm` / `*-wal` / `*-shm`：SQLite 的 WAL 与共享内存伴生文件
  原先**未被忽略**（`data/*.db` 忽略了主库，但 `data/chat.db-wal` 这类文件会出现在 `git status` 里）。
  它们含数据库正文（聊天内容、会话令牌），一旦被 `git add -A` 就会进仓库，属真实泄露路径，已堵上。

### 3.1 本机配置方法（不写入仓库）

```powershell
# 方式一（推荐，仅当前会话/用户环境变量）
setx DEEPSEEK_API_KEY "<your-deepseek-api-key>"

# 方式二（本地未跟踪文件）
copy config\local_secrets.example.json config\local_secrets.json
#   然后填入真实值；该文件已被 .gitignore 忽略
```

---

## 四、建议的轮换步骤（需由密钥所有者在控制台操作）

1. **登录提供方控制台**，为 DeepSeek 与 MiMo 各**新建**一个 Key（不要直接改旧的，先保可用）。
2. **把新 Key 配置到本机**：环境变量或 `config/local_secrets.json`（不要写进代码、不要提交）。
3. **重启服务并验证**：`python tests/verify_default_model.py`（文本）与
   `GET /api/health` 的 `capabilities.text.available` 应为 `true`。
4. **确认无调用后再停用旧 Key**：观察提供方控制台的旧 Key 调用量降到 0，再删除/禁用；
   若旧 Key 就是团队在用的那一个，**先与团队确认切换时间**，避免打断他人。
5. 若仓库曾推送到远端：**不要重写历史**。把泄露的 Key 视为永久失效资产（轮换即可），
   如需清理远端历史请走团队决策 + 所有协作者同步重克隆的正规流程。
6. 轮换完成后在本文件记录日期与操作人（**只记录日期/提供方，不记录密钥**）。

> **不要把"当前文件已清空"当成"历史泄露已解决"**：
> 前者说的是工作区/已跟踪文件（已确认干净），后者说的是 Git 历史与远端（仍含旧密钥）。
> 二者是两件事，轮换才是消除风险的动作。本轮没有供应商账号权限，**未执行轮换**，
> 也没有改写远端历史。

---

## 五、防回归：自动化检查

`tests/test_credentials_hygiene.py`（7 项）在不输出密钥内容的前提下做了以下断言：

| 检查 | 内容 |
|---|---|
| 1 | 扫描所有 **Git 已跟踪文件**，若出现 `sk-…` / `tp-…` / `Bearer …` 形态**只报 file:line**，不打印值 |
| 2 | `config/models.py::_BUILTIN_API_KEYS` 必须为空字典 |
| 3 | 未配置任何密钥时，`get_model_api_key()` 返回 `""`（**不回退到内置值**） |
| 4 | 配置了本地未跟踪文件时，能正确读取 |
| 5 | `config/local_secrets.json` 必须在 `.gitignore` 中 |
| 6 | `.env` 不得被 Git 跟踪 |
| 7 | 示例文件中不得出现真实密钥形态（只允许占位符） |

运行：

```powershell
python -m pytest tests/test_credentials_hygiene.py -q
```

> 说明：检查 1 的正则只能发现"密钥形态"的字符串。命名为 `API_KEY` 的普通短串、
> base64 编码后写入的密钥无法被它发现；这类仍需靠 `git diff` 人工复核。
