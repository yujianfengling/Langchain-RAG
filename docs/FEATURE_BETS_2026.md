# 从「RAG 问答」到「AI 学习操作系统」

> 面向：智慧教育 RAG 系统（已有 Tutor Agent、学习地图、教师端、本地 ASR）
> 目的：不修 bug，只回答一个问题——**下一步做什么功能，才能让这套系统明显高一个档次**
> 方法：扫一遍 2026 年同类开源项目，挑出可抄的范式，再落到本仓库的具体模块
> 日期：2026-10-24
>
> **2026-10-24 状态更新**
> - **A2 Contextual Retrieval + Cross-Encoder 重排：已实现**（默认确定性上下文前缀；`CONTEXTUAL_LLM=1` 启用按章节的模型上下文；`RERANK_ENABLED=true` 启用 Cross-Encoder）。
> - **A1 页面级检索 bbox：已实现**（block 级坐标 + 页面图高亮；PPTX/视觉向量仍未做）。
> - **B1 真 FSRS：已实现**（stability/difficulty/reps/lapses，独立 `fsrs.db`；固定 3→7→16 仅作降级回退）。
> - **C1 拍照批改、C2 课堂实时答题、C3 AI 播客：明确不做，已从本路线图移除。**
> - 其余半成品的缺口见每节末尾的「验收/边界」；不再把这三项当作待办。

---

## 0. 先给结论

先说一个可能不中听的判断：**你这套系统现在不是「功能不够」，而是「功能很多，但没有一个能让人一眼记住、并且能证明有效的杀手级闭环」。**

我把 GitHub 上同类项目扫了一圈（NotebookLM 的开源替代、DeepTutor、OATutor、Anki/FSRS、RAGFlow、MinerU、ColPali、Khanmigo/LearnLM……），发现 2026 年教育 AI 的竞争点已经变了：

1. **从「能回答」到「能产出」**：NotebookLM 真正留住人的不是问答，是一键把资料变成音频、思维导图、卡片、测验。
2. **从「能对话」到「能诊断」**：OATutor 用 BKT 估掌握度，Anki 用 FSRS 排复习——效果来自「练什么、什么时候练」，不是回答多漂亮。
3. **从「学生玩具」到「老师工具」**：Gradescope 的批改流、Kahoot 的课堂流，才是学校愿意付费的部分。

你的机会不是再做一个 Chatbot，而是把这三件事串成一条链：

> **资料 → 讲解 → 练习 → 错因 → 变式 → 间隔复习 → 掌握证据 → 教师干预**

如果只做 5 件事，我建议：

| 顺序 | 功能 | 一句话理由 | 成本 |
|---|---|---|---|
| 1 | 页面级视觉检索 + 原文定位 | 直接解决「图片型课件等于没入库」 | 10–15 人日 |
| 2 | 错因诊断 + 变式题 + FSRS | 把「做过」变成「学会」，效果最硬 | 20–25 人日 |
| 3 | 苏格拉底 / 费曼回讲模式 | 成本最低的差异化，改提示词 + 策略即可 | 3–5 人日 |
| 4 | 拍照批改 | 老师端最刚需，演示也最直观 | 15–20 人日 |
| 5 | AI 播客 / 5 分钟微课 | 最低成本的「哇」点，适合传播 | 10–15 人日 |

---

## 1. 外面的人做对了什么

