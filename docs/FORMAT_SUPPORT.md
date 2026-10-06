# 资料格式能力与导入配置（2026-10-07）

单一事实来源：`rag/formats.py`。前端 accept 列表、拖拽校验、后端校验、解析器分派、
帮助说明与本文件都由它派生；`GET /api/knowledge/capabilities` 是**运行时真值**。

## 1. 现在真实支持什么（实测，不是后缀白名单）

“支持”= 内容能被解析并进入检索。下面每一项都在隔离数据里跑过真实导入 + 提问命中。

| 类别 | 格式 | 可用 | 解析器 | 说明 |
|---|---|---|---|---|
| 课件 | `.pptx` | ✅ | python-pptx | 幻灯片顺序、标题、文本框正文、**表格**（转 Markdown）、**备注**、**组合形状递归**、图片走视觉；`metadata.page` = 真实幻灯片号 |
| 课件 | `.ppt` | ⚠️ 需转换组件 | LibreOffice → pptx | 未检测到 LibreOffice 时**明确失败**（`converter_unavailable`），绝不改后缀冒充 pptx |
| 文档 | `.pdf` | ✅ | pdfplumber（可选视觉增强） | 扫描件复用既有视觉/OCR 链路 |
| 文档 | `.docx` | ✅ | docx2txt | |
| 文档 | `.doc` | ⚠️ 需转换组件 | LibreOffice → docx | 同上 |
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
| 资料包 | `.rar` | ❌ 缺外部工具 | rarfile + unrar | 已装 rarfile，但本机没有 unrar/bsdtar/7z，能力清单如实标 `available=false` |
| 其他 | `.tar` / `.gz` 等 | ❌ 尚不支持 | — | 提交时明确拒绝并给出 reason |

当前机器的实测能力（`GET /api/knowledge/capabilities`）：
- 直接支持 19 种扩展名（含 `.pptx/.pdf/.docx/.txt/.md/.xlsx/.xls/.csv/.html/.htm/.rtf/.odt/.odp/.epub` + 4 种图片）
- 压缩包支持 `.zip`、`.7z`
- `converter.available = false`（未安装 LibreOffice）→ `.ppt/.doc` 会明确失败
- `rarfile` 已安装但 `available = false`（缺外部解压程序）

## 2. 需要我安装什么（只有这两项，且都不是必须）

### A. 旧版 `.ppt` / `.doc`（可选）
必须依赖 LibreOffice 做真实转换（纯 Python 没有可靠的 .ppt 解析器）。

- 安装：到 <https://www.libreoffice.org/download/> 下载 Windows 版并默认安装。
- 配置（二选一，重启后端生效）：
  1. 把 `soffice.exe` 所在目录加入 PATH；或
  2. 设置环境变量 `OFFICE_CONVERTER_PATH=C:\Program Files\LibreOffice\program\soffice.exe`
- 验证：`GET /api/knowledge/capabilities` 的 `converter.available` 变为 `true`，
  `.ppt/.doc` 的 `available` 也为 `true`；随后拖入一个 `.ppt` 应能解析出页数与文本。
- 说明：安装 LibreOffice **不会**让本服务下载任何大型模型；转换产物放在受控临时目录，
  原始文件保留，转换关系可追溯。

### B. `.rar`（可选）
- 需要外部解压程序：安装 WinRAR（含 `UnRAR.exe`）或 `7-Zip`（`7z.exe`）并加入 PATH。
- 验证：`GET /api/knowledge/capabilities` 中 `.rar` 的 `available` 变为 `true`，
  拖入一个 `.rar` 能展开出成员文件。
- 未安装时不会显示“导入成功”，而是明确提示缺少外部工具。

除此之外**不需要**安装任何系统程序或下载模型：`.pptx/.xlsx/.xls/.csv/.zip/.7z` 等
全部由项目 venv 内的 Python 库完成。

## 3. 批量导入限制（可配置，默认值）

| 配置项 | 默认 | 说明 |
|---|---|---|
| 单文件大小 | 20 MB | 沿用既有上传上限，未放宽 |
| 单次总量 | 1024 MB | 按**实际展开字节**统计，防压缩炸弹 |
| 文件数量 | 100 | 含压缩包展开出的成员 |
| 递归深度 | 2 | 嵌套压缩包层级，超出明确提示 |

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
