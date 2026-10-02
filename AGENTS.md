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

---

## 2. 必须用 pytest，不能用 unittest

```bash
python -m pytest tests        # ✅ 唯一正确方式
python -m unittest discover   # ❌ 会出问题，见下
```

**原因（我亲自踩过）**：`tests/conftest.py` 通过 `pytest_configure` 做数据隔离，
把 `EDU_DATA_DIR` 指向独立测试目录（默认 `D:\项目\RAG\git_text_test_data`），
并把 `VISION_MIN_IMAGE_BYTES` 设为 0（否则测试用的合成小图会被"过小图片"过滤跳过）。

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
- `try/except` 不要吞掉 `ImportError`。本项目有过静默吞掉 ImportError 导致
  整个功能（**知识点增量入库**）永久失效、且无任何报错的案例：
  `knowledge_expander` 长期调用一个**从未被实现**的 `add_documents_to_store`，
  ImportError 被 `except Exception` 吞掉，功能坏了一年都没人发现。
  该函数已于 2026-10-02 补上，并有回归测试 `tests/test_incremental_ingest.py` 锁定。
  **教训**：调用外部函数前先确认它存在；新增功能必须配测试，否则静默失效无从察觉。
- 新增"静默降级"分支时**必须打日志**，且降级不应伪装成成功。

---

## 8. 已知未完成项（不要误以为是 bug）

- `rag/multimodal/pdf/processors/vision_processor.py` 与
  `rag/enrichment/multimodal_enricher.py` 是**两套并行的视觉实现**，
  PDF 管线只接了后者。统一它们是待办。
- `材料力学` 课件的 PDF 文本层只有约 3,900 字符（24 页里 131 张图），
  内容量天然极低，检索效果差是**数据源**问题，不是代码问题。
- 部分测试对"哪个 provider 已配置"有环境耦合（依赖 `DASHSCOPE_API_KEY` 等），
  在只配 DeepSeek 的环境下会失败。属于已知项。

---

## 9. 快速自检清单（提交前）

```bash
python -m ruff check .                    # 0.1 秒。当前有 ~25 条存量（见下），
                                          # 你改的文件若引入**新**问题必须修掉
python -m pytest tests/test_<你改的模块>.py   # 相关文件
python -m pytest tests                    # 全量（发版前）
git status --short                        # 确认没夹带临时脚本/密钥/数据文件
```

**关于 ruff 的 ~25 条存量**：主要是 `F821 Undefined name`（前向引用注解）、
`F841 未使用变量`、`F811 重复定义`。它们**不影响运行**，但其中几条值得顺手清理：
- `services/auth_service.py:30` 重新定义了已导入的 `_DATA_DIR`
- `api/routes/models.py:132` 的 `provider` 赋值后从未使用（与"模型切换没生效"相关）
- `rag/vector_store.py:434` 的 `metadatas` 未使用

目标是**存量不增加**：在你改动的文件里不引入新的 ruff 报错即可。

提交信息里写清：**改了什么、为什么、怎么验证的**。
