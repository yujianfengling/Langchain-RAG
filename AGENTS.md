# AGENTS.md — 给 AI 协作者的项目约定

> 本仓库由 AI agent 协作开发（人负责决策与验收）。
> 下面只写**会导致你浪费时间或写错代码的硬事实**，不写泛泛而谈的最佳实践。
> 最后更新：2026-10-02

---

## 0. 执行效率铁律（最重要，先读这条）

**总耗时 ≈ 往返次数 × 单次往返成本。**
你优化"单条命令有多快"的收益，远小于"少跑几次、同时跑几件"。

以下是真实踩坑数据（2026-10-02 一次 14 分钟的任务复盘）：

| 我做的事 | 单次 | 次数 | 小计 |
|---|---|---|---|
| 跑完整测试套件 | 259 秒 | **3 次** | **~13 分钟** |
| 重建知识库（逐张调 API） | 75 秒 | **4 次** | **~5 分钟** |

也就是说：14 分钟里 13 分钟在**等我自己重复跑的测试**，而机器并不慢。

### 四条硬规矩

**1. 昂贵操作一律后台跑，且同一轮里继续干别的**

```bash
# ✅ 正确：后台起全量测试，本轮继续做别的
pytest tests -n auto        # run_in_background=true
# 期间去写代码/跑 ruff/看单文件测试

# ❌ 错误：前台等 259 秒，什么也不做
pytest tests
```

**2. 每次改完先小样本验证，通过才全量；全量只跑一次**

```bash
# ✅ 先 2~3 个样本确认逻辑对，再跑全量
pytest tests/test_<改动模块>.py -k "两三个用例"

# ❌ 错误：改一个字段就整本重建/全量重跑
```

实测教训：曾为一个字段改动**整本重建 4 次**（每次 75 秒）。其中 3 次本可用 2~3 条样本代替。

**3. 相互独立的操作，同一轮并发发出，不要一个一个来**

一次消息里可以同时发多个工具调用。装依赖 + 跑耗时分析 + 读文件是独立的，应并发。
每个往返都有固定成本（生成 + 执行 + 上下文重读），往返次数才是主要矛盾。

**4. 一旦起了后台任务，不要因为"可能用不上"就杀掉**

实测教训：曾主动 `kill` 掉一个 `-n auto` 的全量测试，理由是"已经有 ruff 了"，
结果最后仍然需要那次结果 → 白等一次 259 秒。**不确定就先让它跑完。**

### 两条配套习惯

**5. 调外部 API 的中间结果必须落盘缓存，跨轮次复用**
重建流程里逐张识图 75 秒，其中 74 秒在调 API。改一个非识图字段时，
缓存命中可以让重建降到 3 秒。本项目已有 `data/cache/image_descriptions/`，
**改 Vision 逻辑时记得升 `VISION_PROMPT_VERSION`**（见第 5 节）。

**6. 反馈顺序：`ruff`（0.1s）→ 单文件测试（秒级）→ 后台全量（最后）**
不要第一步就全量。

### 一句话自检

> 在我发出这一轮之前：**有没有本可并行的被我串行化了？有没有本可只跑一次的被我跑了两次？**

---

## 1. 先把反馈回路跑快

这个项目最容易浪费时间的不是写代码，是**等测试**。
完整测试套件 629 项、串行约 260 秒。请按下面顺序用工具，不要每改一行就跑全量。

| 目的 | 命令 | 耗时 |
|---|---|---|
| 查"用了没导入"这类崩坏 bug | `python -m ruff check .` | **0.1 秒** |
| 跑单个测试文件 | `python -m pytest tests/test_xxx.py` | 1–10 秒 |
| 跑相关几个文件（**推荐日常**） | `python -m pytest tests/test_a.py tests/test_b.py` | 数秒~数十秒 |
| 全量（发版前） | `python -m pytest tests` | ~260 秒 |
| 全量 + 并行 | `python -m pytest tests -n auto` | 显著快于串行（需 `pytest-xdist`） |

**纪律**：改完先 `ruff`，再跑**相关文件**，最后才全量。不要一开始就全量。

