# services/knowledge_service.py
# 知识库业务服务层

import os
import re
import shutil
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from rag.document import load_documents
from rag.rag_service import (
    build_knowledge_base,
    list_available_subjects,
    _safe_dir_name,
    delete_knowledge_base,
)

logger = logging.getLogger(__name__)

# 项目根目录（services/ 的上一级）
PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# ========== 安全常量 ==========

# 允许上传的文件扩展名白名单
ALLOWED_EXTENSIONS = {'.txt', '.md', '.pdf', '.docx', '.jpg', '.jpeg', '.png', '.webp'}

# 禁止上传的文件扩展名黑名单（高危类型）
BLOCKED_EXTENSIONS = {'.py', '.exe', '.sh', '.bat', '.cmd', '.ps1', '.js', '.ts',
                      '.php', '.rb', '.pl', '.cgi', '.msi', '.dll', '.com', '.vbs',
                      '.reg', '.inf', '.scr', '.pif', '.jar', '.war'}

# 文件大小限制：20MB
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20MB

# 文件名最大长度
MAX_FILENAME_LENGTH = 255


def validate_filename(filename: str) -> Tuple[bool, str]:
    """
    验证文件名安全性

    检查项：
    1. 文件名非空
    2. 长度限制
    3. 路径穿越攻击防护（禁止 ../ 和绝对路径）
    4. 危险字符检查
    5. 扩展名白名单检查
    6. 扩展名黑名单检查

    Args:
        filename: 原始文件名

    Returns:
        (is_valid, error_message) 元组
    """
    if not filename or not filename.strip():
        return False, "文件名不能为空"

    # 清理文件名：仅取文件名部分，防止路径穿越
    clean_name = Path(filename).name

    # 长度检查
    if len(clean_name) > MAX_FILENAME_LENGTH:
        return False, f"文件名过长，最大允许 {MAX_FILENAME_LENGTH} 个字符"

    # 路径穿越检查：禁止 ../
    if '..' in clean_name:
        return False, "文件名包含非法字符（路径穿越）"

    # 绝对路径检查
    if Path(filename).is_absolute():
        return False, "不允许使用绝对路径作为文件名"

    # 危险字符检查：禁止 \ / : * ? " < > |
    dangerous_chars = re.compile(r'[/\\:*?"<>|]')
    if dangerous_chars.search(clean_name):
        return False, "文件名包含非法字符"

    # 扩展名检查
    file_ext = Path(clean_name).suffix.lower()
    if not file_ext:
        return False, "文件必须包含扩展名"

    # 黑名单检查
    if file_ext in BLOCKED_EXTENSIONS:
        return False, f"禁止上传 {file_ext} 类型文件（安全限制）"

    # 白名单检查
    if file_ext not in ALLOWED_EXTENSIONS:
        return False, f"不支持的文件格式: {file_ext}，支持: {', '.join(sorted(ALLOWED_EXTENSIONS))}"

    return True, ""


def validate_file_size(file_content: bytes) -> Tuple[bool, str]:
    """
    验证文件大小

    Args:
        file_content: 文件内容字节

    Returns:
        (is_valid, error_message) 元组
    """
    file_size = len(file_content)
    if file_size == 0:
        return False, "文件内容为空"
    if file_size > MAX_FILE_SIZE_BYTES:
        size_mb = round(file_size / (1024 * 1024), 2)
        return False, f"文件大小 {size_mb}MB 超过限制（最大 {MAX_FILE_SIZE_BYTES // (1024*1024)}MB）"
    return True, ""


def _resolve_path(path_str: str) -> Path:
    """将路径解析为绝对路径
    
    如果是相对路径，则基于项目根目录解析
    """
    p = Path(path_str)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


class KnowledgeServiceError(Exception):
    """知识库服务异常"""
    pass