| 项目 | 它把什么做到了极致 | 你可以直接抄的 | 链接 |
|---|---|---|---|
| **NotebookLM / Open Notebook** | 资料 → 多模态成果：Audio Overview、思维导图、卡片、带引用的问答 | 「成果」而不是「聊天」作为主界面；每条结论都能点回原文 | [Open Notebook](https://github.com/lfnovo/open-notebook)（40k★） |
| **DeepTutor** | 终身个性化导师：统一上下文、原生 tool-calling、学习地图、成果版本 | 一个统一上下文贯穿问答/工具/地图；能力只决定提示词与工具子集 | [HKUDS/DeepTutor](https://github.com/HKUDS/DeepTutor)（42k★） |
| **OATutor** | 开源自适应教学系统：BKT 掌握度、自适应选题、LTI 接入 | BKT 的 4 参数更新；「最近发展区」选题；LTI 进 LMS | [OATutor](https://www.oatutor.io/)（[源码](https://github.com/CAHLR/OATutor)） |
| **Anki / FSRS** | 记忆模型调度：稳定性、难度、可提取性，按复习历史优化参数 | 用 FSRS 替换固定 1/3 天复习；4 档评分；参数按用户优化 | [fsrs4anki](https://github.com/open-spaced-repetition/fsrs4anki)（4.1k★） |
| **RAGFlow / MinerU / Docling** | 版面感知解析：OCR、表格、公式、阅读顺序、页/块定位 | 页级与 block 级 locator；表格/公式结构化；不要重写解析器 | [RAGFlow](https://github.com/infiniflow/ragflow)（93k★）、[MinerU](https://github.com/opendatalab/MinerU)（82k★）、[Docling](https://github.com/docling-project/docling)（69k★） |
| **ColPali / ColQwen** | 视觉文档检索：把页面当图片做 late interaction，绕开 OCR | 页面级多模态索引；图、表、公式、版式一起检；ViDoRe 评测 | [ColPali](https://github.com/illuin-tech/colpali)（2.8k★） |
| **Khanmigo / LearnLM / MathDial** | 苏格拉底式教学：不直接给答案，按提示阶梯引导 | 提示阶梯、追问已知、让学生先表达；教学策略可评测 | [LearnLM 论文](https://arxiv.org/abs/2412.16429)、[MathDial 论文](https://arxiv.org/abs/2305.14536) |
| **Gradescope / Mathpix / Pix2Text** | 拍照/手写批改：切题、公式 OCR、按步骤给分、教师复核 | 低置信度进人工队列；保留原图与改判痕迹；步骤分 | [Pix2Text](https://github.com/breezedeus/Pix2Text)（3.3k★） |
| **Kahoot / open-kahoot** | 课堂实时答题：扫码、倒计时、实时分布、追赶一题 | 6 位码/二维码 + SSE + 实时柱状图；结果直接进学情 | [open-kahoot](https://github.com/soleilvermeil/open-kahoot) |
| **Langfuse / RAGAS** | 可观测与评测：每次调用可追踪，检索/生成指标可回归 | 黄金集 + CI 门禁 + trace；没有它，前面所有功能都会悄悄退化 | [Langfuse](https://github.com/langfuse/langfuse)（36k★）、[RAGAS](https://github.com/vibrantlabsai/ragas)（16k★） |

### 三个可以立刻借用的「范式」

**范式一：NotebookLM 的「资料 → 成果」**
学生上传一章课件，期待的不是「问一句答一句」，而是「给我一份能带走的东西」：5 分钟音频、一张思维导图、20 张卡片、一套自测。你的 artifacts_service 已经有「成果」的雏形，缺的是**多模态形态**和**一键生成**。

**范式二：DeepTutor 的「一个上下文 + 工具」**
你已经在 tutor_agent.py / tutor_tools.py 里做了类似的事。下一步不是加更多工具，而是让**同一个上下文**同时服务问答、地图、练习、成果、计划——学生换一个入口，不该重新介绍自己。

**范式三：OATutor / Anki 的「调度」**
教学效果的大头来自「在什么时候、练哪一道」，而不是「讲得多好」。你现在的复习规则是固定的 1/3 天，所有学生同一节奏；这是最容易被 FSRS 拉开差距的地方。

---

## 2. 你的系统现在卡在哪

这些不是 bug，是「价值漏点」。按影响排序：

### 2.1 图片型课件，文本 RAG 基本失效

材料力学的 PDF 文本层只有约 3,900 字符，24 页里 131 张图。现有链路是「抽文本 → 切 chunk → 嵌入」，图片只在上游被 Vision 描述成文本，**版式、图表、公式的空间关系全丢了**。学生问「第三页那个受力图」，系统只能召回一段没有上下文的描述。

→ 这是 **页面级视觉检索** 要解决的问题，也是我认为的 P0 第一名。

### 2.2 Rerank 是「用 Embedding 算余弦」，不是真 Reranker

PROJECT_STATUS.md 写得很清楚：轻量实现，复用 Embedding 做余弦重排，没有独立 Reranker。这在同义改写、长 query、多跳问题上会明显掉点。

→ 换 Cross-Encoder（bge-reranker-v2-m3 / Qwen3-Reranker），top 50 重排到 10，成本可控。

### 2.3 复习是「固定间隔」，不是「记忆模型」

review_service.py 的 REVIEW_RULES 是 first_interval_days=1、recheck_interval_days=3。好学生和薄弱学生同一节奏；答对得快、答对得慢，也同一节奏。

→ FSRS：每个知识点一张卡，按「对错 + 用时 + 是否看提示/答案」更新 stability / difficulty，再算下次复习日。

### 2.4 错题只有「对/错」，没有「为什么错」

quiz_attempts 记了答案、得分、用时、是否看提示，但没有错因。错题本只能重做原题，不能告诉学生「你是公式记错了，还是审题漏了条件」。

→ 错因分类 + 变式题，是「错题本」升级成「错题诊所」的关键。

### 2.5 Tutor 是「答案机器」，不是「导师」

tutor_agent.py 的硬性规则第 6 条写着「先给结论再给依据」。这对「查资料」是对的，对「学习」是反的——学生一问，答案就到手了，没有思考发生。

→ 加一个「引导模式」：先问学生怎么想，再按提示阶梯给，最后让学生回讲。

### 2.6 老师端有数据，但没有「一键动作」

class_analytics.py 能算出班级薄弱点、常见错题，但结论停在页面上：老师看完还得自己去找题、布置、跟踪。teacher_grading.py 只能对已有作答改判，没有「拍照上传 → 切题 → 批改」的入口。

→ 学情分析的下一步是「一键干预」：给这个薄弱点生成 10 分钟补救练习，发给这 12 个人，3 天后自动复测。

### 2.7 成果以文本为主，缺音频/可视化/交互

artifacts_service 存的是结构化文本。NotebookLM 已经证明，音频和可视化是「使用时长」和「传播」的放大器。

→ AI 播客 + AI 白板/交互图，是性价比最高的两个新形态。

### 2.8 评测集太小，没有回归门禁

tests/eval 目前是 8 条 RAG + 9 条干扰项 + loop/agent/voice 各若干，能证明链路通，但不足以证明「改了检索没退化」。

→ 每个学科 50–100 题黄金集，Recall@5 / Faithfulness / 引用准确率进 CI。

---

## 3. 13 个功能提案

先看优先级矩阵（★ 越多越值得先做）：

| # | 功能 | 主要用户 | 价值 | 工作量 | 依赖 | 优先级 |
|---|---|---|---|---|---|---|
| A1 | 页面级视觉检索 + 原文定位 | 学生 | ★★★★★ | 10–15 人日 | 解析层补 page/bbox | P0 |
| A2 | Contextual Retrieval + Cross-Encoder | 学生 | ★★★★☆ | 5–8 人日 | 重建索引 | P0 |
| B1 | FSRS 间隔复习 | 学生 | ★★★★★ | 8–12 人日 | 复习/作答数据 | P0 |
| B2 | 错因诊断 + 变式题 | 学生 | ★★★★★ | 10–15 人日 | 题库 + LLM | P0 |
| B3 | 苏格拉底 + 费曼回讲 | 学生 | ★★★★☆ | 3–5 人日 | 改 prompt/策略 | P0 |
| C1 | 拍照批改 | 老师 | ★★★★★ | 15–20 人日 | Vision + 批改流 | P1 |
| C2 | 课堂实时答题 | 老师 | ★★★★☆ | 5–8 人日 | SSE + 班级码 | P1 |
| C3 | AI 播客 / 微课 | 学生 | ★★★★☆ | 10–15 人日 | TTS | P1 |
| C4 | AI 白板 / 交互可视化 | 学生 | ★★★★☆ | 10–15 人日 | 前端画布 | P1 |
| D1 | 智能组卷 | 老师 | ★★★★☆ | 10–15 人日 | 题库统计 | P1/P2 |
| E1 | 知识追踪 BKT | 学生/老师 | ★★★☆☆ | 10–15 人日 | 作答数据 | P2 |
| E2 | 评测与可观测性 | 团队 | ★★★★★ | 5–8 人日 | 黄金集 | P0（底座） |
| F1 | 课堂录音 → 结构化笔记 | 学生 | ★★★★☆ | 10–15 人日 | 本地 ASR | P2 |

### A. 资料底座：让「资料真的可用」

#### A1. 页面级视觉检索 + 原文定位（P0，10–15 人日）

**参考**：ColPali / ColQwen（[论文](https://arxiv.org/abs/2407.01449)）、RAGFlow DeepDoc、MinerU、Docling。

**为什么**：你的场景里大量信息在图片、表格、公式、版式里，文本层可能只有几千字符。只做文本 chunk，等于把这些内容判了死刑。

**MVP 怎么做**（不急着上 ColPali，先做「页面级多模态索引」）：

1. **导入时逐页渲染**：120–150 DPI，存 WebP，路径 data/pages/<subject>/<doc>/p{n}.webp；体积可控，按需加载。
2. **每页三份表示**：
   - OCR 文本（pytesseract 或 Qwen-VL）；
   - 视觉摘要（复用现有 Vision：这一页讲了什么、有哪些图/表/公式）；
   - block 级 bbox（PyMuPDF 能拿；扫描页用 OCR 词框）。
3. **建页级索引**：page_index 表存 page_no / ocr_text / caption / image_path / bbox_json / doc_id；文本检索仍走原 chunk，页面检索走 OCR+摘要。
4. **检索融合**：文本 chunk top-30 + 页面 top-10，RRF 融合，再 cross-encoder 重排到 8；返回 page_image_url + bbox。
5. **前端**：用 pdf.js 做双栏阅读器；引用点击跳到对应页并高亮 bbox；支持「选中一段/一块 → 直接问」。
6. **有 GPU 再升级**：把页级编码器换成 ColQwen2.5 / ColSmol，做真正的 late interaction；没有 GPU 时，「OCR + 视觉摘要」的文本代理已经能覆盖大部分课件问答。

**落点**：
- 解析：rag/loaders/pdf_loader.py（补 page/bbox）
- 服务：新建 services/page_index.py；services/knowledge_service.py 建库时调用
- 接口：api/routes/knowledge.py 增加页面图/定位字段
- 前端：frontend/src/components/TutorWorkspace/SourcePanel.tsx + 新 PDF 阅读器

**验收**：50 题黄金集（含 20 道图片题）Recall@5 ≥ 0.85；引用可定位率 ≥ 95%；「资料未覆盖」误报率下降 50%。

**风险**：首建时间与存储。用 WebP + 懒加载；页面索引支持单独重建，不拖垮文本索引。

---

#### A2. Contextual Retrieval + 真正的 Cross-Encoder（P0，5–8 人日）

**参考**：Anthropic [Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval)（失败检索率降 49%，加重排降 67%）、BGE Reranker、Qwen3-Reranker。

**为什么**：现在每个 chunk 独立嵌入，「这段在整本书的哪一章、回答什么问题」丢了；rerank 又是 embedding 余弦，不是交叉编码。

**怎么做**：

1. 建库时，为每个 chunk 生成 1–2 句上下文前缀（「这是《材料力学》第 3 章弯曲应力下的一段，正在推导最大正应力公式」），拼在原文前再嵌入，同时用于 BM25。
2. 用便宜模型批量生成，按 chunk hash 缓存；升级时加 CONTEXTUAL_VERSION，写进 meta.json。
3. 检索：向量 + BM25 各取 top 50 → RRF → cross-encoder（BAAI/bge-reranker-v2-m3，CPU 可跑）top 50→10。
4. 保留可关闭开关：小机器/低延迟场景只开 contextual，不开 rerank。

**落点**：rag/text_splitter.py、rag/embedding.py、rag/hybrid_retriever.py、services/knowledge_service.py 的 meta.json。

**验收**：黄金集 retrieval failure rate 相对下降 ≥ 30%；P95 延迟增加 < 300ms；重建可回滚。

**成本**：一次性 LLM 调用。1000 个 chunk 大约几十万 token，DeepSeek 价格下是几块钱级别。

---

### B. 学习闭环：让「做过」变成「学会」

#### B1. FSRS 间隔复习（P0，8–12 人日）

**参考**：[fsrs4anki](https://github.com/open-spaced-repetition/fsrs4anki)（4.1k★）、Anki。

**为什么**：固定 1/3 天无法区分「真记住」和「刚看过」。FSRS 用 stability / difficulty / retrievability 三个量，按每次复习更新，能显著提升长期保持率。

**怎么做**：

1. 每个「用户 × 知识点 × 题型」建一张 review_cards：stability、difficulty、last_review、due、reps、lapses。
2. 评分映射（4 档）：
   - 答错 / 看答案 → Again
   - 答对但超时 / 看提示 → Hard
   - 独立答对 → Good
   - 快速且独立答对 → Easy
3. 用 fsrs Python 包算下次 due；冷启动用全局默认参数，复习 ≥ 30 次后按用户历史优化（先离线批处理，不要在线训练）。
4. 保留你现有的「单向从严」：自评答错采信、答对不采信；FSRS 只负责「什么时候再问」，不负责「算不算掌握」。
5. 前端任务页显示「为什么是今天」：上次答对 + 间隔 6 天 + 记忆可提取性 0.82。

**落点**：services/review_service.py、services/learning_data.py（新表）、api/routes/quiz.py、frontend/src/pages/TasksPage.tsx。

**验收**：30 天保持率相对固定间隔 +10%；复习完成率 +15%；逾期率下降。

**风险**：数据稀疏时 FSRS 参数不稳。先用默认参数，把「个性化」留到有足够复习记录之后。

---

#### B2. 错因诊断 + 变式题（P0，10–15 人日）

**参考**：MathDial 的脚手架式对话、Khanmigo 的错误分析、Pix2Text 的手写公式识别。

**为什么**：错题本现在只有「原题 + 正确答案」。学生二次遇到同一知识点还是错，因为没人告诉他「错在哪一类」。

**怎么做**：

1. **错因分类（先 6 类，别贪多）**：
   concept_confusion 概念混淆 / misread 审题偏差 / formula_recall 公式记错 / calculation 计算失误 / step_omission 步骤遗漏 / expression 表达不规范。
2. LLM 结合「题目 + 学生答案 + 正确答案」给 1 个主因 + 一句证据（引用学生原话），存 quiz_attempts.error_code / error_evidence；学生可以申诉/改判。
3. **变式题**：同知识点、换情境/数字/问法；数学题用 SymPy 验证答案；生成 1–3 道，答案不下发；做完再更新掌握度。
4. **错题诊所 UI**：原题 → 我的答案 → 错因 → 正确思路（分步）→ 变式 1 道 → 下次复习时间。
5. 班级侧聚合「高频错因」，给老师一个「这节课 60% 的人错在『公式记错』」的结论。

**落点**：services/quiz_service.py、services/mastery_service.py、services/note_service.py、frontend/src/components/QuizList。

**验收**：同知识点二次错误率 -20%；变式题完成率 > 60%；学生「诊断准」评分 > 4/5。

**风险**：错因标签体系不要一开始做 20 类；先 6 类跑 200 道题，再按混淆矩阵合并。

---

#### B3. 苏格拉底 + 费曼回讲（P0，3–5 人日）

**参考**：Khanmigo、[LearnLM](https://arxiv.org/abs/2412.16429)、[MathDial](https://arxiv.org/abs/2305.14536)。

**为什么**：这是成本最低、差异化最明显的一刀。现在的 prompt 是「先给结论」，学生没有思考就被喂饱了。

**怎么做**：

1. 新增 mode="socratic"（或在 explain / practice 里加开关）。提示阶梯：
   L0 澄清题意 → L1 问已知 → L2 给类比 → L3 给第一步 → L4 给关键一步 → L5 完整解
   每轮最多问 1 个问题；学生连续两次明确要答案，才进 L5，并记录「已揭晓」。
2. **程序侧硬约束**：提示层级、揭晓次数、不得直接给最终答案（除非触发条件）；写进 tutor_turn_events，前端显示「提示 1/3」。
3. **费曼回讲**：做完题后，让学生用「给小学生讲」的方式说一遍；AI 按四维评分：概念准确 / 步骤完整 / 用自己的话 / 有例子；指出一个漏洞，再给示范。
4. 回讲结果作为「掌握证据」的一种（权重低于独立作答，高于「看懂了」）。

**落点**：services/tutor_agent.py（能力提示词）、services/tutor_session.py（mode 字段）、frontend/src/components/TutorWorkspace。

**验收**：会话中「学生先表达」比例 > 70%；同知识点迁移题 +10%；学生满意度不降。

**风险**：低耐心学生会烦。给「直接看答案」按钮，但记录，并在一段时间后安排复测。

---

### C. 内容与体验：让「成果」可带走、可传播

#### C1. 拍照批改（P1，15–20 人日）

**参考**：Gradescope 的「AI 初评 + 教师复核」、Mathpix / [Pix2Text](https://github.com/breezedeus/Pix2Text) 的公式 OCR。

**为什么**：老师最痛的不是「没有数据」，是「批不完」。拍照批改是老师端最刚需、演示最直观的功能。

**怎么做**：

1. 学生/老师拍照上传 → 去畸变、裁切、增强 → 公式 OCR（Pix2Text 或 Qwen-VL）→ 按题切分。
2. 按 rubric 评分（含步骤分）；低置信度进教师复核队列；教师改判保留原判定、理由、时间（你已有 teacher_grading.py 的底子）。
3. 发布后：错因/知识点自动入库；班级错题热力图；学生看到「哪一步被扣分」。
4. 先支持「数学/物理计算题」，不要一上来做全学科。

**落点**：services/teacher_grading.py、services/assignment_service.py、新建 services/submission_service.py、frontend/src/components/Teacher/AssignmentsPanel.tsx。

**验收**：批改时间 -50%；AI 与教师一致率 > 85%；复核率 < 20%；学生反馈覆盖率 100%。

**风险**：手写识别不稳。必须 human-in-the-loop；保留原图；置信度低的题不要自动发布。

---

#### C2. 课堂实时答题（P1，5–8 人日）

**参考**：Kahoot 的课堂节奏、[open-kahoot](https://github.com/soleilvermeil/open-kahoot)。

**为什么**：这是最容易在 5 分钟内让听众「哇」的功能，而且能直接复用你现有的题库、班级码、SSE。

**怎么做**：

1. 教师选 5–10 题 → 开直播 → 6 位码/二维码 → 学生手机作答（不需要装 App）。
2. 实时柱状图、正确率、高频错因；教师可「追一题」或「放慢讲」。
3. 结束后结果自动进 class_analytics，不需要再录一次。

**落点**：api/routes/teacher.py、services/quiz_service.py、新建 live_sessions 表、frontend/src/components/Teacher。

**验收**：到课参与率 > 80%；教师周使用频次；演示场景 0 故障。

**风险**：课堂网络。做「断线重连 + 本地缓存 + 结束后补交」。

---

#### C3. AI 播客 / 5 分钟微课（P1，10–15 人日）

**参考**：NotebookLM Audio Overview、[Open Notebook](https://github.com/lfnovo/open-notebook)、[CosyVoice](https://github.com/FunAudioLLM/CosyVoice) / [IndexTTS](https://github.com/index-tts/index-tts)。

**为什么**：学生不可能一直看屏幕。把一章资料变成 5 分钟双人对话，通勤、跑步、睡前都能「学」，而且天然适合传播。

**怎么做**：

1. 选一章/一个知识点 → 生成双人脚本（讲师 + 学生，3–8 分钟，短句、有例子、有停顿），每个论点带 source_id。
2. TTS → 音频 + 字幕 + 章节标记；支持 0.8x/1.2x；缓存按「内容 hash + 声音」。
3. MVP 先用 edge-tts/浏览器 TTS 跑通；质量不够再上本地 CosyVoice 2（隐私、成本可控）。
4. 不要每份资料都预生成：按需生成 + 热门缓存。

**落点**：services/artifacts_service.py、新建 services/audio_overview.py、api/routes/audio.py、frontend/TutorWorkspace。

**验收**：完播率 > 50%；生成成功率 > 95%；单条成本 < ¥0.5；分享率。

**风险**：双人对话容易「念稿」。脚本要有人设冲突（学生问蠢问题、老师纠正），不要写成论文摘要。

---

#### C4. AI 白板 / 交互可视化（P1，10–15 人日）

**参考**：[tldraw](https://github.com/tldraw/tldraw)（51k★）、[GeoGebra](https://www.geogebra.org/)、[PhET](https://phet.colorado.edu/)、[Manim](https://www.manim.community/)、[MathLive](https://mathlive.io/)。

**为什么**：数学/物理的「讲明白」靠图，不靠文字。现在的回答是 Markdown + LaTeX，学生还是要在脑子里构图。

**怎么做**：

1. 先做三类模板：函数图像、平面几何、受力分析。LLM 只负责抽参数（函数式、点坐标、力的大小方向），前端用 JSXGraph/ECharts 画可拖拽的交互图。
2. Tutor 可以在 tldraw 画布上「边讲边画」：箭头、辅助线、标注；学生也能手写，MathLive 输入公式。
3. 需要视频时，再用 Manim 渲染 MP4（沙箱执行，模板参数化，不执行任意代码）。
4. 成果存成 scene_spec（JSON），可重新打开、改参数，不是死图片。

**落点**：frontend/src/components/TutorWorkspace 新 tab、services/artifacts_service.py 存 scene spec。

**验收**：使用率、概念题提升、成果二次打开率。

**风险**：不要做「任意代码生成」。模板 + 参数，安全可控。

---

### D. 老师端：让老师「省时间」而不是「看报表」

#### D1. 智能组卷（P1/P2，10–15 人日）

**参考**：经典测量理论 / IRT、ASSISTments 的题目统计、现有题库。

**为什么**：老师现在能生成题目，但不能「按蓝图组一套卷」：知识点覆盖、难度分布、Bloom 层级、题型配比、时间长度。

**怎么做**：

1. 输入：范围、时长、难度分布、Bloom 配比、题型；输出：双向细目表 + 试卷 + 答案卡。
2. 约束求解：每个知识点至少 1 题、不重复、难度匹配、总时长匹配；题库不够时生成补题。
3. 用历史作答算 p 值/区分度（先经典指标，数据够了再 2PL）；新题标「未校准」。
4. 导出 Word/PDF；支持 A/B 卷。

**落点**：services/assignment_service.py、services/teaching_data.py、新建 services/paper_builder.py。

**验收**：组卷时间 < 2 分钟；教师修改率 < 30%；知识点覆盖率 100%。

---

### E. 数据与可信：让「牛逼」可证明

#### E1. 知识追踪 BKT（P2，10–15 人日）

**参考**：[OATutor](https://github.com/CAHLR/OATutor) 的 BKT、[pyBKT](https://github.com/CAHLR/pyBKT)。

**为什么**：你现在的掌握度是启发式加权。BKT 用 4 个参数（prior / learn / guess / slip）按每次作答更新，能给出「掌握概率 + 置信度」，还能驱动「最近发展区」选题（成功率 0.6–0.8）。

**怎么做**：

1. 每个知识点一个 BKT 模型；冷启动用全局先验；按学生作答在线更新。
2. 先 **shadow mode**：和现有 mastery_service 并行跑，只记录不展示；两周后对比预测 AUC 和教师判断。
3. 达标后再用于选题/复习优先级；不要一上来替换现有口径（你的「单向从严」是产品资产）。

**落点**：services/mastery_service.py 旁路、services/learning_data.py。

**验收**：预测下次答对 AUC > 0.7；与教师判断一致率 > 75%。

---

#### E2. 评测与可观测性（P0 底座，5–8 人日）

**参考**：[RAGAS](https://github.com/vibrantlabsai/ragas)、[Langfuse](https://github.com/langfuse/langfuse)。

**为什么**：没有它，你不知道「这次改动是变好还是变坏」。你的 tests/eval 已经有雏形，但样本太少、指标太粗。

**怎么做**：

1. 每个学科 50–100 题黄金集：问题、标准答案、证据页/片段、难度、题型。
2. CI 指标：Recall@5、Context Precision、Faithfulness、引用准确率、无依据回答率、P95 延迟、单次成本。
3. Langfuse（或自建 trace 表）记录每次 tutor turn 的检索、工具、模型、token、耗时；prompt 版本化。
4. 发版门禁：核心指标退化 > 5% 直接 fail。

**落点**：tests/eval/、services/usage_ledger.py、新建 services/trace.py。

**验收**：每次发版有指标报告；线上问题能在 10 分钟内定位到「检索/模型/工具/提示词」哪一层。

---

### F. 延伸玩法（P2，有余力再做）

- **课堂录音 → 结构化笔记**：你已经有本地 FunASR，这是现成优势。浏览器录音 → ASR + 说话人分离 → 对齐知识地图 → 提纲/重点/待解决问题/自动小测。参考 Otter、NotebookLM。落点：services/local_speech.py。
- **口语陪练**：跟读 + 情景对话 → 音素级对比 → 发音/流利度/语法反馈。先英语，再普通话。参考 Whisper/FunASR + Azure Pronunciation Assessment。
- **成果导出/多端**：错题导出 Anki apkg（带 FSRS 参数）、计划导出 ICS、笔记导出 Markdown/PDF；PWA 支持拍照/语音。参考 [Anki](https://apps.ankiweb.net/)。
- **LTI 1.3 / Moodle 接入**：学校采购的最后一公里。参考 [LTI 1.3](https://www.imsglobal.org/spec/lti/v1p3)、OATutor 的 Canvas 集成。

---

## 4. 按目标选路线（别一次全做）

### 路线 A：两周内要「演示效果」

优先：**A1 视觉检索（先做页面图 + 引用定位）→ C2 实时课堂 → C3 AI 播客 → C1 拍照批改 demo**。
这四件事在 5 分钟演示里都能被看见；FSRS/BKT 这种「效果型」功能演示不出来。

### 路线 B：要「真实学习效果」

优先：**E2 评测基线 → B2 错因诊断 → B1 FSRS → B3 苏格拉底 → A2 检索增强**。
这条路线不炫，但能让「用了这个系统的班级，月考成绩/保持率更好」有数据支撑。

### 路线 C：要「老师愿意用/学校愿意买」

优先：**C1 拍照批改 → D1 智能组卷 → 学情「一键干预」→ LTI 接入**。
老师不会为「聊天更聪明」付费，但会为「少批 2 小时作业」付费。

---

## 5. 90 天路线图

| 阶段 | 时间 | 交付 | 验收 |
|---|---|---|---|
| 0. 基线 | 第 1 周 | 黄金集 50 题 + RAGAS/Langfuse 指标 + 苏格拉底模式 MVP | 有一张「改前」指标表；引导模式可开关 |
| 1. 资料底座 | 第 2–4 周 | 页面渲染/索引 + 引用 bbox + PDF 阅读器；Contextual Retrieval + reranker | Recall@5 ≥ 0.85；引用可定位 ≥ 95% |
| 2. 学习闭环 | 第 5–7 周 | FSRS + 错因 6 类 + 变式题 + 错题诊所 | 30 天保持率 +10%；二次错误率 -20% |
| 3. 老师端 | 第 8–10 周 | 拍照批改 MVP + 实时课堂 + 学情一键干预 | 批改时间 -50%；课堂参与率 > 80% |
| 4. 传播与扩展 | 第 11–12 周 | AI 播客 + 白板模板 + 组卷 MVP | 完播率 > 50%；组卷 < 2 分钟 |

> 排序逻辑：**先让检索可信，再让学习有效，再让老师省时，最后才做好玩的。** 反过来做，很容易得到一堆「演示很酷、用完不留」的功能。

---

## 6. 不要做的 5 件事

1. **从零自研 GraphRAG / 大知识图谱**。微软 [GraphRAG](https://github.com/microsoft/graphrag) 已进入维护模式；你已经有 graph + 学习地图。要做增量，用 LightRAG / Graphiti 这类现成方案，别重造。
2. **多智能体「学习小镇」**。五个角色聊天演示好看，教学价值难证明。一个带工具的 tutor + 明确策略，胜过五个互相客套的 agent。
3. **全自动判分、无人复核**。教育场景错误成本高；「AI 初评 + 教师复核」才是正解。
4. **为每个新功能加一个一级导航**。你的信息架构已经是 3+4 个入口；新功能应该嵌进「资料 / 任务 / 成果」三个容器，而不是再造一个页面。
5. **自研基座 / 盲目微调**。除非你有 10 万条标注；先把检索、策略、评测做好，换模型就能涨。

---

## 7. 怎么证明「变牛逼了」

| 维度 | 指标 | 目标 |
|---|---|---|
| 学习效果 | 7/30 天保持率、迁移题得分、二次错误率、学习时长 | 30 天保持率 +10%；二次错误率 -20% |
| 检索质量 | Recall@5、Context Precision、引用准确率、无依据回答率 | Recall@5 ≥ 0.85；引用准确率 ≥ 95% |
| 产品体验 | P95 延迟、单次成本、完播率、成果二次打开率 | P95 < 8s；单次成本 < ¥0.5 |
| 教师价值 | 批改时长、组卷时长、周活、班级覆盖率 | 批改 -50%；组卷 < 2 分钟 |
| 业务 | 试用转化、学校部署数、次周留存 | 先定基线，再谈增长 |

---

## 8. 参考项目清单

| 项目 | 星标 | 许可 | 用途 |
|---|---|---|---|
| [HKUDS/DeepTutor](https://github.com/HKUDS/DeepTutor) | 42k | Apache-2.0 | 统一上下文、工具调用、学习地图 |
| [infiniflow/ragflow](https://github.com/infiniflow/ragflow) | 93k | Apache-2.0 | 版面感知解析、DeepDoc |
| [opendatalab/MinerU](https://github.com/opendatalab/MinerU) | 82k | 自定义 | PDF/Office → Markdown/JSON，页/块定位 |
| [docling-project/docling](https://github.com/docling-project/docling) | 69k | MIT | 文档转换、表格/版面 |
| [illuin-tech/colpali](https://github.com/illuin-tech/colpali) | 2.8k | MIT | 视觉文档检索（ColPali/ColQwen/ColSmol） |
| [lfnovo/open-notebook](https://github.com/lfnovo/open-notebook) | 40k | MIT | NotebookLM 开源替代、音频/成果 |
| [CAHLR/OATutor](https://github.com/CAHLR/OATutor) | 269 | MIT | BKT、自适应、LTI |
| [CAHLR/pyBKT](https://github.com/CAHLR/pyBKT) | 289 | MIT | BKT 实现 |
| [open-spaced-repetition/fsrs4anki](https://github.com/open-spaced-repetition/fsrs4anki) | 4.1k | MIT | FSRS 调度与优化 |
| [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG) | 40k | MIT | 轻量图检索（可选） |
| [getzep/graphiti](https://github.com/getzep/graphiti) | 32k | Apache-2.0 | 时序知识图谱（可选） |
| [langfuse/langfuse](https://github.com/langfuse/langfuse) | 36k | — | LLM 追踪与评测 |
| [vibrantlabsai/ragas](https://github.com/vibrantlabsai/ragas) | 16k | Apache-2.0 | RAG 评测指标 |
| [tldraw/tldraw](https://github.com/tldraw/tldraw) | 51k | — | 白板画布 |
| [index-tts/index-tts](https://github.com/index-tts/index-tts) | 24k | — | 零样本 TTS |
| [FunAudioLLM/CosyVoice](https://github.com/FunAudioLLM/CosyVoice) | 24k | Apache-2.0 | 多语言 TTS |
| [breezedeus/Pix2Text](https://github.com/breezedeus/Pix2Text) | 3.3k | MIT | 公式/表格/版面 OCR |

> 许可提醒：MinerU 是自定义许可，商用前读一遍；tldraw、Langfuse 的部分能力有商业条款；IndexTTS 许可见仓库。别把「能跑」当成「能商用」。

---

## 9. 附：本周就能做的 3 个小改动

1. **Tutor 加一句「先别给答案」**（1 天）：在 _COMMON_RULES 前加一个模式判断；学生问「这题怎么做」时，先回「你先说说你卡在哪一步？」；前端加「提示」按钮，点一次给一层。
2. **错题本加「错因」下拉**（2 天）：6 个选项 + 一句证据；先手动，后接 LLM；数据结构先留字段，避免以后迁移。
3. **复习任务显示「为什么是今天」**（2 天）：先用简化规则（对/错/用时 → 1/3/7 天），把 UI 和解释跑通，再换 FSRS。

这三件事都不大，但能立刻让系统从「能答」变成「在教」。