> 注意：`pytest.ini` 里的 `slow/integration/vision` 只是**分类标签**，
> 不会自动排除测试。要跳过得显式写 `-m "not slow"`。

**并行（`-n auto`）现在是可靠的**（2026-10-02 修复）：`tests/conftest.py` 会为每个
xdist worker 分配**独立**的数据目录。此前所有 worker 共用同一个 `git_text_test_data`，
并发读写同一批 SQLite 库，导致"跑真实子进程"和"时序敏感"的用例**随机失败**——
实测同一批测试并行跑三次，失败的用例互不相同，而串行全绿。修复后并行全量 exit 0。

> 若看到并行下零星失败，先怀疑并发干扰（串行复跑确认），**不要直接当成回归**。

⚠️ **但 `-n auto` 不一定安全**（2026-10-02 实测）：worker 数 = CPU 核数，而每个 worker
都要加载 torch / transformers，核多时会**把内存吃爆**。日志里出现
`[gwNN] node down: Not properly terminated` 就是 worker 被杀，此时**结果不可信**
（会伴随一堆 F，看着像回归其实不是）。稳妥用法是限制 worker 数（如 `-n 4`）或串行；
只有确认机器空闲时才用 `-n auto`。

**测试一律不打外网**：`conftest.py` 把 `WEB_SEARCH_ENABLED` 置为 `false`。
要验证联网降级路径的用例，自己 mock `services.web_search_service`。

---

## 2. 必须用 pytest，不能用 unittest

```bash
python -m pytest tests        # ✅ 唯一正确方式
python -m unittest discover   # ❌ 会出问题，见下
```

**原因（我亲自踩过）**：`tests/conftest.py` 通过 `pytest_configure` 做数据隔离，
把 `EDU_DATA_DIR` 指向独立测试目录（默认 `D:\项目\RAG\git_text_test_data`；
并行时再按 worker 细分），并把 `VISION_MIN_IMAGE_BYTES` 设为 0
（否则测试用的合成小图会被"过小图片"过滤跳过），同时把 `WEB_SEARCH_ENABLED`
置为 `false`（测试不打外网）。

`unittest discover` **不会加载 `conftest.py`**，后果：
1. **测试数据写进正式 `data/` 目录**（污染真实库）；
2. 产生大量假失败 —— 我曾因此误报"73 个测试失败"，实际只有 8 个。

另：`conftest.py` 会**拒绝**在 `EDU_DATA_DIR` 指向正式数据目录时运行（直接 `SystemExit(2)`）。
这是有意的保护，不要绕开。

---

## 3. 环境准备

```bash
python -m pip install -r requirements.txt
```

`requirements.txt` 里已声明但常被漏装的两个（**AI 环境重建时容易丢**）：
- `pytest` —— 不装则完全无法跑测试
- `jieba` —— BM25 词级分词的依赖；缺失会自动回退字符级，导致检索质量下降且无报错

静态检查：`python -m pip install ruff`

---

## 4. 密钥与配置

- **绝不把密钥写进任何被 git 跟踪的文件。** 历史教训：`config/models.py` 曾内置真实密钥
  并推到远端，清理时需重写历史（详见 `docs/CREDENTIALS.md`）。
- 密钥只有两个来源：`.env`（已 gitignore）或 `config/local_secrets.json`（已 gitignore）。
- 新增可配项时，**必须同时确认读取路径真的生效**。本项目有过多次"配置项存在但从未被读取"：
  `CHUNK_SIZE`/`RAG_TOP_K`/`EMBEDDING_DEVICE` 都曾长期无效（`rag/embedding.py` 里硬编码了 `device="cpu"`）。
  **验证方法**：改完 `.env` 后打印实际生效值，别只看配置定义。

---

## 5. 视觉（识图）链路：三处容易断的地方

课件图片是这个系统的主要信息源，链路较长，改动时注意：

1. **`VISION_MODE`**（`.env`）：`ocr` 会**完全跳过**识图。图片型课件（扫描版 PDF/PPT 截图）
   必须设 `vision`，否则图片里的正文永远进不了知识库。
