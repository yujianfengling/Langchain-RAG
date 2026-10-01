"""
为真实模型 / 浏览器验收准备独立数据目录（阶段一补验 · 测试数据隔离）

用法：
    python tests/make_e2e_env.py [目标目录]

做的事：
1. 新建（或复用）一个独立数据目录，例如 E:\\WORKBUDDY\\Game\\git_text_e2e_data
2. 复制**课程公共资料**（vector_store / bm25_index / subjects / subjects.json / knowledge_graph）
   —— 这些是课程内容，复制不会带入任何个人学习记录
3. **不复制**任何个人数据：（auth.db / chat.db / learning.db / learning_loop.db /
   user_agent.db / extracted_knowledge.db 全部重新创建为空库）
4. 打印启动命令（后端加 EDU_DATA_DIR 环境变量）

这样验收账号、作答、错题、复习、计划都只写进这个独立目录，正式库保持不变。
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DEFAULT_TARGET = os.path.join(os.path.dirname(ROOT), "git_text_e2e_data")

# 课程公共资料（可复制）
SHARED = ("vector_store", "bm25_index", "subjects", "knowledge_graph")
SHARED_FILES = ("subjects.json",)
# 个人数据（不复制，由服务自行创建空库）
PRIVATE_DB_SUFFIX = (".db", ".db-wal", ".db-shm")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET
    src = os.path.join(ROOT, "data")
    os.makedirs(target, exist_ok=True)

    copied, skipped = [], []
    for name in SHARED:
        s = os.path.join(src, name)
        if not os.path.isdir(s):
            skipped.append(name)
            continue
        d = os.path.join(target, name)
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(s, d)
        copied.append(name)

    for name in SHARED_FILES:
        s = os.path.join(src, name)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(target, name))
            copied.append(name)
        else:
            skipped.append(name)

    private = []
    if os.path.isdir(src):
        for name in os.listdir(src):
            if name.endswith(PRIVATE_DB_SUFFIX):
                private.append(name)

    print("=" * 68)
    print(f"独立验收数据目录: {target}")
    print(f"已复制课程公共资料: {', '.join(copied) or '（无）'}")
    if skipped:
        print(f"源目录中不存在（跳过）: {', '.join(skipped)}")
    print(f"未复制个人数据（新库为空）: {', '.join(private) or '（无）'}")
    print("-" * 68)
    print("启动后端（PowerShell）：")
    print(f'  $env:EDU_DATA_DIR="{target}"')
    print("  python -m uvicorn api.app:app --host 127.0.0.1 --port 8000")
    print("")
    print("启动后端（bash）：")
    print(f'  EDU_DATA_DIR="{target}" python -m uvicorn api.app:app --host 127.0.0.1 --port 8000')
    print("=" * 68)
    print("注意：向量库为本地文件，复制后即可直接检索；若学科资料有更新，")
    print("      请在本目录内重新构建知识库，不要写回正式目录。")


if __name__ == "__main__":
    main()