def build_subject_knowledge(subject_id: str, source_path: str) -> Dict[str, Any]:
    """
    根据目录构建指定学科知识库
    
    Args:
        subject_id: 学科ID
        source_path: 源文件目录路径
        
    Returns:
        构建结果字典
        
    Raises:
        KnowledgeServiceError: 构建失败时抛出
    """
    try:
        # 解析路径为绝对路径
        docs_path = _resolve_path(source_path)
        
        # 检查路径是否存在
        if not docs_path.exists():
            raise KnowledgeServiceError(f"源文件目录不存在: {source_path} (解析为: {docs_path})")
        
        if not docs_path.is_dir():
            raise KnowledgeServiceError(f"路径不是目录: {source_path}")
        
        # 收集目录下的所有文件路径
        supported_extensions = {'.txt', '.md', '.pdf', '.docx', '.png', '.jpg', '.jpeg', '.webp'}
        file_paths = [
            f for f in docs_path.iterdir()
            if f.is_file() and f.suffix.lower() in supported_extensions
        ]
        
        if not file_paths:
            raise KnowledgeServiceError(f"目录中没有找到支持格式的文件: {source_path}")
        
        # 加载文档
        documents = load_documents(file_paths)
        
        # 构建知识库
        build_knowledge_base(
            documents=documents,
            subject_id=subject_id,
        )
        
        return {
            "success": True,
            "subject_id": subject_id,
            "document_count": len(documents),
            "status": "completed",
        }
        
    except KnowledgeServiceError:
        raise
    except Exception as e:
        logger.error(f"知识库构建异常: {str(e)}", exc_info=True)
        raise KnowledgeServiceError("知识库构建失败，请稍后重试")


def delete_subject_knowledge(subject_id: str) -> Dict[str, Any]:
    """
    删除指定学科知识库
    
    Args:
        subject_id: 学科ID
        
    Returns:
        删除结果字典
        
    Raises:
        KnowledgeServiceError: 删除失败时抛出
    """
    try:
        # 检查知识库是否存在
        persist_directory = _resolve_path(f"data/vector_store/{_safe_dir_name(subject_id)}")
        if not persist_directory.exists():
            raise KnowledgeServiceError(f"知识库不存在: {subject_id}")

        # 删除FAISS索引
        delete_knowledge_base(subject_id=subject_id)

        # 删除BM25索引（混合检索索引，与知识库同生命周期）
        bm25_directory = _resolve_path(f"data/bm25_index/{subject_id}")
        if bm25_directory.exists():
            shutil.rmtree(bm25_directory)
            logger.info(f"BM25索引已删除: {bm25_directory}")

        # 删除学科资料目录（含上传的原始文件与提取图片）
        subject_data_dir = _resolve_path(f"data/subjects/{subject_id}")
        if subject_data_dir.exists():
            shutil.rmtree(subject_data_dir)
            logger.info(f"学科资料目录已删除: {subject_data_dir}")

        return {
            "success": True,
            "subject_id": subject_id,
            "status": "deleted",
        }

    except KnowledgeServiceError:
        raise
    except Exception as e:
        logger.error(f"知识库删除异常: {str(e)}", exc_info=True)
        raise KnowledgeServiceError("知识库删除失败，请稍后重试")


def cleanup_subject_data(subject_id: str) -> None:
    """
    尽力清理学科的全部关联数据（FAISS/BM25/资料目录）。

    供删除学科记录时联动调用；各目录不存在时静默跳过，不抛异常。
    """
    for rel in (f"data/vector_store/{_safe_dir_name(subject_id)}",
                f"data/bm25_index/{subject_id}",
                f"data/subjects/{subject_id}"):
        try:
            p = _resolve_path(rel)
            if p.exists():
                shutil.rmtree(p)
                logger.info(f"清理学科数据: {p}")
        except Exception as e:
            logger.warning(f"清理学科数据失败（不阻断删除）: {rel}, {str(e)}")


def list_knowledge_bases() -> List[Dict[str, str]]:
    """
    获取所有已构建的知识库列表
    
    Returns:
        知识库信息列表，每个元素包含 subject_id 和 status
        status: "ready" (已构建), "empty" (未构建), "building" (构建中), "failed" (构建失败)
    """
    try:
        subjects = list_available_subjects()
        result = []
        for subject_id in subjects:
            persist_directory = _resolve_path(f"data/vector_store/{_safe_dir_name(subject_id)}")
            if persist_directory.exists() and any(persist_directory.iterdir()):
                status = "ready"
            else:
                status = "empty"
            result.append({"subject_id": subject_id, "status": status})
        return result
    except Exception as e:
        logger.error(f"获取知识库列表异常: {str(e)}", exc_info=True)
        raise KnowledgeServiceError("获取知识库列表失败")


