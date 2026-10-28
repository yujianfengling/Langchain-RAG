# services/knowledge_service.py
# 知识库业务服务层

from config.path_config import get_data_dir
import os
import re
import shutil
import uuid
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

def supported_document_extensions() -> set:
    """
    当前**可真实解析进入检索**的扩展名（不含压缩包；含可用的转换格式）。

    单一事实来源是 rag.formats：新增/降级格式只改那里，避免"这里能选、
    解析器不认识"的不一致。.ppt/.doc 只有在检测到 LibreOffice 时才算可用。
    """
    try:
        from rag import formats
        return set(formats.parseable_extensions())
    except Exception as e:  # 退化只影响可用格式集合，不影响安全校验
        logger.warning(f"读取格式能力清单失败，退化为核心格式: {e}")
        return {'.txt', '.md', '.pdf', '.docx', '.png', '.jpg', '.jpeg', '.webp'}


# 允许上传的文件扩展名白名单（与 rag.formats 保持一致）
ALLOWED_EXTENSIONS = supported_document_extensions()

# 禁止上传的文件扩展名黑名单（高危类型）
BLOCKED_EXTENSIONS = {'.py', '.exe', '.sh', '.bat', '.cmd', '.ps1', '.js', '.ts',
                      '.php', '.rb', '.pl', '.cgi', '.msi', '.dll', '.com', '.vbs',
                      '.reg', '.inf', '.scr', '.pif', '.jar', '.war'}

# 文件大小上限：唯一来源 rag/formats.limits()（普通文档 max_file_mb、
# 压缩包 max_archive_mb），业务层不再硬编码 20MB。

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

    # 危险字符检查：禁止 \ / : * ? " < > |。必须检查原始输入，
    # 否则 "../../lesson.txt" 会先被 Path.name 去掉目录而被静默接受。
    dangerous_chars = re.compile(r'[/\\:*?"<>|]')
    if dangerous_chars.search(filename):
        return False, "文件名包含非法字符或目录分隔符"

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


def max_file_size_bytes(filename: Optional[str] = None) -> int:
    """
    单个文件的上传上限（字节）。

    唯一来源 rag.formats.limits()：压缩包用 max_archive_mb，其余用 max_file_mb。
    """
    from rag import formats
    ext = formats.normalize_ext(Path(filename).suffix) if filename else ""
    return formats.upload_limit_bytes(ext)


def validate_file_size(file_content: bytes,
                       filename: Optional[str] = None) -> Tuple[bool, str]:
    """
    验证文件大小（按"是否压缩包"取不同上限）

    Args:
        file_content: 文件内容字节
        filename: 原始文件名（用于判定压缩包上限）；缺省按普通文档上限

    Returns:
        (is_valid, error_message) 元组
    """
    file_size = len(file_content)
    if file_size == 0:
        return False, "文件内容为空"

    from rag import formats
    ext = formats.normalize_ext(Path(filename).suffix) if filename else ""
    spec = formats.get_format(ext)
    limit = formats.upload_limit_bytes(ext)
    if file_size > limit:
        size_mb = round(file_size / (1024 * 1024), 2)
        limit_mb = limit // (1024 * 1024)
        kind = "压缩包" if (spec is not None and spec.archive) else "普通文档"
        return False, f"文件大小 {size_mb}MB 超过{kind}上限 {limit_mb}MB"
    return True, ""


def _resolve_path(path_str: str) -> Path:
    """
    将路径解析为绝对路径

    - 以 `data/` 开头 → 基于统一数据根目录（config.path_config.get_data_dir，
      可用 EDU_DATA_DIR 覆盖），保证测试/部署可与正式数据隔离
    - 其余相对路径 → 基于项目根目录
    """
    p = Path(path_str)
    if not p.is_absolute():
        parts = p.parts
        if parts and parts[0] == "data":
            p = get_data_dir().joinpath(*parts[1:])
        else:
            p = PROJECT_ROOT / p
    return p.resolve()