2. **提供方选择**：顺序为 `deepseek > mimo > qwen`，并带**凭证探活**。
   注意 **"key 存在" ≠ "key 可用"** —— MiMo 的 key 常在但会返回 401，
   若只按"key 非空"挑选，失效凭证会把可用凭证挡住。
3. **增强必须在合并之前**：PDF 抽取是**逐元素**的，而 `merge_small` 只保留块首元素的 metadata。
   若先合并再增强，`image`/`table`/`formula` 的 `modality` 会被覆盖，
   结果是**大量图片只有块首那一张被识别**（实测 90 张只识别了 1 张）。
   因此走 `DocumentAdapter.adapt(..., pre_merge_hook=...)`。

`is_content` 字段贯穿整条链路，任一层忘记透传，噪声过滤就会静默失效。
排查时按这个顺序看：`extract_text_from_image` → `process_image_to_document` →
`QwenVisionProvider.analyze_image` → `MultimodalEnricher._enrich_image_document`。

**缓存版本**：改动 Vision 提示词或解析逻辑后，必须升 `VISION_PROMPT_VERSION`
（`rag/enrichment/multimodal_enricher.py`），否则旧缓存会继续生效、改动看不到效果。

---

## 6. 索引与知识库

- 索引目录：`data/vector_store/<safe_dir_name>/`（中文 subject_id 会 base64 编码，
  因为 FAISS 的 C++ 层**要求整个路径为 ASCII**，仅文件名 ASCII 不够）。
- 每个索引目录有 `meta.json`（模型/维度/分块参数）。改动 embedding 模型后必须**重建**，
  否则维度相同但语义空间不同 → 静默返回无关结果。
- **重建不要"先删后建"**：曾有实现先 `rmtree` 再构建，构建失败就永久丢库。
  现在 `services/knowledge_service.py` 用「备份 → 构建 → 成功删备份 / 失败回滚」。
- 删除知识库/文档后必须让**内存缓存失效**（`GlobalFAISS.invalidate` + `hybrid_retriever.clear_index`），
  否则进程不重启就会继续返回已删内容。
- FAISS 与 BM25 的条目数**必须一致**。BM25 会丢弃分词为空的文档，
  因此占位保留是有意设计（见 `bm25_retriever.add_documents`），不要"优化"掉。

---

## 7. 改代码时的约定

- **保持既有测试契约**：若必须改变公开契约（如函数签名/调用时机），
  同一次提交里更新受影响的测试，并在注释说明**为什么**变。
- **不要为了让测试变绿而改断言**：区分「测试写错了」与「实现回归了」。
  若判定是测试对环境有耦合（例如断言某个 provider 的文案），
  在提交信息里写清，并优先让断言反映"应有的行为"。
- **用脚本改文件时，幂等判断绝不能用短字符串**（同一个坑我连续踩了三次）：
  比如 `if 'node_id: nodeId' in s: print('已改过')` —— 这个串会匹配到文件**别处**
  的同名写法（注释、别的函数），于是脚本以为改过了、**直接跳过，改动静默丢失**。
  最坏的结果是"提交里看着做了、实际没生效"（本项目真发生过一次）。
  正确做法：
  1. 判据用**待替换的完整旧文本**（`if new in s and old not in s`），不要用片段；
  2. 替换前 `assert s.count(old) == 1`，不为 1 就报错退出；
  3. **改完必须复核文件**（grep / read 一次），不要只信脚本打印的"已改过"。
- `try/except` 不要吞掉 `ImportError`。本项目有过静默吞掉 ImportError 导致
  整个功能（**知识点增量入库**）永久失效、且无任何报错的案例：
  `knowledge_expander` 长期调用一个**从未被实现**的 `add_documents_to_store`，
  ImportError 被 `except Exception` 吞掉，功能坏了一年都没人发现。
  该函数已于 2026-10-02 补上，并有回归测试 `tests/test_incremental_ingest.py` 锁定。
  **教训**：调用外部函数前先确认它存在；新增功能必须配测试，否则静默失效无从察觉。
