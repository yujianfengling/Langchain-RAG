"""
V2.3.1 Phase 2.4.4: Vision Reliability Hardening 验证脚本
验证：第一次运行调用Vision API，第二次运行缓存命中
"""
import sys
import time
import logging
from pathlib import Path

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))


def run_pdf_build(run_number: int) -> dict:
    """
    运行PDF知识库构建
    
    Args:
        run_number: 运行次数（1或2）
    
    Returns:
        运行结果统计
    """
    logger.info(f"{'='*60}")
    logger.info(f"第{run_number}次运行：开始构建知识库")
    logger.info(f"{'='*60}")
    
    start_time = time.time()
    
    # 使用09气体动理论知识点总结.pdf
    pdf_path = "data/subjects/大学物理/09气体动理论知识点总结.pdf"
    
    if not Path(pdf_path).exists():
        logger.error(f"PDF文件不存在: {pdf_path}")
        return {"success": False, "error": "PDF文件不存在"}
    
    try:
        # 导入必要的模块
        from rag.loaders.pdf_loader import PdfLoader
        from rag.embedding import get_embedding_model
        from rag.vector_store import create_vector_store
        from rag.hybrid_retriever import build_index
        
        # 1. 加载PDF
        logger.info(f"[Run {run_number}] 加载PDF: {pdf_path}")
        loader = PdfLoader()
        load_result = loader.load(pdf_path)
        
        if not load_result.success:
            return {"success": False, "error": f"PDF加载失败: {load_result.error}"}
        
        documents = load_result.documents
        logger.info(f"[Run {run_number}] 加载完成: {len(documents)}个文档")
        
        # 统计文档类型
        doc_counts = {}
        vision_stats = {"success": 0, "cached": 0, "failed": 0, "skipped": 0, "not_applicable": 0}
        vision_api_called = False
        
        for doc in documents:
            doc_type = doc.metadata.get("type", "unknown")
            doc_counts[doc_type] = doc_counts.get(doc_type, 0) + 1
            
            # 只统计图片文档的vision_status
            if doc_type == "image":
                vision_status = doc.metadata.get("vision_status", "unknown")
                if vision_status in vision_stats:
                    vision_stats[vision_status] += 1
                else:
                    vision_stats["success"] += 1  # 默认归类为success
                
                if vision_status in ("success", "failed"):
                    vision_api_called = True
            else:
                vision_stats["not_applicable"] += 1
        
        logger.info(f"[Run {run_number}] 文档类型统计: {doc_counts}")
        logger.info(f"[Run {run_number}] Vision状态统计: {vision_stats}")
        logger.info(f"[Run {run_number}] Vision API实际调用: {vision_api_called}")
        
        # 打印image Document详情
        for doc in documents:
            if doc.metadata.get("type") == "image":
                logger.info(f"[Run {run_number}] Image Document详情:")
                logger.info(f"  - vision_status: {doc.metadata.get('vision_status')}")
                logger.info(f"  - vision_retries: {doc.metadata.get('vision_retries')}")
                logger.info(f"  - model_used: {doc.metadata.get('model_used')}")
                logger.info(f"  - page_content长度: {len(doc.page_content)}")
                logger.info(f"  - page_content前100字: {doc.page_content[:100]}")
        
        # 2. 创建向量存储
        logger.info(f"[Run {run_number}] 创建向量存储...")
        embedding = get_embedding_model()
        
        # 使用临时目录
        temp_index_dir = f"data/temp_phase244_run{run_number}"
        vector_store = create_vector_store(
            docs=documents,
            embeddings=embedding,
            collection_name=f"phase244_run{run_number}"
        )
        
        # 3. 构建混合索引
        logger.info(f"[Run {run_number}] 构建混合索引...")
        build_index(
            subject_id=f"phase244_run{run_number}",
            documents=documents,
            embeddings=embedding
        )
        
        total_time = time.time() - start_time
        
        result = {
            "success": True,
            "run_number": run_number,
            "total_documents": len(documents),
            "document_counts": doc_counts,
            "vision_stats": vision_stats,
            "vision_api_called": vision_api_called,
            "total_time": total_time
        }
        
        logger.info(f"[Run {run_number}] 构建完成，耗时: {total_time:.2f}秒")
        
        return result
        
    except Exception as e:
        logger.error(f"[Run {run_number}] 构建失败: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


def test_retrieval(run_number: int) -> dict:
    """
    测试检索结果
    
    Args:
        run_number: 运行次数
    
    Returns:
        检索结果
    """
    logger.info(f"[Run {run_number}] 测试检索...")
    
    try:
        from rag.hybrid_retriever import retrieve
        
        # 测试查询
        test_queries = [
            "正态分布曲线",
            "钟形曲线",
            "统计学分布"
        ]
        
        results = {}
        
        for query in test_queries:
            logger.info(f"[Run {run_number}] 查询: {query}")
            
            # 混合检索 - 返回的是dict列表
            retrieved = retrieve(
                query=query,
                subject_id=f"phase244_run{run_number}",
                top_k=20
            )
            
            # 检查image Document是否出现（retrieve返回dict，有metadata字段）
            image_found = False
            image_rank = -1
            
            for i, item in enumerate(retrieved):
                metadata = item.get("metadata", {}) if isinstance(item, dict) else getattr(item, 'metadata', {})
                if metadata.get("type") == "image":
                    image_found = True
                    image_rank = i + 1
                    logger.info(f"[Run {run_number}] Image Document出现在排名 {image_rank}")
                    logger.info(f"  - vision_status: {metadata.get('vision_status')}")
                    logger.info(f"  - score: {item.get('score', 'N/A')}")
                    break
            
            results[query] = {
                "image_found": image_found,
                "image_rank": image_rank,
                "total_results": len(retrieved)
            }
            
            logger.info(f"[Run {run_number}] 结果: image_found={image_found}, image_rank={image_rank}")
        
        return results
        
    except Exception as e:
        logger.error(f"[Run {run_number}] 检索测试失败: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def main():
    """主函数"""
    logger.info("V2.3.1 Phase 2.4.4: Vision Reliability Hardening 验证")
    logger.info("="*60)
    
    # 第一次运行
    logger.info("\n" + "="*60)
    logger.info("第一次运行：应调用Vision API")
    logger.info("="*60)
    
    result1 = run_pdf_build(1)
    
    if not result1.get("success"):
        logger.error(f"第一次运行失败: {result1.get('error')}")
        return
    
    # 测试第一次检索
    retrieval1 = test_retrieval(1)
    
    # 第二次运行
    logger.info("\n" + "="*60)
    logger.info("第二次运行：应使用Vision缓存")
    logger.info("="*60)
    
    result2 = run_pdf_build(2)
    
    if not result2.get("success"):
        logger.error(f"第二次运行失败: {result2.get('error')}")
        return
    
    # 测试第二次检索
    retrieval2 = test_retrieval(2)
    
    # 比较结果
    logger.info("\n" + "="*60)
    logger.info("验证结果对比")
    logger.info("="*60)
    
    logger.info("\n第一次运行:")
    logger.info(f"  - 文档总数: {result1.get('total_documents')}")
    logger.info(f"  - 文档类型: {result1.get('document_counts')}")
    logger.info(f"  - Vision状态: {result1.get('vision_stats')}")
    logger.info(f"  - 耗时: {result1.get('total_time', 0):.2f}秒")
    
    logger.info("\n第二次运行:")
    logger.info(f"  - 文档总数: {result2.get('total_documents')}")
    logger.info(f"  - 文档类型: {result2.get('document_counts')}")
    logger.info(f"  - Vision状态: {result2.get('vision_stats')}")
    logger.info(f"  - 耗时: {result2.get('total_time', 0):.2f}秒")
    
    # 验证关键指标
    logger.info("\n" + "="*60)
    logger.info("关键验证指标")
    logger.info("="*60)
    
    # 检查第一次运行是否有Vision API调用
    vision_api_called_run1 = result1.get("vision_api_called", False)
    logger.info(f"1. 第一次运行Vision API调用: {'是' if vision_api_called_run1 else '否'}")
    
    # 检查第二次运行是否全部缓存命中
    vision_stats2 = result2.get("vision_stats", {})
    cached_count = vision_stats2.get("cached", 0)
    image_count = result1.get("document_counts", {}).get("image", 0)
    all_cached = cached_count >= image_count and image_count > 0
    logger.info(f"2. 第二次运行全部缓存命中: {'是' if all_cached else '否'} (cached={cached_count}, images={image_count})")
    
    # 检查两次文档数量是否一致
    docs_match = result1.get("total_documents") == result2.get("total_documents")
    logger.info(f"3. 两次文档数量一致: {'是' if docs_match else '否'}")
    
    # 检查检索结果
    logger.info("\n检索结果:")
    for query in retrieval1:
        if query in retrieval2:
            r1 = retrieval1[query]
            r2 = retrieval2[query]
            logger.info(f"  查询: {query}")
            logger.info(f"    - 第一次: image_found={r1.get('image_found')}, rank={r1.get('image_rank')}")
            logger.info(f"    - 第二次: image_found={r2.get('image_found')}, rank={r2.get('image_rank')}")
    
    # 最终结论
    logger.info("\n" + "="*60)
    logger.info("验证结论")
    logger.info("="*60)
    
    if vision_api_called_run1 and all_cached and docs_match:
        logger.info("✅ 验证通过：Vision缓存机制工作正常")
        logger.info("   - 第一次运行调用了Vision API")
        logger.info("   - 第二次运行全部缓存命中")
        logger.info("   - 两次文档数量一致")
    else:
        logger.warning("⚠️ 验证发现问题：")
        if not vision_api_called_run1:
            logger.warning("   - 第一次运行未调用Vision API（可能是缓存已存在）")
        if not all_cached:
            logger.warning("   - 第二次运行未全部缓存命中")
        if not docs_match:
            logger.warning("   - 两次文档数量不一致")


if __name__ == "__main__":
    main()