# 允许被扫描/建库的受控根目录（阶段一补验安全修复）
# 说明：建库接口此前接受任意绝对路径，匿名者可以让服务扫描服务器任意目录
# 并把内容索引进知识库（再经检索接口外泄）。这里把可扫描范围限制在
# 受控目录内，越界一律拒绝。
# 注意：受控根目录必须与 _resolve_path 使用**同一个数据根**（get_data_dir()）。
# 否则当 EDU_DATA_DIR 指向别处时，"data/subjects/x" 会被解析到新数据根，
# 却拿去和项目内的 <repo>/data 比较 → 隔离部署下建库一律被拒（2026-10-07 修复）。
def _allowed_source_roots() -> tuple:
    data_root = get_data_dir()
    return (
        data_root / "subjects",
        data_root / "uploads",
        data_root / "knowledge_sources",
    )


def ensure_allowed_source_dir(path_str: str) -> Path:
    """
    校验"知识库源目录"是否位于受控根目录内，返回解析后的绝对路径。

    Raises:
        KnowledgeServiceError: 路径越界 / 不存在 / 不是目录
    """
    if not path_str or not str(path_str).strip():
        raise KnowledgeServiceError("源目录不能为空")
    resolved = _resolve_path(path_str)
    allowed = False
    for root in _allowed_source_roots():
        try:
            root_resolved = root.resolve()
        except OSError:
            continue
        if resolved == root_resolved or root_resolved in resolved.parents:
            allowed = True
            break
    if not allowed:
        logger.warning(f"拒绝越界的知识库源目录: {resolved}")
        raise KnowledgeServiceError(
            "源目录必须是受控目录下的子目录（data/subjects、data/uploads、"
            "data/knowledge_sources）")
    if not resolved.exists():
        raise KnowledgeServiceError("源目录不存在")
    if not resolved.is_dir():
        raise KnowledgeServiceError("路径不是目录")
    return resolved


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
        # 解析并校验路径（仅允许受控目录，防止扫描服务器任意路径）
        docs_path = ensure_allowed_source_dir(source_path)
        
        # 收集目录下的所有文件路径（支持格式来自 rag.formats）
        supported_extensions = supported_document_extensions()
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

        # 删除索引旁挂目录（.building/.old/.prev/.bak），否则已删除内容仍有备份残留
        _remove_index_sidecars(subject_id)

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
    _remove_index_sidecars(subject_id)


def get_vector_store_dir(subject_id: str) -> str:
    """
    获取学科向量库目录（统一路径解析入口）

    供接口层做"知识库是否已构建"的前置校验，避免各处各自硬编码项目根目录。
    """
    from rag.rag_service import _safe_dir_name
    return str(_resolve_path(f"data/vector_store/{_safe_dir_name(subject_id)}"))


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

        # 2. 文件大小校验（按是否压缩包取不同上限）
        valid, err_msg = validate_file_size(file_content, filename)
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
    # subject_id 安全校验（此前本函数缺少校验，与其它函数不一致）
    if not subject_id or not re.match(r'^[\w一-鿿-]+$', subject_id):
        raise KnowledgeServiceError("学科ID格式不合法")
    try:
        subject_dir = _resolve_path(f"data/subjects/{subject_id}")
        
        if not subject_dir.exists():
            return []
        
        supported_extensions = supported_document_extensions()
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

        # 2026-10-08 修复（真实复现）：**只失效缓存是不够的**。
        # 磁盘索引里仍然带着该文件的 chunk，下一次检索重新加载后依旧会召回已删内容
        # （实测：删除 ALPHA-R9.txt 后提问，答案与来源里仍然出现它）。
        # 因此删除后必须用**剩余资料**重建一次；增量向量化会复用未变化资料的分块与向量，
        # 所以这次重建代价很小。若已无剩余资料，则直接清掉知识库。
        try:
            remaining = [
                f for f in subject_dir.iterdir()
                if f.is_file() and f.suffix.lower() in supported_document_extensions()
            ]
        except OSError:
            remaining = []
        try:
            if remaining:
                rebuild_subject_knowledge(subject_id)
                logger.info(f"删除后已重建索引（剩余 {len(remaining)} 份资料）: {subject_id}")
            else:
                delete_subject_knowledge(subject_id)
                logger.info(f"删除最后一份资料，已清理知识库: {subject_id}")
        except Exception as e:
            # 重建失败不能把"删除"本身变成失败：文件已删、缓存已失效，
            # 但要留下明确告警，提示需要手动重建。
            logger.warning(f"删除后重建索引失败（文件已删除，请手动重建）: {subject_id}: {e}")

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


