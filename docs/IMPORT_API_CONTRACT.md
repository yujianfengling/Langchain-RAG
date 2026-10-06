# 资料导入接口契约（2026-10-07，本轮冻结）

前后端并行开发依据。实现方不得单方面改名；如需变更，先改本文件并通知对方。

## 1. 能力清单

`GET /api/knowledge/capabilities`

```json
{ "success": true,
  "data": {
    "formats": [
      { "ext": "pptx", "kind": "presentation", "label": "PowerPoint 演示文稿",
        "parser": "python-pptx", "requires": null, "available": true, "note": null }
    ],
    "archives": [ { "ext": "zip", "label": "ZIP 压缩包", "parser": "zipfile",
                    "requires": null, "available": true, "note": null } ],
    "converter": { "available": true, "path": "C:\\Program Files\\Microsoft Office\\root\\Office16",
                   "kind": "msoffice",
                   "note": "已检测到 Microsoft Office；通过 COM 转换，可用应用：PowerPoint/Word" },
    "limits": { "max_file_mb": 200, "max_archive_mb": 500,
                "max_extract_file_mb": 200, "max_extract_total_mb": 1024,
                "max_total_mb": 1024, "max_files": 100,
                "max_depth": 2, "max_seconds": 300 },
    "accept": ".pptx,.ppt,.pdf,.docx,.doc,.txt,.md,.xlsx,.xls,.csv,.png,.jpg,.jpeg,.webp,.zip,..."
  } }
```

- `available=false` 的格式仍会出现在清单里，但前端要标注"需要转换组件/暂不支持"，后端必须返回明确原因，不得假装成功。
- `accept` 只包含 `available=true` 的扩展名，供 <input accept> 与拖拽校验共用。

## 2. 发起导入

`POST /api/knowledge/{subject_id}/import`  `multipart/form-data`

- 字段 `files`：可重复，支持多文件与压缩包混投。
- 权限：沿用学科写权限（课程学科=授课教师/管理员；个人学科=创建者/管理员；历史学科=管理白名单）。
- 返回：

```json
{ "success": true,
  "data": { "job_id": "imp_xxx", "accepted": 3,
            "rejected": [ { "filename": "a.xyz", "reason": "不支持的文件类型",
                            "error_type": "unsupported_format" } ] } }

- `rejected` 项在 `filename`/`reason` 之外附带 `error_type`（便于前端区分
  unsupported_format / converter_unavailable / size_exceeded / count_exceeded）；
  这是**附加字段**，不改变既有字段含义。
- `limits` 的默认值随 `IMPORT_MAX_*` 环境变量变化，前端应读取响应而不是写死：
  `max_file_mb` = 普通文档单文件（默认 200MB，env `IMPORT_MAX_DOCUMENT_MB`）；
  `max_archive_mb` = 压缩包单文件（默认 500MB，env `IMPORT_MAX_ARCHIVE_MB`）；
  `max_extract_file_mb` = 展开后单个成员（默认 200MB，env `IMPORT_MAX_EXTRACT_FILE_MB`）；
  `max_extract_total_mb` / `max_total_mb` = 单批展开总量（默认 1024MB，env `IMPORT_MAX_EXTRACT_TOTAL_MB`）。
  `POST /knowledge/upload` 与 `/import` 都按"是否压缩包"取不同上限，二者保持一致。
- `converter.kind` 如实取值 `"libreoffice"` / `"msoffice"` / `null`（不可用时为 null）；
  `available=false` 时 `.ppt/.doc` 的 `requires` 仍为 `"libreoffice"`，前端据此归类
  `converter_unavailable`（字段值稳定，不随后端变化）。
```

- 立刻返回，解析与建库在后台线程完成（不得在请求里同步阻塞）。

## 3. 查询任务（刷新后可恢复）

`GET /api/knowledge/imports/{job_id}` 与 `GET /api/knowledge/{subject_id}/imports?limit=5`

```json
{ "success": true,
  "data": {
    "job_id": "imp_xxx", "subject_id": "s1",
    "status": "queued|running|succeeded|partial|failed|cancelled",
    "created_at": "...", "updated_at": "...", "finished_at": null,
    "totals": { "total": 15, "files": 12, "ready": 10, "processing": 0,
                "failed": 2, "unprocessed": 0, "skipped": 3, "bytes": 123456 },
    "items": [
      { "item_id": "itm_1", "display_name": "第1章/课件.pptx",
        "source_path": "第1章/课件.pptx", "origin_archive": "资料包.zip",
        "size": 12345, "status": "ready", "stage": "indexing", "progress": 1.0,
        "message": "可学习", "doc_id": "mat_xxx", "error_type": null, "retryable": false }
    ],
    "archive_summaries": [
      { "archive": "资料包.zip", "extracted": 8, "skipped": 2,
        "note": "已忽略 __MACOSX 等无关内容" }
    ],
    "kb": { "status": "ready", "ready": true, "document_count": 12, "chunk_count": 340 },
    "summary_text": "10 份可学习，2 份未能导入",
    "can_retry": true,
    "can_continue": false,
    "stage_timings": { "extract_ms": 1200, "parse_ms": 41000, "build_ms": 3000,
                       "vision_ms": 38000, "total_ms": 45200 },
    "warnings": ["旧版 .ppt 需要 Office 转换组件（LibreOffice 或 Microsoft Office），已跳过 1 个文件"]
  } }
```

