# 真实压缩包导入超时——根因与修复（2026-10-07）

## 一、这次真实任务是什么

| 项 | 值 |
|---|---|
| 任务 | `imp_d3557678d5b5` |
| 学科 | `sub-yno2z3fi`（个人学科「计算机图形学」，用户 `2410630618`） |
| 原包 | `2026秋计算机图形学.zip`，**367 MB**，仍在 `demo_data/import_tmp/imp_d3557678d5b5/raw/000_2026秋计算机图形学.zip` |
| 起止 | 19:50:30 → 19:56:00（**330 秒**） |
| 原包成员 | **16 个 .pptx**（无目录、无临时文件、无不支持格式） |
| 实际处理 | **8 个**（第 10 章、第 1–4 章及第 4 章 part1~part4） |
| 未处理 | **8 个**（第 5 章 part1/part2(两个)、第 6 章 part1/part2、第 7、8、9 章） |
| 页面显示 | 「导入队列 已完成 / 8 份资料已可学习」＋一行警告——**没有告诉用户还有 8 份没进来** |

用户说"大约 10 个文件"，实际是 **16 份有效课件**（第 5 章有两个仅差空格/连字符的同名变体）。

### 8 份"可学习"是真的吗？——是

已核验：向量库 `demo_data/vector_store/sub-yno2z3fi/` 存在（`index.faiss` + `meta.json`，
`documents=261`，构建时间正好是任务结束时刻），BM25 索引也存在。
真实检索抽查：

| 提问 | 命中文件（带真实幻灯片号） |
|---|---|
| 直线的扫描转换算法 | 第4章part1（page 30 / 10 / 11） |
| 消隐计算 | 第10章（第8章未入库，故只命中有文字的其它章） |
| 颜色模型 | 第10章（page 17 / 2 / 20） |

即 8 份确实进了索引、可检索、引用带页码。

## 二、为什么超时（实测归因，不是猜测）

用真实 `services.import_service._parse_file` 解析该学科的两份课件副本（不改动演示库）：

| 文件 | 耗时 | 图片情况 |
|---|---|---|
| 计算机图形学-第10章-真实感图形绘制.pptx | **11.8 s** | 3 张图，全部命中视觉缓存 |
| 计算机图形学-第1章 绪论.pptx | **61.8 s** | 11 张图，10 张缓存命中 + **1 次真实视觉调用** |

对照：同一份 deck 只用 `PptxLoader(enrich_images=False)` 解析是 **0.6 s**。

**结论：耗时几乎全部来自逐张图片的串行视觉调用**，而不是解析、分块或向量化。

三条实测证据：

1. **串行**：`rag/loaders/pptx_loader.py` 里 `for doc in image_docs: enricher._enrich_single_document(doc)`
   是一张一张来的，没有并发上限概念。
2. **单次调用很慢**：第 1 章那份 61.8s 里只有 1 张未命中缓存 → 单次真实视觉调用约 **40 s+**。
3. **必然失败还送**：`services/multimodal_service.py:294-306` 对超过 4096px / 4194304 像素的图**直接拒绝**，
   先不缩放。真实日志：
   - `slide001_img2.png 5439x4501 超过限制（最大边长 4096px）`
   - `slide003_img1.jpg 8294400 像素超过限制（最大 4194304 像素）`
   既浪费往返时间，又静默丢掉大图里的内容。

补充事实：OCR 通道不可用（`tesseract` 未安装），所以图像内容只能靠视觉模型。

### 超时判定本身的缺陷
`services/import_service.py:541` 只在**每个文件开始前**检查整批预算：

```python
if time.monotonic() - started > limits["max_seconds"]:
    warnings.append("解析耗时超过上限 300s，剩余资料未处理"); break
```

- 单个文件可以无限期拖住整批（没有单文件上限、没有单次调用上限、没有"无进展"判断）；
- 触发后只写一条 warning，`_derive_status` 只看 item 状态 → 8 个 ready → **succeeded**；
- 未处理的 8 个成员**根本没有 item**，所以 totals 里完全不可见，界面也无从显示；
- 没有"继续"入口，用户只能重传整个 367MB 的包。

## 三、第二个真实阻塞：不是压缩包损坏，是我们把**目录当文件读**