def get_knowledge_base_stats(subject_id: str) -> Dict[str, Any]:
    """
    获取知识库统计信息
    
    Args:
        subject_id: 学科ID
        
    Returns:
        统计信息字典，包含统一的状态字段
        
    Raises:
        KnowledgeServiceError: 获取失败时抛出
    """
    try:
        # 检查知识库是否存在（使用 _safe_dir_name 处理中文路径）
        persist_directory = _resolve_path(f"data/vector_store/{_safe_dir_name(subject_id)}")
        if not persist_directory.exists():
            raise KnowledgeServiceError(f"知识库不存在: {subject_id}")
        
        # 获取目录大小
        total_size = sum(f.stat().st_size for f in persist_directory.rglob('*') if f.is_file())
        file_count = sum(1 for f in persist_directory.rglob('*') if f.is_file())
        
        # 统一状态判断：有向量文件则为ready，否则为empty
        has_knowledge_base = total_size > 0 and file_count > 0
        status = "ready" if has_knowledge_base else "empty"
        
        # 获取文档数量（上传的原始文件）
        documents = get_subject_documents(subject_id)
        document_count = len(documents)
        
        # 获取分块数量（向量数据库中的向量数量）
        # FAISS 在 Windows 上不支持中文路径，使用 tempfile 中转
        chunk_count = 0
        try:
            import faiss
            import tempfile
            faiss_index_path = persist_directory / "index.faiss"
            if faiss_index_path.exists():
                with tempfile.TemporaryDirectory() as tmp_dir:
                    tmp_index = os.path.join(tmp_dir, "index.faiss")
                    shutil.copy2(str(faiss_index_path), tmp_index)
                    index = faiss.read_index(tmp_index)
                    chunk_count = index.ntotal
        except Exception as e:
            logger.warning(f"获取分块数量失败: {str(e)}")
        
        return {
            "subject_id": subject_id,
            "status": status,
            "has_knowledge_base": has_knowledge_base,
            "document_count": document_count,
            "chunk_count": chunk_count,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
        }
        
    except KnowledgeServiceError:
        raise
    except Exception as e:
        logger.error(f"获取知识库统计信息异常: {str(e)}", exc_info=True)
        raise KnowledgeServiceError("获取知识库统计信息失败")


def upload_file_to_subject(subject_id: str, file_content: bytes, filename: str) -> Dict[str, Any]:
    """
    上传文件到指定学科目录
    
    Args:
        subject_id: 学科ID
        file_content: 文件内容
        filename: 文件名
        
    Returns:
        上传结果字典
        
    Raises:
        KnowledgeServiceError: 上传失败时抛出
    """
    try:
        # 1. 文件名安全校验
        valid, err_msg = validate_filename(filename)
        if not valid:
            logger.warning(f"文件名校验失败: {filename}, 原因: {err_msg}")
            raise KnowledgeServiceError(err_msg)

        # 使用清理后的安全文件名
        safe_filename = Path(filename).name

        # 2. 文件大小校验
        valid, err_msg = validate_file_size(file_content)
        if not valid:
            logger.warning(f"文件大小校验失败: {safe_filename}, 原因: {err_msg}")
            raise KnowledgeServiceError(err_msg)

        # 3. subject_id 安全校验（仅允许字母数字下划线连字符）
        if not re.match(r'^[\w\u4e00-\u9fff-]+$', subject_id):
            raise KnowledgeServiceError("学科ID格式不合法")

        # 4. 创建学科目录并保存文件
        subject_dir = _resolve_path(f"data/subjects/{subject_id}")
        subject_dir.mkdir(parents=True, exist_ok=True)

        file_path = subject_dir / safe_filename

        # 5. 检查文件是否已存在
        replaced = file_path.exists()
        if replaced:
            logger.info(f"文件已存在，将覆盖: {safe_filename}")

        with open(file_path, 'wb') as f:
            f.write(file_content)

        # v2.6：覆盖同名文件后旧索引仍指向旧内容，必须失效
        stale = _invalidate_subject_index(subject_id) if replaced else False

        logger.info(f"文件上传成功: subject={subject_id}, file={safe_filename}, size={len(file_content)}")

        return {
            "success": True,
            "subject_id": subject_id,
            "filename": safe_filename,
            "size": len(file_content),
            "status": "uploaded",
            "replaced": replaced,
            "index_stale": stale,
            "message": (f"文件 {safe_filename} 上传成功"
                        + ("（已覆盖同名文件，知识库索引已失效，请重新构建）" if stale else "")),
        }
        
    except KnowledgeServiceError:
        raise
    except Exception as e:
        logger.error(f"文件上传异常: {str(e)}", exc_info=True)
        raise KnowledgeServiceError("文件上传失败，请稍后重试")