- 新增"静默降级"分支时**必须打日志**，且降级不应伪装成成功。
- **前端拆包要实测首屏，别凭直觉加 `manualChunks`**（2026-10-02 实测打脸）：
  按包名粗粒度把 react / antd / framer-motion / echarts 拆成 vendor chunk 后，
  首屏从 **874 KB 涨到 1580 KB（+81%）**——单独成 chunk 后 Rollup 保留了
  antd 大量未被使用的代码，tree-shaking 失效。
  **真正的优化是路由级懒加载**（`src/App.tsx` 的 `lazy` + `Suspense`，
  首屏 3.94MB → 874KB）。要动拆包，先量首屏：把 `dist/index.html` 里所有
  `/assets/*.js` 加起来，**只看最大的那个文件会得出错误结论**。

---

## 8. 已知未完成项（不要误以为是 bug）

- **两套并行的视觉实现**（2026-10-02 已核实清楚哪套是活的）：
  `rag/enrichment/multimodal_enricher.py` 的 `MultimodalEnricher` 是**活的那套**
  （`rag/loaders/pdf_loader.py` 用它）；`rag/multimodal/pdf/processors/vision_processor.py`
  的 `VisionProcessor` **没有任何生产代码调用**，只有测试覆盖（`test_processors.py`、
  `test_trial_permission_guards.py`）。
  差别：前者面向 documents（图片/表格/文本分派）、走 `services/multimodal_service`
  （多提供方 + 能力状态上报）、缓存带 `VISION_PROMPT_VERSION`；后者面向图片文件/PDFElement、
  自带 `cache/vision_descriptions`、模型名写死 qwen-vision。
  **改视觉行为必须改活的那套**，否则改了没效果。
  统一方案（待决策）：① 删掉未接线的那套并迁走其测试；② 保留但明确标注未接线
  （当前已加警示注释）；③ 让 processors 变成 MultimodalEnricher 的薄封装。
- `材料力学` 课件的 PDF 文本层只有约 3,900 字符（24 页里 131 张图），
  内容量天然极低，检索效果差是**数据源**问题，不是代码问题。
- 环境耦合的测试已改为**显式跳过**（2026-10-02）：依赖 `DASHSCOPE_API_KEY` 的
  `test_explicit_model_overrides_default` 现在是 `skipUnless`，只配 DeepSeek 时会
  **SKIP 而不是假失败**。看到 SKIP 是正常的，别当成问题。
- `frontend/src/components/ModelSelector` 是**死组件**（没有任何页面引用），
  而后端 `/api/models/switch` 也**不改动任何状态**（只校验提供方配置、返回"已选择"）。
  即"模型切换"这个功能从未真正接通。接线还是删除，待决策。
- **掌握度对自评采取"单向从严"，已接线**（2026-10-02 晚）：
  `mastery_service.rebuild_point_from_attempts` 按作答记录重放，
  `quiz_service.self_evaluate` 在学生改判"答错"后调用它。
  规则是：
  - 自评**答错** → 采信。AI 判的"对"被撤回、记为"错"（学生承认不会，是真证据）；
  - 自评**答对** → 不采信、也不触发重算。**撤不掉** AI 判的那条错误证据。

  **为什么不能"完全生效"**：若自评答对也采信，学生点一下「我答对了」就能把错误证据
  抹掉——这是刷分，会毁掉整个掌握度。反之若完全不采信，就会出现"AI 判对、学生改判错，
  掌握度还显示对"的自相矛盾。单向从严同时解决这两点。
  改动时必须同时跑 `test_phase1_hardening.py`（端到端两个方向）与
  `test_mastery_rebuild.py`（口径）。

---

## 9. 快速自检清单（提交前）

```bash
python -m ruff check .                    # 0.1 秒。**当前 services/ api/ rag/ 已清零**，
                                          # 请保持 0
python -m pytest tests/test_<你改的模块>.py   # 相关文件
python -m pytest tests -n 4               # 全量（worker 隔离已做；别用 -n auto，见第 1 节）
git status --short                        # 确认没夹带临时脚本/密钥/数据文件
```