`莫大物化.7z`（287 MB）在学科 `sub-kg5yy0t4`「莫大物化」两次导入均失败
（`imp_5ae1033b535e`、`imp_363144414fed`）：

```
archive_corrupt: 读取压缩包条目失败: 新建文件夹: [Errno 13] Permission denied:
'C:\Users\21508\AppData\Local\Temp\rag_7z_yewu0ht9\新建文件夹'
```

### 原包是好的（先证伪"损坏"）
- 用户给的原始文件 `D:\APP_DATA\xwechat_files\...\莫大物化.7z` 与后端已保留的
  `demo_data/import_tmp/imp_5ae1033b535e/raw/000_莫大物化.7z` **sha256 完全一致**
  （`a208184d74f4bbbd…`）。
- `py7zr.SevenZipFile(...).extractall()` 到系统 TEMP **成功**（EXTRACT_OK）。
- 成员清单：**84 个 = 72 个文件 + 12 个目录**；含 `物化/1/*.jpg`（多级中文目录）、
  一个嵌套压缩包 `莫大物化课件.zip`（75 MB）、以及全角目录名 `６`。

### 真正的根因（100% 复现）
**py7zr 的 `getnames()` 返回目录名时不带结尾斜杠**，而 `services/archive_service.py` 的
`_iter_7z` 用 `name.endswith("/")` 判目录：

```
dirs (12):
   '新建文件夹'   endswith_slash=False
   '物化/4'       endswith_slash=False
   ...
会被当作文件加进 extract targets 的目录: ['新建文件夹','物化/4',...,'６']
```

于是 12 个目录全部被当成文件：先进入 `extract targets`，解压后又被
`path.stat()` / `_p.read_bytes()` 读取 —— 在 Windows 上对目录 `open()` 正好抛：

```
READ_DIR -> PermissionError [Errno 13] Permission denied: '...\新建文件夹'
```

与用户看到的文案逐字一致。随后这个 **PermissionError 被错误归类为 `archive_corrupt`**，
于是界面显示"压缩包损坏"，而原包其实完全正常。

> 说明：把暂存目录从系统 TEMP 挪到受控目录是好事（已做），但**不能修掉这个缺陷** ——
> 目录仍会被读；必须改用成员元数据判目录、并且绝不读取目录。

## 四、修复方向（对应本轮实现）

1. **状态与计数**：新增 `total / ready / processing / failed / unprocessed / skipped`，
   有未处理或失败即 `partial`；未进入索引不得显示"可学习"。
2. **可继续**：`POST /knowledge/imports/{job_id}/continue`，从保留的原包继续处理剩余成员，
   已 ready 的按内容哈希 + 解析配置 + Embedding 版本复用；跨服务重启可恢复。
3. **超时**：区分单文件上限、单次调用上限、无进展、整批预算；单文件超时只影响该文件；
   整批到时标记 unprocessed 并给"继续"，不再 succeeded。
4. **提速（不丢内容）**：超大图先等比缩放到提供方限制内再送；同内容图片去重；
   受控并发（默认 3）；单张超时（默认 30s）；原生文本/表格/备注优先，不整页识图。
5. **受控解包目录**：不再写系统 TEMP，改为任务自己的受控目录，失败也不留下垃圾。
6. **计时可解释**：每个条目落库 `timings`（parse/vision/convert/chunks…），任务落库阶段耗时。

（修改后的实测数字见本文档末尾"修复后实测"一节。）

---

# 修复后实测（2026-10-07）

## A. 计算图形学压缩包（367MB / 16 份 PPTX）：从 8/16 到 16/16

| 项 | 修复前 | 修复后 |
|---|---|---|
| 任务状态 | `succeeded`（谎报） | 旧任务视图 `partial`；继续后 `succeeded` |
| 计数 | files=8，未处理不可见 | total=16 / ready=8 / unprocessed=8，相加=16 |
| 界面 | "已完成 / 8 份资料已可学习" | "部分完成 · 已处理 8/16 · 未处理 8" + **继续处理剩余资料** |
| 继续能力 | 无（只能重传 367MB） | `POST /imports/{id}/continue`，复用已 ready 的资料 |
| 最终结果 | 8 份可检索 | **16 份全部可检索，753 个分块**（原 261） |