def get_subject_document_path(subject_id: str, filename: str) -> Path:
    """
    获取学科文档的完整路径（带安全校验）
    
    Args:
        subject_id: 学科ID
        filename: 文件名
        
    Returns:
        文档完整路径
        
    Raises:
        KnowledgeServiceError: 校验失败或文件不存在
    """
    # 文件名安全校验
    valid, err_msg = validate_filename(filename)
    if not valid:
        raise KnowledgeServiceError(err_msg)

    # subject_id 安全校验
    if not re.match(r'^[\w\u4e00-\u9fff-]+$', subject_id):
        raise KnowledgeServiceError("学科ID格式不合法")

    safe_filename = Path(filename).name
    file_path = _resolve_path(f"data/subjects/{subject_id}/{safe_filename}")
    
    # 二次校验：确保解析后的路径确实在学科目录内
    subject_dir = _resolve_path(f"data/subjects/{subject_id}")
    if not str(file_path).startswith(str(subject_dir)):
        logger.warning(f"路径穿越检测: {filename}")
        raise KnowledgeServiceError("文件路径不合法")

    if not file_path.exists() or not file_path.is_file():
        raise KnowledgeServiceError(f"文件不存在: {safe_filename}")
    
    return file_path


def get_subject_documents(subject_id: str) -> List[Dict[str, Any]]:
    """
    获取学科目录下的所有文档列表
    
    Args:
        subject_id: 学科ID
        
    Returns:
        文档信息列表
        
    Raises:
        KnowledgeServiceError: 获取失败时抛出
    """
    try:
        subject_dir = _resolve_path(f"data/subjects/{subject_id}")
        
        if not subject_dir.exists():
            return []
        
        supported_extensions = {'.txt', '.md', '.pdf', '.docx', '.jpg', '.jpeg', '.png', '.webp'}
        documents = []
        
        for file_path in subject_dir.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in supported_extensions:
                stat = file_path.stat()
                documents.append({
                    "name": file_path.name,
                    "size": format_file_size(stat.st_size),
                    "type": file_path.suffix.lower(),
                    "upload_time": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })
        
        return documents
        
    except Exception as e:
        logger.error(f"获取文档列表异常: {str(e)}", exc_info=True)
        raise KnowledgeServiceError("获取文档列表失败")


def delete_subject_document(subject_id: str, filename: str) -> Dict[str, Any]:
    """
    删除学科目录下的单个文档
    
    Args:
        subject_id: 学科ID
        filename: 文件名
        
    Returns:
        删除结果字典
        
    Raises:
        KnowledgeServiceError: 删除失败时抛出
    """
    try:
        # 文件名安全校验
        valid, err_msg = validate_filename(filename)
        if not valid:
            raise KnowledgeServiceError(err_msg)

        # subject_id 安全校验
        if not re.match(r'^[\w\u4e00-\u9fff-]+$', subject_id):
            raise KnowledgeServiceError("学科ID格式不合法")

        safe_filename = Path(filename).name
        file_path = _resolve_path(f"data/subjects/{subject_id}/{safe_filename}")
        
        # 二次校验：确保解析后的路径确实在学科目录内
        subject_dir = _resolve_path(f"data/subjects/{subject_id}")
        if not str(file_path).startswith(str(subject_dir)):
            logger.warning(f"路径穿越检测: {filename}")
            raise KnowledgeServiceError("文件路径不合法")

        if not file_path.exists():
            raise KnowledgeServiceError(f"文件不存在: {safe_filename}")
        
        file_path.unlink()
        logger.info(f"文件删除成功: subject={subject_id}, file={safe_filename}")

        # v2.6 修复：删除源文件后必须让索引失效。
        # 此前只 unlink 文件、索引岿然不动，用户删完文件立刻提问，
        # 答案仍然引用那份已删文件的内容（100% 必然触发，不依赖并发或重启）。
        stale = _invalidate_subject_index(subject_id)

        return {
            "success": True,
            "subject_id": subject_id,
            "filename": safe_filename,
            "status": "deleted",
            "index_stale": stale,
            "message": (f"文件 {safe_filename} 已删除"
                        + ("，知识库索引已失效，请重新构建" if stale else "")),
        }
        
    except KnowledgeServiceError:
        raise
    except Exception as e:
        logger.error(f"删除文件异常: {str(e)}", exc_info=True)
        raise KnowledgeServiceError("删除文件失败，请稍后重试")


def _invalidate_subject_index(subject_id: str) -> bool:
    """
    使指定学科的内存索引缓存失效（v2.6 新增）。

    删除或覆盖源文件后调用，避免进程内继续返回陈旧内容。
    返回 True 表示确实存在索引（调用方据此提示用户重建）。
    """
    existed = False
    try:
        from rag.vector_store import GlobalFAISS
        from rag.rag_service import _safe_dir_name
        safe = _safe_dir_name(subject_id)
        existed = GlobalFAISS.exists(safe)
        GlobalFAISS.invalidate(safe)
    except Exception as e:
        logger.warning(f"FAISS 缓存失效失败: {e}")
    try:
        from rag.hybrid_retriever import clear_index
        clear_index(subject_id)
    except Exception as e:
        logger.warning(f"BM25 缓存失效失败: {e}")
    return existed


