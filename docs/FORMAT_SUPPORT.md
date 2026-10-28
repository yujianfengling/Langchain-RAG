# 资料格式能力与导入配置（2026-10-07）

单一事实来源：`rag/formats.py`。前端 accept 列表、拖拽校验、后端校验、解析器分派、
帮助说明与本文件都由它派生；`GET /api/knowledge/capabilities` 是**运行时真值**。

## 1. 现在真实支持什么（实测，不是后缀白名单）

“支持”= 内容能被解析并进入检索。下面每一项都在隔离数据里跑过真实导入 + 提问命中。

| 类别 | 格式 | 可用 | 解析器 | 说明 |
|---|---|---|---|---|
| 课件 | `.pptx` | ✅ | python-pptx | 幻灯片顺序、标题、文本框正文、**表格**（转 Markdown）、**备注**、**组合形状递归**、图片走视觉；`metadata.page` = 真实幻灯片号 |
| 课件 | `.ppt` | ✅（有转换组件时） | LibreOffice 或 Microsoft Office COM → pptx | 检测不到转换组件时**明确失败**（`converter_unavailable`），绝不改后缀冒充 pptx |
| 文档 | `.pdf` | ✅ | PyMuPDF + 本地 OCR 兜底（rapidocr/liteparse） | 扫描件自动分流到本地逐页 OCR，不逐图调用付费视觉 |
| 文档 | `.docx` | ✅ | docx2txt | |
| 文档 | `.doc` | ✅（有转换组件时） | LibreOffice 或 Microsoft Office COM → docx | 同上 |
| 文档 | `.txt` / `.md` | ✅ | stdlib | |
| 文档 | `.html` / `.htm` | ✅ | beautifulsoup4 | |
| 文档 | `.rtf` | ✅ | striprtf | |
| 文档 | `.odt` | ✅ | odfpy | |
| 文档 | `.epub` | ✅ | EbookLib | |
| 表格 | `.xlsx` | ✅ | openpyxl | 按工作表/行输出，保留表名 |
| 表格 | `.xls` | ✅ | xlrd | 直读，不需要转换 |
| 表格 | `.csv` | ✅ | stdlib | |
| 演示 | `.odp` | ✅ | odfpy | |
| 图片 | `.png` / `.jpg` / `.jpeg` / `.webp` | ✅ | Pillow + 视觉模型 | 识别失败/未识别会明确标记，不当作完整解析成功 |
| 资料包 | `.zip` | ✅ | zipfile | 子目录、中文名（cp437/GBK 兼容）、忽略 `__MACOSX` 等 |
| 资料包 | `.7z` | ✅ | py7zr | |
| 资料包 | `.rar` | ✅（有后端时） | rarfile + unrar/unar/7z/bsdtar | 后端优先级：unrar/unar/7z/7zz（PATH）→ 系统 `tar.exe`（bsdtar）；加密 `archive_encrypted`、solid `archive_unsupported` 都明确失败 |
| 其他 | `.tar` / `.gz` 等 | ❌ 尚不支持 | — | 提交时明确拒绝并给出 reason |

当前机器的实测能力（`GET /api/knowledge/capabilities`，运行时真值）：
- 直接支持 19 种扩展名（含 `.pptx/.pdf/.docx/.txt/.md/.xlsx/.xls/.csv/.html/.htm/.rtf/.odt/.odp/.epub` + 4 种图片）
- 压缩包支持 `.zip`、`.7z`、`.rar`
- `converter.available = true`、`converter.kind = "msoffice"`（本机装了 Microsoft Office 16；
  无 LibreOffice）→ `.ppt/.doc` 走 COM **真实转换**（消费级 LibreOffice 缺失不再阻塞）
- `.rar` `available = true`，`note` 写明后端是 `bsdtar`（`C:\Windows\System32\tar.exe`）

## 2. 需要我安装什么（只有这两项，且都不是必须）

### A. 旧版 `.ppt` / `.doc`
检测顺序与优先级（`OFFICE_CONVERTER=auto` 时）：
1. `OFFICE_CONVERTER_PATH` 显式指定的 LibreOffice；
2. PATH / 常见路径下的 LibreOffice；
3. **Microsoft Office COM（pywin32，Windows）**——本机走的就是这条；
4. 都没有 → 能力清单 `available=false`，导入明确失败（`converter_unavailable`），绝不改后缀冒充。

