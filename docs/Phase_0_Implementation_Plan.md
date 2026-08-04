# Phase 0 Implementation Plan

## 一、目标

将 `rag/document.py` 中的文件加载逻辑迁移到 `rag/loaders/` 目录，保持接口完全兼容。

**核心原则**：新增 -> 适配 -> 测试 -> 替换

---

## 二、文件清单

### 2.1 新增文件

```
rag/loaders/
├── __init__.py              # 导出所有 Loader
├── base.py                  # Loader 基类
├── text_loader.py           # TXT 加载器
├── markdown_loader.py       # Markdown 加载器
├── pdf_loader.py            # PDF 加载器
├── docx_loader.py           # DOCX 加载器
└── image_loader.py          # 图片加载器
```

### 2.2 修改文件

| 文件 | 修改内容 | 影响范围 |
|------|---------|---------|
| `rag/document.py` | 改为路由层，调用 loaders | 核心变更 |
| `rag/__init__.py` | 可能需要更新导出 | 低 |

### 2.3 不修改文件

- `rag/multimodal/` - 保持不变
- `services/multimodal_service.py` - 保持不变
- `rag/vector_store.py` - 保持不变
- `rag/embedder.py` - 保持不变
- `rag/processor.py` - 保持不变

---

## 三、迁移顺序

### Step 1: 创建 Loader 基类（无风险）

**文件**：`rag/loaders/base.py`

**内容**：
- `BaseLoader` 抽象基类
- `LoadResult` 数据类
- 通用工具函数

**验证**：单元测试通过

---

### Step 2: 迁移 TXT Loader（低风险）

**文件**：`rag/loaders/text_loader.py`

**逻辑来源**：`rag/document.py` 第 150-179 行 `_load_txt()`

**迁移内容**：
```python
# 原有逻辑
def _load_txt(file_path: Path) -> List[Document]:
    try:
        if LOADER_AVAILABLE.get('text', False):
            loader = TextLoader(str(file_path), encoding='utf-8')
            return loader.load()
    except Exception as e:
        logger.warning(f"LangChain TextLoader failed: {e}, using fallback")
    
    # 降级方案
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    return [Document(page_content=content, metadata={"source": str(file_path)})]
```

**验证**：
- [ ] 单元测试：`TextLoader.load()` 返回正确
- [ ] 集成测试：TXT 知识库 rebuild 成功

---

### Step 3: 迁移 Markdown Loader（低风险）

**文件**：`rag/loaders/markdown_loader.py`

**逻辑来源**：`rag/document.py` 第 181-215 行 `_load_markdown()`

**迁移内容**：
- UnstructuredMarkdownLoader 优先
- TextLoader 降级
- 直接读取兜底

**验证**：
- [ ] 单元测试通过
- [ ] Markdown 知识库 rebuild 成功

---

### Step 4: 迁移 PDF Loader（中风险）

**文件**：`rag/loaders/pdf_loader.py`

**逻辑来源**：`rag/document.py` 第 217-259 行 `_load_pdf()`

**迁移内容**：
```python
# 原有逻辑
def _load_pdf(file_path: Path) -> List[Document]:
    try:
        if LOADER_AVAILABLE.get('pdf', False):
            loader = PyPDFLoader(str(file_path))
            return loader.load()
    except Exception as e:
        logger.warning(f"LangChain PyPDFLoader failed: {e}, using fallback")
    
    # 降级方案：pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            full_text = ""
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n\n"
        return [Document(page_content=full_text, metadata={"source": str(file_path), "total_pages": len(pdf.pages)})]
    except Exception as e:
        raise ValueError(f"Failed to load PDF: {e}")
```

**验证**：
- [ ] 单元测试：LangChain 和 pdfplumber 两种路径
- [ ] 集成测试：PDF 知识库 rebuild 成功

---

### Step 5: 迁移 DOCX Loader（低风险）

**文件**：`rag/loaders/docx_loader.py`

**逻辑来源**：`rag/document.py` 第 261-296 行 `_load_docx()`

**迁移内容**：
- Docx2txtLoader 优先
- docx2txt 直接读取降级

