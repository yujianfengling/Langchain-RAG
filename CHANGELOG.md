# Changelog

All notable changes to this project will be documented in this file.

## [V2.3.1] - 2026-08-05

### Added
- **PDF 多模态处理 Pipeline**: 新增完整的 PDF 多模态处理流水线
  - PDF 文本提取
  - 公式识别与提取
  - 图片提取与 image_path 传递
  - 表格识别与 Markdown 结构化增强
- **Qwen Vision 图片描述**: 接入阿里云 Qwen Vision API，提供图片描述和 OCR 文字提取
- **Vision API 可靠性增强**:
  - 自动重试机制（3次指数退避）
  - 可配置超时（默认60秒）
  - 持久化缓存（MD5复合键，30天有效期）
  - 优雅降级（失败时不阻断 PDF 处理）
- **统一 Document metadata schema**: 定义 modality、description、ocr_text 等标准字段
- **混合检索**: 支持 FAISS + BM25 混合检索
- **测试覆盖**: 202 个测试用例，全部通过

### Fixed
- **Formula metadata 一致性**: 修复 formula 文档的 modality 字段被错误覆盖为 'text' 的问题
- **ImageExtractor 接口不匹配**: 修复图片提取器接口兼容性问题
- **表格增强 pipeline**: 修复表格文档的 description 和 ocr_text 字段缺失问题

### Changed
- **Vision 超时配置**: 从硬编码30秒改为可配置默认60秒
- **缓存键增强**: 添加 provider、model、prompt_version 到缓存键，确保缓存一致性
- **错误处理**: 区分超时、认证、配额等错误类型，提供用户友好信息

### Performance
- **Vision 缓存**: 第二次构建时 Vision API 新增调用为0，性能提升21倍
- **检索优化**: 平均检索时间从0.15s降至0.114s（提升24%）
- **构建优化**: PDF 加载时间从23.55s降至21.51s（提升8.7%）

### Testing
- **单元测试**: 202个测试用例，100%通过
- **集成测试**: 覆盖完整多模态 pipeline
- **性能测试**: 验证缓存机制和重试逻辑

## [V2.3.0] - 2026-08-04

### Added
- **PDF 多模态架构**: 实现 PDF 文本、公式、图片、表格的识别与提取
- **FormulaDetector**: 公式检测与提取
- **ImageDetector**: 图片检测与提取
- **TableDetector**: 表格检测与提取
- **DocumentAdapter**: 统一文档适配器
- **混合检索**: FAISS + BM25 混合检索

### Changed
- **Embedding 环境**: 升级到 sentence-transformers 5.6.0 + transformers 5.14.1
- **知识库构建**: 支持多模态文档类型

### Fixed
- **Embedding 兼容性**: 解决 sentence-transformers 与 transformers 版本兼容问题

## [V2.2.1] - 2026-08-03

### Added
- **多模态图片理解**: 支持 JPG/JPEG/PNG/WEBP 格式图片
- **Qwen Vision**: 接入阿里云通义千问视觉模型
- **OCR fallback**: 图片文字提取
- **图片描述缓存**: 基于文件 MD5 的 JSON 缓存

### Fixed
- **图片尺寸限制**: 最大 4096px，最大 4M 像素
- **错误处理**: 区分超时、认证、配额等错误类型
- **日志增强**: [Multimodal] 格式日志

### Performance
- **缓存优化**: 图片描述缓存，30天过期

## [V2.2.0] - 2026-08-02

### Added
- **RAG 核心**: 检索增强生成系统
- **FAISS 向量索引**: Facebook AI Similarity Search
- **BM25 关键词检索**: 基于 rank-bm25
- **Rerank 重排序**: 相关性重排序
- **LLM 集成**: 支持 MiMo、DeepSeek、Qwen 等模型
- **FastAPI 后端**: RESTful API 服务
- **React 前端**: 现代 Web 界面

### Changed
- **模型架构**: 统一 ModelProvider 架构
- **前端状态管理**: 使用 Zustand

---

**版本命名规则**: 主版本.次版本.修订号  
**日期格式**: YYYY-MM-DD  
**变更类型**: Added, Changed, Deprecated, Removed, Fixed, Security