def _attach_material_ids(subject_id: str, documents: List[Any]) -> int:
    """
    给课程学科的分块写入 `material_id`（**稳定的资料标识**）

    目的：检索期据此执行"资料可见范围"过滤（见 services.teaching_access
    .filter_visible_documents），从而
    - 不必只靠文件名判断来源（文件名可能被重命名或重名）；
    - 改可见范围 / 删除资料时无需重建索引即可生效（检索期实时读库）。

    约束：
    - 只对**已被课程认领**的学科生效；历史/公共学科没有资料与可见范围的概念。
    - 任何异常都不阻断建库：拿不到标识时分块不带 `material_id`，
      检索期退化为按文件名认领的兼容路径。

    Returns:
        被标注的分块数量（供日志与验收观察）
    """
    if not documents:
        return 0
    try:
        from services import teaching_data as td
        owner = td.subject_owner(subject_id)
        if not owner:
            return 0
        by_name = {
            str(m.get("filename")): m.get("id")
            for m in td.list_materials(owner["course_id"], include_teacher_only=True)
            if m.get("filename")
        }
        if not by_name:
            return 0
        tagged = 0
        for doc in documents:
            metadata = getattr(doc, "metadata", None)
            if not isinstance(metadata, dict):
                continue
            name = metadata.get("file_name")
            if not name:
                src = metadata.get("source") or ""
                name = str(src).split("\\")[-1].split("/")[-1] if src else ""
            material_id = by_name.get(str(name))
            if material_id:
                metadata["material_id"] = material_id
                tagged += 1
        if tagged:
            logger.info("课程分块已标注资料标识: subject=%s tagged=%d/%d",
                        subject_id, tagged, len(documents))
        return tagged
    except Exception as e:
        logger.warning(f"资料标识标注失败（不影响建库，检索期退化为按文件名认领）: {e}")
        return 0


# ========== 索引的"版本化构建 + 原子发布"（2026-10-08） ==========
#
# 问题（已实测）：旧的 _build_index_with_rollback 是「备份 → 删除线上索引 →
# 重建 → 成功清备份 / 失败回滚」。删除与重建之间（大库可达数分钟）线上索引
# 是空的，学生检索不到任何资料；进程若在此窗口被杀，回滚不会执行，线上索引
# 会永久为空（实例：vector_store/sub-kg5yy0t4 为 0 文件、bm25 目录不存在，
# 而该学科有 25 份"可学习"资料）。
#
# 新方案：新版本构建到 staging 目录并校验，再用两次 rename 原子发布。
# 构建期间线上目录保持原样，检索继续读旧版本；失败只清理 staging，
# 线上旧版本完全不受影响（不需要回滚，因为没有破坏）。
_STAGING_SUFFIX = ".building"
_OLD_SUFFIX = ".old"
_PREV_SUFFIX = ".prev"
_INDEX_SIDECAR_SUFFIXES = (".building", ".old", ".prev", ".bak")


def _count_index_files(root: Path) -> int:
    """统计目录下文件数（不存在 / 不可读返回 0）"""
    try:
        return sum(1 for f in root.rglob("*") if f.is_file()) if root.exists() else 0
    except OSError:
        return 0


def _sidecar(base: Path, suffix: str) -> Path:
    """与索引目录同级的旁挂目录，如 vector_store/<name>.building"""
    return base.parent / f"{base.name}{suffix}"


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _remove_index_sidecars(subject_id: str) -> None:
    """删除学科索引的全部旁挂目录（staging/old/prev/bak）。

    删除知识库时必须一并清理：否则"已删除"的资料仍以备份副本留在磁盘上。
    """
    for base in (_resolve_path(f"data/vector_store/{_safe_dir_name(subject_id)}"),
                 _resolve_path(f"data/bm25_index/{subject_id}")):
        for suffix in _INDEX_SIDECAR_SUFFIXES:
            _remove_tree(base.parent / f"{base.name}{suffix}")