**验证**：
- [ ] 单元测试通过
- [ ] DOCX 知识库 rebuild 成功

---

### Step 6: 迁移 Image Loader（中风险）

**文件**：`rag/loaders/image_loader.py`

**逻辑来源**：`rag/document.py` 第 298-326 行 `_load_image()`

**迁移内容**：
```python
# 原有逻辑
def _load_image(file_path: Path) -> List[Document]:
    try:
        from services.multimodal_service import get_multimodal_service
        multimodal_service = get_multimodal_service()
        converter = MultimodalDocumentConverter(multimodal_service=multimodal_service)
    except Exception as e:
        logger.warning(f"Failed to get multimodal service: {e}")
        converter = MultimodalDocumentConverter()
    
    return converter.convert_image(file_path)
```

**依赖**：
- `services/multimodal_service.py`（不修改）
- `rag/multimodal/document_converter.py`（不修改）

**验证**：
- [ ] 单元测试：mock multimodal_service
- [ ] 集成测试：图片知识库 rebuild 成功

---

### Step 7: 重构 document.py 为路由层（核心变更）

**文件**：`rag/document.py`

**修改策略**：保留原文件作为备份，创建新版本

**新版本内容**：
```python
"""
文档加载模块 - 路由层

负责：
1. 文件类型判断
2. 调用对应 Loader
3. 保持接口兼容
"""

from pathlib import Path
from typing import List, Union
import logging

from rag.loaders import (
    TextLoader,
    MarkdownLoader,
    PdfLoader,
    DocxLoader,
    ImageLoader,
    get_loader_for_file,
)

logger = logging.getLogger(__name__)

# 保持原有接口
def load_document(file_path: Union[str, Path]) -> List['Document']:
    """加载单个文件"""
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    loader = get_loader_for_file(file_path)
    if loader is None:
        raise ValueError(f"Unsupported file format: {file_path.suffix}")
    
    return loader.load(file_path)

def load_documents(directory: Union[str, Path]) -> List['Document']:
    """加载目录下所有支持的文件"""
    directory = Path(directory)
    if not directory.exists():
        raise FileNotFoundError(f"Directory not found: {directory}")
    
    all_documents = []
    for file_path in directory.rglob('*'):
        if file_path.is_file():
            try:
                docs = load_document(file_path)
                all_documents.extend(docs)
            except Exception as e:
                logger.warning(f"Failed to load {file_path}: {e}")
    
    return all_documents

# 保持原有辅助函数
def get_supported_formats() -> List[str]:
    """获取支持的文件格式列表"""
    return ['.txt', '.md', '.pdf', '.docx', '.jpg', '.jpeg', '.png', '.webp']

def check_loader_status() -> dict:
    """检查各加载器的可用状态"""
    # ... 保持原有逻辑
```

**验证**：
- [ ] 所有现有测试通过
- [ ] 无破坏性变更

---

### Step 8: 更新 rag/__init__.py（如有必要）

**检查**：是否需要更新导出

**验证**：
- [ ] `from rag import load_documents` 正常工作

---

### Step 9: 清理旧代码（可选）

**条件**：所有测试通过，确认迁移完成

**操作**：
- 将旧代码移到 `rag/document_legacy.py`（备份）
- 或添加注释标记为废弃

---

## 四、风险点

### 4.1 接口兼容性风险

**风险**：调用方依赖特定行为或返回格式

**缓解**：
- 保持函数签名完全一致
- 保持返回类型完全一致
- 保持异常类型完全一致

**验证**：
```python
# 确保这些调用方式仍然有效
from rag.document import load_document, load_documents
from rag import load_document, load_documents
```

### 4.2 降级逻辑风险

**风险**：迁移过程中丢失降级逻辑

**缓解**：
- 逐行迁移，不重构逻辑
- 保持 try/except 结构
- 保持日志输出

### 4.3 依赖导入风险

**风险**：Loader 之间的依赖关系

**缓解**：
- Loader 之间不相互依赖
- 共享依赖通过参数注入
- 保持懒加载模式