- `OFFICE_CONVERTER=auto|libreoffice|msoffice|none`（默认 `auto`）：可强制某一种或彻底停用。
- COM 实现要点：`DispatchEx` 新建实例、`DisplayAlerts=0`；PowerPoint 用
  `Presentations.Open(ReadOnly=True, WithWindow=False)` + `SaveAs(path, 24)`（pptx）/
  `SaveAs(path, 1)`（旧 ppt）；Word 用 `Documents.Open(ReadOnly=True)` +
  `SaveAs2(path, FileFormat=12/0)`；在**独立线程**里 `CoInitialize/CoUninitialize`；
  超时抛 `converter_timeout`、其它异常抛 `converter_error`；`finally` 里 `Quit`。
- 验证：`GET /api/knowledge/capabilities` 的 `converter` 结构为
  `{available, path, kind: "libreoffice"|"msoffice"|null, note}`。

### B. `.rar`
检测顺序：`UNRAR_TOOL` env → PATH 上的 `unrar` / `unar` / `7z` / `7zz` →
系统自带 `%SystemRoot%\System32\tar.exe`（bsdtar，Windows 10+）。

- 命中后把路径写入 `rarfile` 对应的 `UNRAR_TOOL/UNAR_TOOL/SEVENZIP_TOOL/
  SEVENZIP2_TOOL/BSDTAR_TOOL`。
- 加密 RAR：明确 `archive_encrypted`（`rar5-hpsw.rar` 实测）；solid RAR：明确
  `archive_unsupported`（bsdtar 报 "RAR solid archive support unavailable"）；
  分卷不完整/截断：`archive_corrupt`。都带可读原因，**不假成功**。
- 本机无 unrar/7z，实际使用 `C:\Windows\System32\tar.exe`。

除此之外**不需要**安装任何系统程序或下载模型：`.pptx/.xlsx/.xls/.csv/.zip/.7z` 等
全部由项目 venv 内的 Python 库完成。

## 3. 批量导入限制（可配置，默认值）

单一来源：`rag/formats.limits()`（`GET /api/knowledge/capabilities` 的 `limits`）。

| 配置项（env） | 默认 | 含义 |
|---|---|---|
| `IMPORT_MAX_DOCUMENT_MB` | 512 MB | 普通文档单文件上限（旧字段 `max_file_mb`）|
| `IMPORT_MAX_ARCHIVE_MB` | 1024 MB | 压缩包单文件上限（`max_archive_mb`）|
| `IMPORT_MAX_EXTRACT_FILE_MB` | 512 MB | 压缩包展开后**单个成员**上限（`max_extract_file_mb`）|
| `IMPORT_MAX_EXTRACT_TOTAL_MB` | 1024 MB | 单批**展开总量**上限（`max_extract_total_mb`，旧字段 `max_total_mb` 同义）|
| `IMPORT_MAX_FILES` | 100 | 文件数量（含展开成员）|
| `IMPORT_MAX_DEPTH` | 2 | 递归深度 |
| `IMPORT_MAX_SECONDS` | 7200 | **整个任务**的墙钟上限（秒）；任务内部按 `IMPORT_BATCH_SECONDS` 自动分批 |
| `IMPORT_ITEM_MAX_SECONDS` | 180 | 单文件解析墙钟上限（秒）；超时记 `item_timeout` 并继续后面的文件 |
| `IMPORT_PDF_ITEM_MAX_SECONDS` | 1200 | 大 PDF（>300 页）单文件上限（秒）|
| `IMPORT_OCR_ENGINE` | auto | `auto | rapidocr | liteparse | pytesseract | off` |
| `IMPORT_OCR_DPI` | 200 | 扫描页渲染 DPI（越高越慢）|
| `IMPORT_OCR_MAX_SIDE` | 1600 | 渲染后最长边上限；实测 1600 比 2200 快约一倍，识别字数几乎不变 |
| `IMPORT_OCR_WORKERS` | 2 | 页级 OCR 并发（上限 3；1=串行）|
| `IMPORT_OCR_ITEM_MAX_SECONDS` | 2700 | 扫描件 OCR 单文件上限（秒）；页级缓存可断点续跑 |
| `VISION_MAX_CONCURRENCY` | 6 | 同一批图片视觉调用的受控并发数 |
| `VISION_MAX_IMAGES_PER_DOC` | 20 | 正文 PDF 每份最多识图数（防超时）|
| `VISION_DOC_BUDGET_SECONDS` | 180 | 正文 PDF 识图总预算（秒）|
| `VISION_IMAGE_TIMEOUT_SECONDS` | 30 | 单张图片视觉调用墙钟上限（秒）；超时继续其余图片 |

### 3.1 超大图与图片提速（2026-10-07）

- 超出提供方上限（最大边长 4096px / 最大 4194304 像素）的图片先用 Pillow 等比缩放出
  **送审副本**再识别（原图文件不动），并记录 `image_original_size` / `image_scaled_size` /
  `image_max_side`；只有确实无法解码时才 `skipped` 并写明确原因，绝不"必然失败还送"。