def _restore_sidecar_if_live_empty(live: Path, sidecar: Path) -> bool:
    """发布/构建被中断后：线上缺失或为空而 sidecar 有有效内容 → 用 sidecar 恢复线上。

    典型场景：原子发布刚把「线上 → .old」，进程被杀，尚未执行「staging → 线上」。
    此时线上为空而 .old 是完整旧版本；下一次建库前先恢复，避免空窗延续。
    """
    if _count_index_files(live) > 0 or _count_index_files(sidecar) == 0:
        return False
    if live.exists():
        _remove_tree(live)
    shutil.move(str(sidecar), str(live))
    logger.warning(f"[index-publish] 发布中断恢复: {sidecar.name} -> {live.name}")
    return True


def _recover_index_sidecars(subject_id: str, live: Path, old: Path, prev: Path) -> None:
    """建库前修复上一次被中断的发布残局（.old / .prev → 线上）。"""
    if _restore_sidecar_if_live_empty(live, old):
        logger.warning(f"[index-publish] 已从 .old 恢复线上索引: {subject_id}")
    elif _restore_sidecar_if_live_empty(live, prev):
        logger.warning(f"[index-publish] 已从 .prev 恢复线上索引: {subject_id}")


def _invalidate_memory_index(subject_id: str) -> None:
    """发布后让进程内缓存失效——**只清内存，不删磁盘**。

    必须区别于 _invalidate_subject_index：后者调用的 hybrid_retriever.clear_index
    会删除磁盘上的 bm25.pkl/bm25_meta.json。原子发布刚把新索引 rename 到线上，
    若在此调用 clear_index 会把刚发布的新版本删掉（实测会把 BM25 一起清空）。
    """
    try:
        from rag.vector_store import GlobalFAISS
        GlobalFAISS.invalidate(_safe_dir_name(subject_id))
    except Exception as e:
        logger.warning(f"FAISS 缓存失效失败: {e}")
    try:
        from rag.hybrid_retriever import invalidate_index
        invalidate_index(subject_id)
    except Exception as e:
        logger.warning(f"BM25 缓存失效失败: {e}")


def _count_faiss_vectors(persist_dir: Path) -> int:
    """读取 FAISS 索引的真实块数（Windows 中文路径需临时 ASCII 中转）"""
    import faiss
    import tempfile
    index_path = persist_dir / "index.faiss"
    if not index_path.exists():
        return -1
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_index = os.path.join(tmp_dir, "index.faiss")
        shutil.copy2(str(index_path), tmp_index)
        return int(faiss.read_index(tmp_index).ntotal)


def _count_bm25_documents(bm25_dir: Path):
    """读取 BM25 索引的条目数及其元数据（bm25_meta.json）"""
    import pickle
    pkl_path = bm25_dir / "bm25.pkl"
    if not pkl_path.exists():
        return -1, {}
    try:
        from rag.hybrid_retriever import read_bm25_meta
        meta = read_bm25_meta(str(bm25_dir))
    except Exception:
        meta = {}
    try:
        with open(pkl_path, "rb") as f:
            data = pickle.load(f)
    except Exception as e:
        logger.warning(f"读取 staging BM25 索引失败: {e}")
        return -1, meta
    return len(data.get("documents", []) or []), meta