**ruff 现状（2026-10-02）**：`services/`、`api/`、`rag/` 已**清零**（此前约 25 条存量）。
清理时修掉的真问题包括：`rag/text_splitter.py` 里 `SimpleDocument` 被三个 ImportError
分支各抄了一份（已提到模块级 + 用 `TYPE_CHECKING` 声明 `Document`）、
`api/routes/models.py` 的死赋值（顺带查出"模型切换成功"是误导文案，见第 8 节）。
**保持 0。**

> 逐条核对上下文，别用 `ruff --fix --unsafe-fixes` 一把梭：`last_error = e` 在
> `model_service.py` 的流式与非流式里文本**完全相同**，但只有非流式那处会被后续
> `raise` 真正使用——误删会丢错误信息。

提交信息里写清：**改了什么、为什么、怎么验证的**。

---

## 10. 可观测性：外部能力状态与调用账本（2026-10-02 新增）

这个项目反复吃"静默降级"的亏（`knowledge_expander` 坏了一年、
`VISION_MODE=ocr` 让图片永远进不了知识库、`CHUNK_SIZE` 从未生效）。
现在有统一出口，**新增外部依赖时请接进来**，不要只写日志。

### 两个模块

- `services/capability_status.py` —— 运行态能力状态（llm / web_search / vision / speech）。
  **被动探活**：调用方在真实调用后 `safe_report(CAP_X, ok, reason=...)`，
  不额外发探测请求、不花钱。`ok=None` 表示本次进程还没调用过（**不等于不可用**）。
- `services/usage_ledger.py` —— 外部调用账本（按天 × 能力）。只记**成功**的调用；
  有当日预算（`USAGE_DAILY_BUDGET_*`，0 = 不限）。落盘在 `get_data_dir()/usage_ledger.db`。

两者都经 `GET /health/ready` 下发（`external_capabilities` / `usage_today`），
前端在全局横幅（只在降级/超预算时出现）和设置页「运行状态」面板展示。

### 接入约定（踩过的坑）

- 上报**绝不能影响主流程**：用 `capability_status.safe_report(...)`（内部吞异常）。
- 记账记在"**一次调用**"层面，不是"一次尝试"：底层有重试循环，按尝试记会虚高。
- 区分"用户主动中断"与"真故障"：流式对话里客户端断开抛 `GeneratorExit`，
  **不能**上报成降级（见 `model_service.OpenAICompatibleProvider.chat_stream`）。
- 记账/上报挂在 `ModelProvider.chat` / `chat_stream` 上，**不要**挂
  `_call_openai_compatible_api`——后者被 `vision` 复用，会把识图误记成文本调用。
- 预算超限**只拦后台预生成**（`services/quiz_prewarm.py`），不拦学生交互。

### 排查入口

```bash
curl -s http://localhost:8000/health/ready
```

看 `external_capabilities.<能力>.ok / last_error` 就知道"哪一环此刻坏了、为什么"，
不用翻日志。

---

## 11. 出题的来源分层（2026-10-02 新增）

各校教材版本不同，本地知识库只覆盖学生自己上传的资料。`note_service.generate_quiz`
因此分三层降级，**每层都带来源标记**（写入 `quiz_questions.source`，前端显示成标签）：

| 层 | 条件 | source |
|---|---|---|
| 1 | 知识库有证据（**且不联网**） | `knowledge_base` / `note` / `quiz` |
| 2 | 证据不足 + Tavily 可用 | `web_search`（带回素材 URL） |
| 3 | 联网不可用或没命中 | `general_knowledge` |

**必须保持"不冒充"**：通用题不能显示成"你的资料里有这道题"。
联网素材只当素材用——Tavily 的 `include_answer` 语言不稳定（中文查询会回英文摘要），
且网页答案本身经常是错的，所以答案一律由我们自己的模型**重新推导核对**。

配置在 `.env`：`TAVILY_API_KEY` / `WEB_SEARCH_ENABLED` / `WEB_SEARCH_MAX_RESULTS` /
`WEB_SEARCH_PREGEN_POINTS` / `WEB_SEARCH_PREGEN_COUNT`。
预生成由建库/重建成功后触发（`api/routes/knowledge.py` 的 `_schedule_quiz_prewarm`），
每个学科在一个进程内只跑一次。

