#!/usr/bin/env python3
"""
智慧教育RAG系统 - 服务器启动脚本
使用uvicorn启动FastAPI应用
"""
import os
import sys

# 用户级 PYTHONPATH（本机是 D:\Lib\site-packages，pydantic 1.x）会在解释器
# 启动时插到 venv 前面。这里先从环境和 sys.path 拿掉，再加载 uvicorn，
# 否则知识库接口会在导入 pydantic v2 时 500。热重载子进程继承的是改过的环境。
_pythonpath = os.environ.pop("PYTHONPATH", "")
_shadows = {
    os.path.normcase(os.path.abspath(p))
    for p in _pythonpath.split(os.pathsep)
    if p
}
if _shadows:
    sys.path[:] = [
        p for p in sys.path
        if not p or os.path.normcase(os.path.abspath(p)) not in _shadows
    ]

import uvicorn

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