def _validate_staged_index(subject_id: str, staging_vector: Path,
                           staging_bm25: Path) -> Dict[str, Any]:
    """发布前校验 staging 索引：meta 存在、能加载、块数>0，且三者同一版本。

    Returns:
        {"faiss_count", "bm25_count", "meta", "bm25_meta"}

    Raises:
        KnowledgeServiceError: 任一校验失败（调用方据此清理 staging 并保留线上）
    """
    from rag.vector_store import read_index_meta

    meta = read_index_meta(str(staging_vector))
    if not meta:
        raise KnowledgeServiceError(f"staging 索引缺少 meta.json: {subject_id}")

    faiss_count = _count_faiss_vectors(staging_vector)
    if faiss_count <= 0:
        raise KnowledgeServiceError(
            f"staging FAISS 索引无法加载或为空（ntotal={faiss_count}）: {subject_id}")

    bm25_count, bm25_meta = _count_bm25_documents(staging_bm25)
    if bm25_count <= 0:
        raise KnowledgeServiceError(
            f"staging BM25 索引缺失或为空（documents={bm25_count}）: {subject_id}")

    meta_docs = int(meta.get("documents") or 0)
    if not (meta_docs == faiss_count == bm25_count):
        raise KnowledgeServiceError(
            f"staging 索引版本不一致（FAISS={faiss_count} / BM25={bm25_count} / "
            f"meta.documents={meta_docs}），拒绝发布: {subject_id}")

    build_id = meta.get("build_id")
    if not build_id or bm25_meta.get("build_id") != build_id:
        raise KnowledgeServiceError(
            f"staging 索引 build_id 不一致（meta={build_id!r} / "
            f"bm25={bm25_meta.get('build_id')!r}），拒绝发布: {subject_id}")

    return {"faiss_count": faiss_count, "bm25_count": bm25_count,
            "meta": meta, "bm25_meta": bm25_meta}


def _publish_index_pair(live_v: Path, staging_v: Path, old_v: Path, prev_v: Path,
                         live_b: Path, staging_b: Path, old_b: Path, prev_b: Path) -> None:
    """成对原子发布向量索引与 BM25 索引（全部是同卷 rename，毫秒级窗口）。

    两个目录无法用一次 rename 同时替换，因此分两批：
    1) 把存在的线上目录统一挪到 .old（旧版本离位）；
    2) 把 staging 统一切到线上（新版本就位）；
    3) 把 .old 转存为 .prev（"上一次有效版本"的可恢复备份）。
    任一步失败都把已挪走的旧版本放回线上，避免留下"新 FAISS + 旧 BM25"
    或空窗；staging 由调用方清理。
    """
    moved: list = []
    for live, old in ((live_v, old_v), (live_b, old_b)):
        if _count_index_files(live) > 0:
            _remove_tree(old)
            os.replace(str(live), str(old))
            moved.append((live, old))
        elif live.exists():
            # 线上的空目录不参与发布，也不覆盖有效备份
            _remove_tree(live)

    replaced: list = []
    try:
        for live, staging in ((live_v, staging_v), (live_b, staging_b)):
            os.replace(str(staging), str(live))
            replaced.append((live, staging))
    except Exception:
        # 把本次已切上去的新版本挪回 staging（随后清理）
        for live, staging in replaced:
            if not staging.exists() and _count_index_files(live) > 0:
                os.replace(str(live), str(staging))
        # 把旧版本放回线上
        for live, old in moved:
            if _count_index_files(old) > 0 and _count_index_files(live) == 0:
                os.replace(str(old), str(live))
        _remove_tree(staging_v)
        _remove_tree(staging_b)
        raise

    # 成功：保留"上一次有效版本"作为可恢复备份
    for old, prev in ((old_v, prev_v), (old_b, prev_b)):
        if _count_index_files(old) > 0:
            _remove_tree(prev)
            os.replace(str(old), str(prev))
        else:
            _remove_tree(old)