- 条目 `status`：`pending|parsing|indexing|ready|failed|skipped`。
- `progress` 只在该阶段有真实百分比时给出，否则为 null；不得编造。
- 全部文件解析失败时 `kb.ready=false`，且 `status != "succeeded"`。

### 3.1 totals 语义（2026-10-07 新增字段，只增不改）

| 字段 | 含义 |
|---|---|
| `total` | 本次任务发现的成员总数（含已跳过） |
| `ready` | 已建立索引、确实可学习的资料数 |
| `processing` | 仍在解析/建库中的资料数 |
| `failed` | 解析或建库失败的资料数 |
| `unprocessed` | **发现但没有为它建条目的成员数**（整批预算耗尽/继续处理前的剩余） |
| `skipped` | 明确跳过的成员数（无关文件、不可用格式等） |
| `files` | 旧字段，语义保持为 `ready + failed + skipped` |

**不变量**：`total == ready + processing + failed + unprocessed + skipped`。

状态语义：
- 只要 `unprocessed > 0` 或 `failed > 0`，`status` 必须是 `partial`，**绝不 `succeeded`**；
  `summary_text` 形如"N 份可学习，M 份未处理（可继续）"。
- 未进入索引的条目不得写成"可学习"：只有在统一建库成功后
  `indexing → ready`；建库失败回退为 `failed`（error_type=`build_failed`，可重试）。
- 建库失败绝不清空已有知识库（沿用"备份→构建→失败回滚"）。

### 3.2 计时（2026-10-07 新增，便于页面显示"最慢文件/最慢阶段"）

- 每个 item 附带 `timings`：`parse_ms / vision_ms / vision_calls / vision_cached /
  vision_failed / vision_timeout / images_total / images_deduped / item_timed_out`，
  以及复用时的 `reused`、单条建库的 `chunks`。
- job 附带 `stage_timings`：`extract_ms / parse_ms / build_ms / vision_ms / total_ms`
  （单条建库时另有 `build_chunks`）。所有计时均为非负整数毫秒。

说明（实现侧，2026-10-07）：
- `GET /api/knowledge/{subject_id}/imports?limit=5` 返回的 `data` 形如
  `{ "jobs": [<job>, ...], "latest": <job>|null }`；单个 job 结构与
  `GET /api/knowledge/imports/{job_id}` 的 `data` 完全一致。
- 压缩包展开出的被忽略项（__MACOSX、隐藏文件、临时文件等）会作为
  `status="skipped"` 的条目如实列出，`message` 写清跳过原因。
- 压缩包自身损坏 / 加密 / 超限时，会在 items 里产生
  `status="failed"`、`error_type=archive_corrupt | archive_encrypted |
  archive_limit_exceeded` 的条目（`retryable=false`），并在
  `archive_summaries` 里说明原因；绝不静默或显示成"没有可导入的资料"。
- 每条被接受的文件都必须有至少一条终态（ready/failed/skipped）条目。
- `archive_summaries[].note` 说明本包展开了什么、跳过了什么。

## 4. 重试、继续与取消

- `can_continue`：当任务终态且 `unprocessed > 0`（或存在可重试失败）且保留了原包时为
  `true`。页面据此展示"可继续"。

- `POST /api/knowledge/imports/{job_id}/continue` → `{ "success": true, "data": { "job_id": "imp_new" } }`
  （2026-10-07 新增）从**同一个已保留的原包/原始文件**继续，只处理"未处理/失败且可重试"
  的成员，**不要求用户重传**，也不重复处理已 ready 的资料。
  - 幂等：已 ready 成员按 `content_sha（内容哈希）+ 解析配置 + embedding 版本` 判定可复用；
    内容变化则正确失效并重新解析。重试/继续都不会产生重复资料、重复分块、重复地图节点。
  - 跨服务重启：任务与成员计划落库（`import_jobs.plan_json`），原包保留在
    `import_tmp/<job>/raw`；重新解包后按 `(origin_archive, source_path)` 与既有条目对齐。
  - 无可继续内容时返回 400（`没有可继续处理的条目`），不假成功。

- `POST /api/knowledge/imports/{job_id}/retry` → `{ "success": true, "data": { "job_id": "imp_new" } }`
  （只重试失败且 retryable 的条目，不产生重复资料）。
  实现口径：重试会重新处理该任务的原始上传内容，已成功落盘的"同名同内容"按内容哈希
  判为"已存在、不重复导入"并记为 ready；同名不同内容一律另存（不静默覆盖）。
  `build_failed`、`converter_unavailable`、`item_timeout`、`parse_error`、
  `io_error` 的条目 `retryable=true`。

- 新增超时/错误分类（准确原因，不假成功）：
  `item_timeout`（单文件墙钟超时）、`archive_member_unreadable`（单成员读取失败，
  只跳过该成员、其余继续）、`archive_staging_unwritable`、`archive_disk_full`。
- `DELETE /api/knowledge/imports/{job_id}` → 取消未开始/进行中的任务并清理临时目录（已导入的资料不删除）

## 5. 前端行为

- 拖拽区始终可见（有资料时也能继续拖），无写权限时不渲染可操作的上传框。
- 拖拽时高亮；`dragover/drop` 必须 `preventDefault`，禁止浏览器直接打开文件。
- 队列展示：文件名、大小、状态、失败原因、重试按钮；上传/解析/建库三段状态分开。
- 完成后展示真实摘要 + 「查看详情」+「开始伴学（跳 /student?subject=<sid>）」。
- 轮询 1.5s，任务终态后停止；刷新页面用 `/{subject_id}/imports` 恢复最近任务。