### 4.4 图片加载特殊性

**风险**：ImageLoader 依赖 multimodal_service

**缓解**：
- 保持原有的 try/except 导入模式
- 不修改 multimodal_service
- 保持降级到 MultimodalDocumentConverter

---

## 五、回滚方案

### 5.1 即时回滚

如果发现严重问题，立即回滚：

```bash
# 恢复 rag/document.py
git checkout HEAD -- rag/document.py

# 删除新增目录
rm -rf rag/loaders/
```

### 5.2 分阶段回滚

如果某个 Loader 有问题：

1. 在 `rag/document.py` 中临时禁用该 Loader
2. 回退到原有逻辑
3. 修复 Loader 后重新启用

### 5.3 代码备份

迁移前备份关键文件：

```bash
cp rag/document.py rag/document_backup.py
```

---

## 六、测试计划

### 6.1 单元测试

每个 Loader 独立测试：

```python
# tests/test_loaders.py
def test_text_loader():
    loader = TextLoader()
    assert loader.can_load("test.txt")
    docs = loader.load("test/fixtures/test.txt")
    assert len(docs) > 0

def test_pdf_loader():
    loader = PdfLoader()
    assert loader.can_load("test.pdf")
    docs = loader.load("test/fixtures/test.pdf")
    assert len(docs) > 0
```

### 6.2 集成测试

测试完整的加载流程：

```python
# tests/test_document_integration.py
def test_load_documents_from_directory():
    docs = load_documents("test/fixtures/")
    assert len(docs) > 0
    # 验证 TXT、DOCX、PDF、图片都能加载
```

### 6.3 回归测试

确保现有功能不受影响：

```bash
# 运行现有测试
python -m pytest tests/ -v

# 特别关注
python -m pytest tests/test_document.py -v
python -m pytest tests/test_rag_service.py -v
```

### 6.4 验收测试

Phase 0 验收标准：

- [ ] TXT 知识库 rebuild 成功
- [ ] DOCX 知识库 rebuild 成功
- [ ] PDF 知识库 rebuild 成功
- [ ] 图片知识库 rebuild 成功
- [ ] RAG 问答正常
- [ ] 与 V2.2.1 结果无明显差异

---

## 七、时间估算

| 步骤 | 预计时间 | 风险等级 |
|------|---------|---------|
| Step 1: Loader 基类 | 0.5 天 | 低 |
| Step 2: TXT Loader | 0.5 天 | 低 |
| Step 3: Markdown Loader | 0.5 天 | 低 |
| Step 4: PDF Loader | 1 天 | 中 |
| Step 5: DOCX Loader | 0.5 天 | 低 |
| Step 6: Image Loader | 1 天 | 中 |
| Step 7: 重构 document.py | 1 天 | 高 |
| Step 8: 更新 __init__.py | 0.5 天 | 低 |
| Step 9: 清理旧代码 | 0.5 天 | 低 |
| 测试和验收 | 1 天 | - |
| **总计** | **7 天** | - |

---

## 八、依赖关系

```
Step 1 (BaseLoader)
    ↓
Step 2-6 (各 Loader，并行)
    ↓
Step 7 (重构 document.py)
    ↓
Step 8 (更新 __init__.py)
    ↓
Step 9 (清理旧代码)
```

---

## 九、注意事项

### 9.1 不做的事情

- ❌ 不修改 RAG 检索逻辑
- ❌ 不修改 embedding
- ❌ 不修改 vector_store
- ❌ 不接入 Qwen-VL
- ❌ 不改变 Document metadata 格式
- ❌ 不删除旧代码（除非确认迁移完成）

### 9.2 必须做的事情

- ✅ 保持 `load_document()` 接口兼容
- ✅ 保持 `load_documents()` 接口兼容
- ✅ 保持所有降级逻辑
- ✅ 保持所有日志输出
- ✅ 保持所有异常类型

### 9.3 代码风格

- 使用 type hints
- 添加 docstring
- 保持日志格式一致
- 遵循项目编码规范

---

**文档版本**：V1.0  
**创建日期**：2026-08-04  
**状态**：待审核