def _build_index_with_rollback(subject_id: str, documents: List[Any]) -> Dict[str, Any]:
    """
    独立目录构建 → 校验 → 原子发布（2026-10-08 重构）。

    与旧实现（备份 → 删线上 → 重建 → 失败回滚）的区别：
    - 新版本构建到 "<name>.building"，构建期间线上目录**保持原样**，
      检索继续读旧版本，不存在"删除与重建之间的空窗"；
    - 构建成功后校验（meta 存在 / FAISS 能加载且块数>0 / BM25 存在），
      再 线上→.old、staging→线上 两次 rename 发布（毫秒级窗口）；
    - 失败只清理 staging，线上旧版本完全不受影响（无需回滚）；
    - .old 随后转存为 .prev（上一次有效版本的可恢复备份），
      空目录绝不会覆盖有效备份；
    - FAISS / BM25 / meta.json 来自同一次构建，并用 build_id 绑定。

    这是 rebuild 与批量导入**共用**的唯一建库路径。
    """
    if not documents:
        raise KnowledgeServiceError("没有可入库的文档内容")

    # 给课程学科的分块打上**稳定的资料标识**（material_id），供检索期做
    # "资料可见范围"过滤：改可见范围/删资料无需重建即可生效，
    # 且不必依赖文件名匹配（文件名可能被重命名或重名）。
    _attach_material_ids(subject_id, documents)

    safe_name = _safe_dir_name(subject_id)
    persist_directory = _resolve_path(f"data/vector_store/{safe_name}")
    bm25_directory = _resolve_path(f"data/bm25_index/{subject_id}")

    staging_vector = _sidecar(persist_directory, _STAGING_SUFFIX)
    staging_bm25 = _sidecar(bm25_directory, _STAGING_SUFFIX)
    old_vector = _sidecar(persist_directory, _OLD_SUFFIX)
    old_bm25 = _sidecar(bm25_directory, _OLD_SUFFIX)
    prev_vector = _sidecar(persist_directory, _PREV_SUFFIX)
    prev_bm25 = _sidecar(bm25_directory, _PREV_SUFFIX)

    # 0. 修复上一次被中断的发布（线上缺失时先恢复 .old / .prev）
    _recover_index_sidecars(subject_id, persist_directory, old_vector, prev_vector)
    _recover_index_sidecars(subject_id, bm25_directory, old_bm25, prev_bm25)

    # 1. 清理上一次残留的 staging（未完成的构建不值得保留）
    _remove_tree(staging_vector)
    _remove_tree(staging_bm25)

    build_id = uuid.uuid4().hex
    vector_store = None
    try:
        # 2. 在独立目录里构建新版本——线上目录与内存缓存都不动
        vector_store = build_knowledge_base(
            documents=documents,
            subject_id=subject_id,
            persist_dir=str(staging_vector),
            bm25_dir=str(staging_bm25),
            update_cache=False,
            strict=True,
            build_id=build_id,
        )
        # 3. 发布前校验：能加载、块数>0、meta 存在，且同一版本
        _validate_staged_index(subject_id, staging_vector, staging_bm25)
    except BaseException:
        # 失败保留旧版本：只清 staging，线上未被触碰
        _remove_tree(staging_vector)
        _remove_tree(staging_bm25)
        raise

    # 4. 原子发布：线上 → .old，staging → 线上，.old → .prev（成对切换）
    try:
        _publish_index_pair(
            persist_directory, staging_vector, old_vector, prev_vector,
            bm25_directory, staging_bm25, old_bm25, prev_bm25,
        )
    except Exception:
        _remove_tree(staging_vector)
        _remove_tree(staging_bm25)
        _invalidate_memory_index(subject_id)
        raise

    # 5. 让进程内缓存失效（仅内存），检索下一次会加载新版本
    _invalidate_memory_index(subject_id)

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
        "build_id": build_id,
        # 2026-10-08：向量化增量统计（命中缓存 = 未变化分块，未调用 Embedding）
        "embedding_stats": dict(
            getattr(vector_store, "embed_stats", None)
            or (meta.get("embedding_stats") if isinstance(meta, dict) else None)
            or {}),
    }


def collect_subject_documents(subject_id: str) -> List[Any]:
    """解析学科目录下所有可解析文件（供 rebuild / 导入共用）"""
    subject_dir = _resolve_path(f"data/subjects/{subject_id}")
    if not subject_dir.exists():
        raise KnowledgeServiceError(f"学科目录不存在: {subject_id}")
    supported_extensions = supported_document_extensions()
    file_paths = [
        f for f in subject_dir.iterdir()
        if f.is_file() and f.suffix.lower() in supported_extensions
    ]
    if not file_paths:
        raise KnowledgeServiceError("目录中没有找到支持格式的文件")
    return load_documents(file_paths)


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
        documents = collect_subject_documents(subject_id)
        if not documents:
            raise KnowledgeServiceError("目录中没有可解析的文件内容")
        result = _build_index_with_rollback(subject_id, documents)
        return {
            **result,
            "status": "rebuilt",
            "message": f"知识库 {subject_id} 重新构建成功",
        }
        
    except KnowledgeServiceError:
        raise
    except Exception as e:
        logger.error(f"知识库重新构建异常: {str(e)}", exc_info=True)
        raise KnowledgeServiceError("知识库重新构建失败，请稍后重试")


