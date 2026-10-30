# 中期演示版整改 · 交接记录（2026-10-30 晚，第 3 位接手 Agent）

> 面向"额度中断后接续"：只写**改了哪里、验证结果、下一步**。随进度更新。

## 一、恢复现场结论（本轮实查）

- 基线 `c381f07`（已推送）。前两位 Agent 留下 **16 个未提交修改文件 + 2 个新源码文件**
  （`services/chapter_outline.py`、`tests/test_page_index_reuse.py`），改动时间 10-30 15:42–18:12。
- 恢复点：stash `b6202a7`（tracked 全量快照）+ 新文件备份在
  `D:/神の文档/langchain-rag-work/recovery-20261030/`。
- 后端/前端当时均未启动，无被打断的导入任务。
- 中断点定位：**`services/knowledge_map.py` 的 `generate_map` 半接线**——
  `collect_evidence` 已改为返回 `(evidence, outlines)`，`_CHAPTER_SECTION` 提示词段、
  `_validate_map(expected_chapters=...)` 参数都已写好，但调用点没接：
  ① 474 行丢弃 outlines；② `{chapter_section}` 占位符从未替换（原样发给了模型）；
  ③ `_validate_map` 没传章清单；④ `_validate_map` 主体缺"漏章检测"实现。

## 二、本轮已完成（有验证证据）

1. **补完学习路线接线**（`services/knowledge_map.py`）：
   - `generate_map`：捕获 outlines → `merged_chapters` 合并 → 章节清单拼进提示词
     （label 用 `chapter_label` 原文标题）→ `_validate_map(expected_chapters=…)`；
   - `_validate_map`：漏章 → `uncovered_chapters` + `chapter_missing` 诊断（章号对上即算
     交代过，标题被改写不算缺章）；payload 顶层带 `uncovered_chapters`；
   - 降级路径（`_fallback_map`、no_evidence）补 `uncovered_chapters: []` 保持契约。
2. **新增测试 `tests/test_chapter_outline.py`（8 项全过）**：正文"第N章"不误判、
   页眉聚合去重、part 合并、漏章上报、改写标题不算缺、无清单不误报、提示词接线。
3. **真实数据实测（任务 3 数据侧）**：`sub-03mgutrp`（《数据库系统概论》252 页 3 份）
   页面文本 → 章节大纲 1–18 章全部解析出；part 边界章（4、10）跨文件合并正确；
   `chapter_excerpts` 每章取到真实开头正文。**不重开 PDF、不 OCR**。
4. **回归**：`ruff check .` 全绿；`test_page_index.py + test_page_index_reuse.py`
   12 项通过；`test_tutor_core / test_knowledge_tracing / test_lecture_notes` 后台跑完 exit 0。

## 三、历史证据（前两位 Agent，本轮复核为真）

- 页面索引重构：单文档入口 `build_document_page_index`（页级断点、复用导入 OCR 缓存
  `PageTextCache`、不伪造 bbox、超上限如实 truncated、m:/f: 别名、删除/替换/取消任务
  全链路失效）；导入成功后自动接续建页（`import_service._schedule_page_index_after_import`）。
- API 权限：`/pages` 列表、`/pages/search`、`/pages/image` 均补了"教师草稿资料学生不可见"
  边界；`tutor_tools._t_search_pages` 同口径 + 范围过滤。
- 前端：TutorWorkspace「工具」下拉已拆成白板/录音笔记独立按钮（带 Tooltip+focus）；
  MaterialsPage 建页后台化、状态轮询、按页流式预览（批 4 页）、删除关预览、
  继续处理未覆盖页入口；UploadDropzone 进度文案修复。
- 12 项 page_index 测试通过（本轮复跑确认）。

## 四、浏览器实测结果（真实浏览器 + 真实模型，2026-10-30 晚）

以学科属主临时会话（`user_11417ecbd531`，ttl 6h，结束需 revoke）登录，学科 `sub-03mgutrp`（数据库，3 份教材 PDF）：

- **任务 3 端到端 ✅**：侧栏「重新生成」16.9s 返回 200，新路线 **18 章全部生成**（旧路线只有 1/6/9/13/14 章）；API 复核 `uncovered_chapters=[]`、`diagnostics=[]`、`matched_previous=5`（旧学习记录保留）。
- **任务 1 闭环 ✅**：
  - 资料库每份资料显示「页面 84/84」；重按「建立页面索引」1 秒内完成（页级断点全跳过，doc_state 补齐）；
  - 「查看」按页预览流式加载（批 4 页），封面页真实渲染；
  - 伴学问 SELECT/范式两轮真实回答，引用带页码+章节；
  - **本轮新修**：`SourcePanel.tsx` 此前只有检索型来源才有「查看页面图」；已补与白板同口径的链路（页码+文件名反推 `/pages/image`，没建页时如实提示不伪造）。实测引用 [1] 打开 part2 第 19 页（教材 176–177 页 6.2 规范化），内容与片段一致；
  - 白板「课件原文」页签同样打开第 19 页真实页面图，标「已引用」。