- 同一批内相同内容（MD5）的图片只识别一次（`images_deduped`）。
- 未命中缓存的图片按 `VISION_MAX_CONCURRENCY` 并发，单张超过
  `VISION_IMAGE_TIMEOUT_SECONDS` 记 `vision_timeout` 并继续其余图片，不阻塞整份资料。

兼容：仍接受旧 env `IMPORT_MAX_FILE_MB` 作为 `IMPORT_MAX_DOCUMENT_MB` 的回退。
超限一律返回**准确原因**（哪个限制、实际多大、上限多少），绝不超限后仍继续建库或假成功。

### 3.0 扫描件与本地 OCR（2026-10-28）

- 判定：PDF 抽样页文本层几乎为空 → 视为扫描件，走 `rag/loaders/pdf_ocr.py` 的**逐页 OCR**，
  每页一个 Document，`page` 保持 0 基（展示时 +1），因此引用页码真实可核对。
- 引擎优先级：`rapidocr`（自带 ONNX 模型、离线、按页）→ `liteparse`（内置 Tesseract）→
  `pytesseract`（仅检测到系统 tesseract 时）→ 明确失败（`ocr_unavailable`）。
- 缓存：`<data>/cache/ocr_pages/<文件SHA>/p<页>_<引擎>_<DPI>_v1.txt`；中断重跑只补缺失页。
- 视觉模型只服务正文 PDF 的插图，且受 `VISION_MAX_IMAGES_PER_DOC` / `VISION_DOC_BUDGET_SECONDS` 限制；
  扫描件默认**不**调用付费视觉（`IMPORT_OCR_CAPTION_IMAGES=0`）。

### 3.1 请求体大小的两层边界（不是后端应用层）

应用层（FastAPI/Starlette）**没有**请求体大小限制，也不在 `api/middleware.py` 里做；
真正会挡住 500MB 压缩包的是反向代理/部署层：

- **开发（Vite dev）**：`frontend/vite.config.ts` 的 `/api` 代理（http-proxy）默认不限制请求体，
  无需额外配置；代理超时 `proxy.timeout` 默认也够。
- **生产部署**：
  - Nginx：`client_max_body_size 512m;`（至少 ≥ `IMPORT_MAX_ARCHIVE_MB` + 表单开销），
    并视需要调大 `client_body_timeout` / `proxy_read_timeout`（导入任务异步返回，通常够用）。
  - Apache：`LimitRequestBody 536870912`。
  - 直接暴露 uvicorn：默认无 body 上限，但要确认前置网关/负载均衡也有同样的放宽。

安全边界：拒绝绝对路径与 `..` 路径穿越、拒绝符号链接、只写入受控临时目录与当前学科资料目录、
不执行包内程序/脚本/Office 宏；失败或取消后清理临时内容，不误删已有资料；
加密/损坏/分卷压缩包明确失败并给原因，不记录密码。

## 4. 端到端验收证据（隔离数据）

样本（由 `tests/_browser/make_samples.py` 真实生成，含模型无法预知的事实编号）：
多页 pptx（标题/正文/表格/备注/组合形状）、xlsx、csv、txt、png、
pack.zip（两个子目录各有同名 `课件.pptx`、中文与带空格文件名、`__MACOSX`/`Thumbs.db`/`~$` 垃圾）、
pack.7z、corrupt.zip。

结果（`tests/_browser/verify_import.py`，32/32 通过）：

- 一次提交 8 个文件 → 任务 `partial`：11 份可学习、1 份失败（损坏 zip，原因明确）、4 份跳过
- 压缩包内两个同名 `课件.pptx` 分别导出为 `第1章__课件.pptx` / `第2章__课件.pptx`，`doc_id` 不同、来源 `pack.zip` 可追溯
- 5 个不可预知事实全部在问答中命中，且**引用来自正确文件**
- `slides.pptx` 的引用带真实幻灯片编号（`page=1`）
- 学习地图的节点来源包含导入的新课件
- 其他账号读取该个人学科返回 403
- `.exe` 被拒并给出 `unsupported_format` 原因

浏览器真实拖拽（`tests/_browser/verify_dragdrop.js`，6/6 通过）：
把 pptx 与 zip 通过 `DataTransfer` 拖入 `.udz-drop` → 触发真实 `POST /import`（200）→
队列显示“处理中/已完成”与真实摘要“3 份资料已可学习”→「查看详情」「开始伴学」→
刷新后仍能恢复任务与结果 → 390/800/1366 均可用。

## 6. 本轮实测证据（2026-10-07，隔离数据 + 真实文件）

### 6.1 本机工具实测结论（无需安装任何东西）

