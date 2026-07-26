#!/usr/bin/env python3
"""
智慧教育RAG系统 - 服务器启动脚本
使用uvicorn启动FastAPI应用
"""
import uvicorn
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    print("="*60)
    print("智慧教育RAG系统 - 启动中...")
    print("="*60)
    
    # 启动uvicorn服务器
    uvicorn.run(
        "api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