def rebuild_subject_knowledge(subject_id: str) -> Dict[str, Any]:
    """
    重新构建学科知识库
    
    Args:
        subject_id: 学科ID
        
    Returns:
        构建结果字典
        
    Raises:
        KnowledgeServiceError: 构建失败时抛出
    """
    try:
        subject_dir = _resolve_path(f"data/subjects/{subject_id}")
        
        if not subject_dir.exists():
            raise KnowledgeServiceError(f"学科目录不存在: {subject_id}")
        
        # 收集目录下的所有文件路径
        supported_extensions = {'.txt', '.md', '.pdf', '.docx', '.png', '.jpg', '.jpeg', '.webp'}
        file_paths = [
            f for f in subject_dir.iterdir()
            if f.is_file() and f.suffix.lower() in supported_extensions
        ]
        
        if not file_paths:
            raise KnowledgeServiceError(f"目录中没有找到支持格式的文件")
        
        # 加载文档
        documents = load_documents(file_paths)

        # v2.6 关键修复：此前是「先删除旧知识库 → 再重建」，
        # 一旦重建失败（抽取异常、维度不符、磁盘问题…）旧索引已不可恢复，
        # 用户会直接丢失整个学科知识库。现在改为：
        #   1) 先把旧索引备份到 .bak
        #   2) 构建新索引
        #   3) 成功则删除备份；失败则回滚备份
        persist_directory = _resolve_path(f"data/vector_store/{_safe_dir_name(subject_id)}")
        bm25_directory = _resolve_path(f"data/bm25_index/{subject_id}")
        backup_dir = persist_directory.parent / f"{persist_directory.name}.bak"
        bm25_backup_dir = bm25_directory.parent / f"{bm25_directory.name}.bak"

        def _backup():
            """备份现有索引（存在才备份）"""
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)
            if persist_directory.exists():
                shutil.copytree(persist_directory, backup_dir)
            if bm25_backup_dir.exists():
                shutil.rmtree(bm25_backup_dir, ignore_errors=True)
            if bm25_directory.exists():
                shutil.copytree(bm25_directory, bm25_backup_dir)

        def _rollback():
            """用备份恢复旧索引"""
            restored = False
            if backup_dir.exists():
                if persist_directory.exists():
                    shutil.rmtree(persist_directory, ignore_errors=True)
                shutil.copytree(backup_dir, persist_directory)
                restored = True
            if bm25_backup_dir.exists():
                if bm25_directory.exists():
                    shutil.rmtree(bm25_directory, ignore_errors=True)
                shutil.copytree(bm25_backup_dir, bm25_directory)
                restored = True
            if restored:
                _invalidate_subject_index(subject_id)
                logger.warning(f"知识库重建失败，已从备份回滚: {subject_id}")

        def _cleanup_backup():
            shutil.rmtree(backup_dir, ignore_errors=True)
            shutil.rmtree(bm25_backup_dir, ignore_errors=True)

        _backup()
        try:
            # 删除旧索引后重建（失败会被下面的 except 回滚）
            if persist_directory.exists():
                delete_knowledge_base(subject_id=subject_id)

            vector_store = build_knowledge_base(
                documents=documents,
                subject_id=subject_id,
            )
        except Exception:
            _rollback()
            raise
        _cleanup_backup()

        # 统计过滤效果（供前端提示"知识库体量"是否异常）
        try:
            from rag.vector_store import read_index_meta
            meta = read_index_meta(str(persist_directory)) or {}
        except Exception:
            meta = {}

        return {
            "success": True,
            "subject_id": subject_id,
            "document_count": len(documents),
            "chunk_count": getattr(vector_store, "count", lambda: None)(),
            "chunks_before_filter": meta.get("chunks_before_filter"),
            "chunks_dropped": meta.get("chunks_dropped"),
            "status": "rebuilt",
            "message": f"知识库 {subject_id} 重新构建成功",
        }
        
    except KnowledgeServiceError:
        raise
    except Exception as e:
        logger.error(f"知识库重新构建异常: {str(e)}", exc_info=True)
        raise KnowledgeServiceError("知识库重新构建失败，请稍后重试")


def format_file_size(size_bytes: int) -> str:
    """
    格式化文件大小
    
    Args:
        size_bytes: 文件大小（字节）
        
    Returns:
        格式化后的文件大小字符串
    """
    if size_bytes == 0:
        return "0 B"
    
    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    
    return f"{size_bytes:.2f} {size_names[i]}"