def rebuild_subject_knowledge_from_documents(subject_id: str,
                                             documents: List[Any]) -> Dict[str, Any]:
    """
    用**已解析**的完整 documents 集合重建学科知识库（批量导入专用）。

    导入任务先把本批资料与既有资料一起解析好，再统一调用一次，
    避免"每个文件触发整库重建"，也避免重复解析。
    """
    try:
        result = _build_index_with_rollback(subject_id, documents)
        return {
            **result,
            "status": "rebuilt",
            "message": f"知识库 {subject_id} 构建成功",
        }
    except KnowledgeServiceError:
        raise
    except Exception as e:
        logger.error(f"知识库构建异常: {str(e)}", exc_info=True)
        raise KnowledgeServiceError("知识库构建失败，请稍后重试")


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

def restore_orphaned_index_backups() -> int:
    """启动时修复"建库被中断"留下的空索引（返回修复的学科数）。

    为什么需要（2026-10-07 真实事故）：
    旧实现「备份 → **删除现有索引** → 重建 → 成功删备份 / 失败回滚」在删除与重建之间
    留下空窗；进程若在这段时间被杀，except 分支永远不执行，**回滚不会发生**，
    于是 .bak 留在磁盘、线上索引永久为空。
    实测：vector_store/sub-kg5yy0t4 为 0 个文件，而同目录 sub-kg5yy0t4.bak 有 3 个文件，
    bm25_index/sub-kg5yy0t4 直接不存在 —— 该学科有 25 份"可学习"资料却完全检索不到。

    2026-10-08 起正常路径已改为「staging 构建 + 原子发布」（见 _build_index_with_rollback），
    不再产生空窗；本函数仍是**兜底**，覆盖历史遗留的 .bak，以及发布被硬杀时留下的
    .old / .prev。

    修复策略（只读判断 + 原子性恢复）：
    - 线上索引**缺失或为空** 且存在 .bak/.old/.prev → 用其恢复线上索引；
    - 线上索引非空：
        * .bak/.old 只是"构建成功/发布后、清理前"被中断的残留 → 删掉；
        * .prev 是**有意保留的"上一次有效版本"备份** → 保留，不删。
    """
    fixed = 0
    sidecars = ((".bak", False), (".old", False), (".prev", True))
    for rel, name in (("data/vector_store", "vector_store"), ("data/bm25_index", "bm25_index")):
        base = _resolve_path(rel)
        if not base.exists():
            continue
        for suffix, keep_when_live in sidecars:
            for backup in sorted(base.glob(f"*{suffix}")):
                live = base / backup.name[: -len(backup.suffix)]
                try:
                    live_files = _count_index_files(live)
                    backup_files = _count_index_files(backup)
                    if live_files == 0 and backup_files > 0:
                        if live.exists():
                            _remove_tree(live)
                        shutil.copytree(backup, live)
                        _remove_tree(backup)
                        fixed += 1
                        logger.warning(
                            f"[index-recovery] {name}: 线上索引为空，已从 {backup.name} "
                            f"恢复 {live.name}（{backup_files} 个文件）")
                        try:
                            _invalidate_subject_index(live.name)
                        except Exception:
                            pass
                    elif live_files > 0 and not keep_when_live:
                        # 线上索引完整：.bak/.old 是中断后的残留
                        _remove_tree(backup)
                except Exception as e:      # 恢复失败不能阻塞启动
                    logger.warning(
                        f"[index-recovery] {name}/{backup.name} 恢复失败: {e}")
    return fixed
