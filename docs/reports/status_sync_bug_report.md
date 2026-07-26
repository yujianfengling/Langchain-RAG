# Bug #2: 知识库列表页与详情页状态不一致

## Bug 概述

| 项目 | 内容 |
|------|------|
| 发现日期 | 2026-07-26 |
| 严重程度 | 中 |
| 影响范围 | 知识库管理页面 |
| 状态 | ✅ 已修复 |

## 现象描述

**列表页**：`test_physics` 显示绿色标签 "已构建"  
**详情页**：同一学科显示灰色标签 "未构建"

两个页面对同一知识库的构建状态判断不一致。

## 根因分析

### 问题1: 状态值不统一

| 页面 | API | 状态字段值 | 显示逻辑 |
|------|-----|-----------|---------|
| 列表页 | `GET /api/knowledge` | `"已构建"` | `status === '已构建'` ✅ |
| 详情页 | `GET /api/knowledge/{id}/stats` | 无 `status` 字段 | `stats?.has_knowledge_base` → `undefined` → "未构建" |

### 问题2: 详情页数据解析错误

```typescript
// 问题代码
setStats(statsRes.value?.data);  // .data 不存在于 API 响应中

// API 实际返回格式
{
  "subject_id": "test_physics",
  "status": "ready",      // 新增
  "stats": {
    "has_knowledge_base": true,
    "file_count": 1,
    "total_size_bytes": 124,
    ...
  }
}
```

`Promise.allSettled` 包装了结果，所以 `statsRes.value` 才是 API 响应本身，`statsRes.value?.data` 为 `undefined`。

### 问题3: 后端未返回统一状态字段

- `GET /api/knowledge` 返回 `status: "已构建"`（中文）
- `GET /api/knowledge/{id}/stats` 不返回 `status` 字段

## 修复方案

### 1. 统一后端状态规范

```python
# 状态枚举
"ready"    → 已构建
"empty"    → 未构建
"building" → 构建中
"failed"   → 构建失败
```

### 2. 修改文件列表

| 文件 | 修改内容 |
|------|---------|
| `services/knowledge_service.py` | `list_knowledge_bases()` 返回 `"ready"/"empty"` |
| `services/knowledge_service.py` | `get_knowledge_base_stats()` 新增 `status` 和 `has_knowledge_base` 字段 |
| `api/schemas/knowledge_schema.py` | `KnowledgeBaseItem.status` 默认值改为 `"empty"` |
| `api/schemas/knowledge_schema.py` | `KnowledgeStatsResponse` 新增 `status` 字段 |
| `api/routes/knowledge.py` | `get_stats()` 返回 `status` 字段 |
| `frontend/src/types/index.ts` | `KnowledgeStats` 新增 `status` 和 `has_knowledge_base` 字段 |
| `frontend/src/components/KnowledgeManager/index.tsx` | 列表页状态判断改为 `status === 'ready'` |
| `frontend/src/components/KnowledgeManager/index.tsx` | 详情页状态判断改为 `stats?.status === 'ready'` |
| `frontend/src/components/KnowledgeManager/index.tsx` | 修复 `setStats(statsRes.value)` 去掉 `.data` |

### 3. 关键修改详情

#### 后端 `knowledge_service.py`

```python
# list_knowledge_bases - 统一状态值
status = "ready" if has_files else "empty"

# get_knowledge_base_stats - 新增状态字段
return {
    "subject_id": subject_id,
    "status": "ready" if has_knowledge_base else "empty",
    "has_knowledge_base": has_knowledge_base,
    ...
}
```

#### 前端 `index.tsx`

```typescript
// 列表页
<Tag color={status === 'ready' ? 'success' : 'default'}>
  {status === 'ready' ? '已构建' : '未构建'}
</Tag>

// 详情页
<Tag color={stats?.status === 'ready' ? 'success' : 'default'}>
  {stats?.status === 'ready' ? '已构建' : '未构建'}
</Tag>

// 数据获取 - 去掉错误的 .data
setStats(statsRes.value);
```

## 测试结果

### 测试流程

```
创建学科 → 上传文件 → 构建知识库 → 返回列表页 → 确认状态 → 进入详情页 → 确认状态
```

### 测试结果

| 步骤 | 预期 | 实际 | 结果 |
|------|------|------|------|
| 列表页显示 | 已构建 | 已构建 ✅ | 通过 |
| 详情页 Tag | 已构建 | 已构建 ✅ | 通过 |
| 详情页统计 | 已就绪 | 已就绪 ✅ | 通过 |
| API 统一返回 | `status: "ready"` | `status: "ready"` ✅ | 通过 |

## API 响应格式（修复后）

### GET /api/knowledge

```json
{
  "subjects": [
    {"subject_id": "test_physics", "status": "ready"},
    {"subject_id": "math_001", "status": "empty"}
  ]
}
```

### GET /api/knowledge/{subject_id}/stats

```json
{
  "subject_id": "test_physics",
  "status": "ready",
  "stats": {
    "has_knowledge_base": true,
    "path": "data/vector_store/test_physics",
    "total_size_bytes": 12345,
    "total_size_mb": 0.01,
    "file_count": 5
  }
}
```

## 遗留问题

文档数量和分块数量显示为 0，因为后端返回 `file_count` 而非 `document_count` 和 `chunk_count`。这是独立问题，不影响状态同步。

---

**修复完成时间**: 2026-07-26  
**测试状态**: ✅ 通过