| 工具 | 状态 | 用途与实测 |
|---|---|---|
| Microsoft Office 16 | ✅ 已安装 | `C:\Program Files\Microsoft Office\root\Office16`；PowerPoint/Word COM 可真实转换。实测把 pptx 转成 **107 KB 的真实 .ppt**，再转回 pptx 成功 |
| 系统 bsdtar | ✅ 已安装 | `C:\Windows\System32\tar.exe`（libarchive 3.8.8）；实测解出真实 RAR 成员（`seektest.rar` → stest1.txt/stest2.txt 各 2048 B，内容校验通过） |
| LibreOffice | ❌ 未安装 | 不装也能用：转换走 Office COM。若希望改用无界面 CLI，可装 LibreOffice 并设 `OFFICE_CONVERTER_PATH` |
| unrar / 7-Zip | ❌ 未安装 | 不需要：RAR 走 bsdtar。装 7-Zip 只是多一个后端（`UNRAR_TOOL` 可指到 7z.exe） |

### 6.2 大文件（>20MB）真实导入

样本：`大课件.pptx` **25.8 MB**、`大资料包.zip` **47.8 MB**（内含 25.8 MB 的 pptx、22 MB 二进制、一个 txt）。

- 两个文件都被接受，未因旧的 20MB 上限被拒；任务 `succeeded`，`ready=3 / failed=0 / skipped=1`，知识库就绪。
- **导入期间其他接口仍可访问**：并发探测 `/health` 与 `/knowledge/capabilities` 5/5 成功、最慢 0.67s。
- **可检索**：`大课件.pptx` 的 `LARGE-9241` 与压缩包内 `ZIPFACT-9241` 都在问答中命中。
- **引用保留原文件名与幻灯片号**：sources 中出现 `(大课件.pptx, page=1)`。
- **刷新可恢复**：`GET /knowledge/imports/{id}` 与 `/{subject_id}/imports` 都能取回同一任务。
- **重复内容不重复导入**：再次提交同内容 → 条目提示"同名同内容，已跳过（不重复导入）"，资料数不变（3 → 3）。
- **无误报的重试**：全部成功时 `retry` 返回 400「没有可重试的失败条目」，不假成功。
- 其他账号读取该个人学科 → **403**。

### 6.3 真实旧 PPT 与真实 RAR

- **旧 PPT**：Office COM 真实转换 → 导入成功，`LEGACY-9241` 可检索，引用为 **原文件名 `旧课件.ppt` + 第 1 页**。
- **真实 RAR**（互联网真实测试集）：`seektest.rar` 解出 2 个成员并导入，来源压缩包可追溯。
- **明确失败（原因准确，非工具误报）**：
  - `rar5-hpsw.rar` → `archive_encrypted`「RAR 压缩包已加密，需要密码」；
  - `unicode.rar`（solid）→ `archive_unsupported`（bsdtar 报 "RAR solid archive support unavailable"，rarfile 单独解压 CRC 失败）；
  - `rar3-old.rar`（分卷缺失/截断）→ `archive_corrupt`（bsdtar 报 "Truncated RAR file data"，rarfile 找不到 `.r00` 分卷）。
  三者都用外部工具独立复核过，是我们的能力边界而不是解析 bug。
- **RAR 内中文目录/同名文件/混合格式**：与 ZIP/7z 走**同一套成员映射与同名去重**代码路径
  （单测用中文目录 + 同名成员覆盖）。本轮**没有**用真实 RAR 验证这一条 —— 机器上没有 RAR 压缩器
  （RAR 只能由 WinRAR 生成），现有 RAR 样本里也不含中文目录。若需要，装 WinRAR 后可用它打包再验。

### 6.4 浏览器实操

- 上传限制与拒绝原因：`tests/_browser/verify_limits_ui.js` 9/9。把隔离后端的限制调成 25/30MB 后，
  拖入 25.8 MB 的 pptx 与 47.8 MB 的 zip，都在**提交前**被拒且**没有发出请求**，
  原因精确到："单个文件上限 25MB，该文件 25.8 MB" / "单个压缩包上限 30MB，该文件 47.8 MB"，
  展开区展示 capabilities 返回的真实三档上限与"暂不可用格式与原因"。
- 不可用格式提示：把 `OFFICE_CONVERTER=none` 后，界面出现"暂不可用格式与原因"分区，
  `.ppt/.doc` 明确标注不可用并附原因；此时拖入 `.ppt` 同样被提交前拒绝、不发请求。
- 截图：`tests/_screenshots/lim_a_limits_1366.png`、`lim_c_archive_rejected_1366.png`、
  `lim_g_converter_required_1366.png`，另有 390 / 800 视图。