- **任务 2 ✅**：「历史/白板/录音笔记/成果与详情」独立成排（白板标题带一句话简介）；1366/800/390px 三档截图均无换行，390px 收缩为图标（aria-label 与 Tooltip 保留）。
- 截图存档：`C:\Users\21508\.zcode\cli\artifacts\sess_02b3d6e2-8375-4728-b884-d4c9ec2dca14\`（call_*-tool-result-*.png）。

**新发现的断点（任务 5 范围）**：历史记录弹窗里的会话只能预览（tab 展开内容），**没有"继续此会话"载入主区的入口**；刷新后主区是新会话。
→ 后续核实结论：历史弹窗定位是「回看 + 白板承接」；**会话连续性另有一条已验证的路径**——重新进入学科后主区自动恢复上一会话（含每题作答回显"这是你上次的作答"），刷新继续不依赖历史弹窗。

## 四点五、任务 4/5 实测结果（真实浏览器 + 真实模型）

- **选点联动 ✅**：路线点「函数依赖与范式」→ 主区切到该知识点，出现「讲懂这个知识点 / 做一道练习」。
- **出题 ✅**：真实 LLM 出 3 题（单选/判断/简答），题目挂知识点标签与「1 份来源」；未提交的题不显示答案（不泄露）。
- **作答反馈 ✅**：第 1 题答对→「答对本次」+掌握度 67%；第 2 题故意答错→「答错本次」+正确解释+掌握度 50%；简答（归属不可靠）标「待确认」。
- **任务与复习定位 ✅**：「任务与复习」页出现「复习：函数依赖与范式 · 错题自动安排 · 截止 2026-10-31」。
- **刷新继续 ✅**：刷新后复习任务仍在、路由保持；重新进入学科后「建议下一步：函数依赖与范式」、节点状态「练习中」、上轮作答回显全部保留。
- **跨模式上下文 ✅**：要求"结合刚才练习情况出复习笔记"→ 笔记第一段即「先说你错的那道题」，引用真实作答（答"正确"是错的）+ 教材原文 + 页码引用。
- 未真实走录音笔记（IAB 无麦克风）；出题/作答链路已由伴学入口等效覆盖。

## 五、收尾状态（本轮结束）

- **本地提交 `e45bf2d`**（22 文件 +2368/−233），未推送。恢复点 stash `b6202a7` 保留。
- 全量门禁：**串行 1070 用例 / 0 失败 / 0 错误 / 2 预期跳过 / exit 0**
  （JUnit：`langchain-rag-work/verify-20261030/logs/full-gate-serial.xml`）。
  ⚠️ `-n 4` 两次报收集不一致 + sqlite 冲突，**根因是显式指定 EDU_DATA_DIR 的用法问题**
  （conftest 只对默认测试目录按 worker 细分，见 conftest.py:68）；用默认目录跑 `-n 4` 即可，
  不是代码回归（出错文件串行复跑 36/36 全过）。
- ruff 全绿；`tsc --noEmit` 与 `npm run build` 通过。
- 临时会话已 revoke，前后端服务已停。演示停放状态按 progress-report 第九节手动重启。
- 遗留（不阻塞演示）：录音笔记端到端需真机麦克风验证（IAB 无音频输入）；
  其他旧学科如需页码覆盖显示，重按一次「建立页面索引」补 doc_state（秒级，页级断点全跳过）。

## 六、待办（接续者从这里开始）

1. 推送由人决策（本地 `e45bf2d` 领先 origin/master 一个提交）。
2. 若需录屏：启动服务后按第四节路径重演（选点→出题→作答→复习任务→刷新）。
3. 旧索引重建前后对比（progress-report 第八节建议 3）仍是可选优化。

## 五、注意事项（给下一位）

- `demo_data/page_index.db` 里 `doc_state` 只有新代码写过；252 页旧页（sub-03mgutrp）
  无 doc_state 行 → 状态接口 pages=252 但 document_details 空，前端按"无详情"显示，
  重跑一次建页即可补齐（页级断点会全部跳过，秒级）。
- 测试/脚本**别用 demo_data 当 EDU_DATA_DIR**（本轮清理过误写入的 sX/s1 两行）。
- `pytest tests/test_chapter_outline.py` 等 conftest 会自动隔离数据目录。