继续任务的阶段耗时（后端 `stage_timings`，真实值）：

| 阶段 | 耗时 |
|---|---|
| extract_ms（解包 367MB） | 4.6 s |
| parse_ms（含视觉） | 214 s |
| build_ms（分块+向量化+索引） | 24.9 s |
| vision_ms（累计，受控并发 3） | 278 s |
| total_ms（墙钟） | **244 s** |

即：8 份剩余课件用 **244s** 完成，而修复前 8 份要 330s 还只处理一半（因为那时视觉是逐张串行）。
原图不缩放会直接失败的两张，现在先等比缩放再送审，内容不再丢。

逐章检索复核（全部命中对应章节，且带幻灯片号）：

| 提问 | 命中 |
|---|---|
| 二维变换矩阵 | 第5章part1（page 30 / 28） |
| 二维观察与裁剪 | 第5章part2（page 6） |
| 三维几何变换 | 第6章part1（page 3） |
| 三维投影变换 | 第6章part2（page 3） |
| 三维对象的表示 | 第7章（page 3） |
| 消隐算法 | **第8章（page 4）** —— 修复前只能命中第10章 |
| 颜色模型 | **第9章（page 6）** —— 修复前只能命中第10章 |

## B. 莫大物化.7z（287MB / 84 条目 = 72 文件 + 12 目录）

| 项 | 修复前 | 修复后 |
|---|---|---|
| 结果 | `failed`，archive_corrupt，0 份资料 | `partial`，**0 failed**，按预算推进并可继续 |
| 目录 | 12 个目录被当文件读 → PermissionError | 目录不算资料、不读取、不报错 |
| 成员发现 | 0（解包就崩） | **92**（72 顶层 + 20 嵌套 zip 成员） |
| 界面 | "处理失败（archive_corrupt）" | "部分完成 · 已处理 16/92 · 未处理 76" + 继续入口 |

第一轮真实数据：extract 19.4s、parse 294s、build 42s、total 355.6s，产出 657 个分块，
**failed=0 / skipped=0**。该包图片极多（几十张 5MB 扫描页），按预算是分批推进的正常形态 ——
每批都保留已完成结果，继续不会重复识别（图片缓存 + 内容哈希 + 文档解析缓存）。

## C. 浏览器实操（真实点击）

`tests/_browser/verify_import_progress_ui.js`：登录真实账号 → 进「资料库」→ 莫大物化 → 管理资料，
页面显示：

```
导入队列  [部分完成]   已处理 16 / 共 92 份
92 总数 | 16 可学习 | 76 未处理
已处理 16/92 份，未处理 76 份
还有 76 份资料未处理
已完成的资料会复用，不会重复导入。
[ 继续处理剩余资料 ]
16 份可学习，76 份未处理（可继续）
整批预算 300s 已到，剩余成员未处理（可继续处理）
```

点击主按钮后**真实发出** `POST /knowledge/imports/{id}/continue`，队列立刻继续推进
（新资料陆续出现），并保持已完成的 16 份不重复。截图：
`tests/_screenshots/imp_partial_1366.png`、`imp_after_continue_1366.png`。

## D. 预算调整（有依据，不是"把 300 改大"）

单批解析预算默认 **300s → 900s**（`IMPORT_MAX_SECONDS`）。依据：真实 16 份 20–30MB 图片型课件
需要约 244s 处理 8 份；300s 会让这类**正常**资料永远停在一半。
同时仍然生效的独立上限：

- 单文件 `IMPORT_ITEM_MAX_SECONDS=180`（超时只影响该文件，其余继续）
- 单张视觉 `VISION_IMAGE_TIMEOUT_SECONDS=30`（不阻塞整份资料）
- 图片并发 `VISION_MAX_CONCURRENCY=3`

## E. 顺带修掉的一个真实失败：rag 导入死锁

第一轮"继续"曾以
`任务执行异常: deadlock detected by _ModuleLock('rag.rag_service')` 失败（0 条目）。
原因：后台线程导入 knowledge_service → `from rag.rag_service import ...`，
与启动预热线程并发进入 import 机器。修复：`services/rag_loader.load_rag_stack()`
把整条 rag 栈放进同一把可重入进程锁，预热与导入任务都先走它。
