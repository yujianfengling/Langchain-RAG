"""
隔离测试夹具（阶段一补验）

问题：此前部分测试依赖**本机正式库里的预存数据**（例如 subjects.json 里有"数据结构"
这个学科、并且它已经建好知识库），一旦换了机器 / 清理了 data/ 就必然失败；
测试还会把数据写进正式库（data/*.db），污染真实学习记录。

本夹具把测试完全隔离：
- 所有数据目录/数据库都指向本用例自己的临时目录（用完即删）
- 需要的"运行时数据"（学科条目 + 已就绪的知识库）在临时目录里**真实创建**，
  走的是与生产完全相同的代码路径（subject_service / knowledge_service / rag 的
  真实实现），不做函数级 patch 绕过校验
- 因此测试既不依赖、也不修改正式 data/

用法：

    from tests.fixtures_isolated import IsolatedDataTestCase

    class MyTest(IsolatedDataTestCase):
        SUBJECTS = ("数据结构",)          # 自动注册为"学科存在 + 知识库已就绪"

        def test_x(self):
            ...  # 可正常通过 require_kb_ready("数据结构")
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def start_isolated_env(subjects=("数据结构",)):
    """
    启动隔离环境：临时 data/ 目录 + 临时 SQLite 库 + 真实注册的学科与"已就绪"知识库。

    Returns:
        (tmpdir, data_dir, patchers)
    """
    from pathlib import Path

    from services import (
        auth_service, conversation_service, learning_data,
        learning_service, mastery_service, subject_service, user_profiler,
    )
    from services import knowledge_service
    from services import agent_runtime, learning_memory, plan_adjustment
    from services import knowledge_expander
    from services import teaching_data
    from rag import rag_service

    tmpdir = tempfile.mkdtemp(prefix="isolated_")
    data_dir = os.path.join(tmpdir, "data")
    os.makedirs(data_dir, exist_ok=True)

    # 1) 学科清单（真实写入临时 subjects.json）
    subjects_file = os.path.join(data_dir, "subjects.json")
    with open(subjects_file, "w", encoding="utf-8") as f:
        json.dump([{"id": s, "name": s, "description": None} for s in subjects],
                  f, ensure_ascii=False)

    # 2) 知识库目录 + "已就绪"标记（真实创建目录，让生产代码判定为 ready）
    vs_base = os.path.join(data_dir, "vector_store")
    for s in subjects:
        d = os.path.join(vs_base, rag_service._safe_dir_name(s))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "faiss_index"), "wb") as f:
            f.write(b"isolated-test-index")
    os.makedirs(os.path.join(data_dir, "subjects"), exist_ok=True)

    # 3) 把所有数据路径指向临时目录。
    #    模块级常量在 import 时就算好了，所以这里既设置 EDU_DATA_DIR（供运行期
    #    调用 get_data_dir() 的代码），也逐个 patch 已缓存路径的模块常量。
    patchers = [
        mock.patch.dict(os.environ, {"EDU_DATA_DIR": data_dir}),
        mock.patch("config.path_config.get_data_dir", return_value=Path(data_dir)),
        # 运行期才解析 data/ 路径的模块（名字在其命名空间里）
        mock.patch.object(knowledge_service, "get_data_dir", return_value=Path(data_dir)),
        mock.patch.object(knowledge_service, "PROJECT_ROOT", Path(tmpdir)),
        mock.patch.object(subject_service, "SUBJECTS_FILE", Path(subjects_file)),
        mock.patch.object(rag_service, "_VECTOR_STORE_BASE", vs_base),
        mock.patch.object(rag_service, "_SUBJECTS_DATA_BASE",
                          os.path.join(data_dir, "subjects")),
        mock.patch.object(learning_data, "_DATA_DIR", data_dir),
        mock.patch.object(learning_data, "DB_PATH",
                          os.path.join(data_dir, "learning_loop.db")),
        mock.patch.object(learning_data, "BACKUP_DIR",
                          os.path.join(data_dir, "backups")),
        mock.patch.object(learning_service, "DB_PATH",
                          os.path.join(data_dir, "learning.db")),
        mock.patch.object(learning_service, "_DATA_DIR", data_dir),
        mock.patch.object(conversation_service, "DB_PATH",
                          os.path.join(data_dir, "chat.db")),
        mock.patch.object(conversation_service, "_DATA_DIR", data_dir),
        mock.patch.object(auth_service, "DB_PATH",
                          os.path.join(data_dir, "auth.db")),
        mock.patch.object(auth_service, "_DATA_DIR", data_dir),
        mock.patch.object(mastery_service, "_AGENT_DB",
                          os.path.join(data_dir, "user_agent.db")),
        mock.patch.object(user_profiler, "DB_PATH",
                          os.path.join(data_dir, "user_agent.db")),
        mock.patch.object(user_profiler, "_DATA_DIR", data_dir),
        # 阶段二新增模块（各自缓存了数据目录，必须一并隔离）
        mock.patch.object(plan_adjustment, "DB_PATH",
                          os.path.join(data_dir, "learning.db")),
        mock.patch.object(plan_adjustment, "_DATA_DIR", data_dir),
        mock.patch.object(agent_runtime, "DB_PATH",
                          os.path.join(data_dir, "learning.db")),
        mock.patch.object(agent_runtime, "_DATA_DIR", data_dir),
        mock.patch.object(learning_memory, "DB_PATH",
                          os.path.join(data_dir, "user_agent.db")),
        mock.patch.object(learning_memory, "_DATA_DIR", data_dir),
        # 知识提取库独立成库（extracted_knowledge.db），同样必须隔离，
        # 否则"知识提取"相关用例会把测试知识点写进正式库
        mock.patch.object(knowledge_expander, "EXTRACTED_KB_PATH",
                          os.path.join(data_dir, "extracted_knowledge.db")),
        mock.patch.object(knowledge_expander, "_DATA_DIR", data_dir),
        # 教师端库（teaching.db）：同样必须隔离，否则建课/作业/学情会写进正式库
        mock.patch.object(teaching_data, "DB_PATH",
                          os.path.join(data_dir, "teaching.db")),
        mock.patch.object(teaching_data, "_DATA_DIR", data_dir),
    ]
    for p in patchers:
        p.start()

    # 4) 按生产逻辑建表（隔离库需要与生产一致的结构）
    learning_data._init_db()
    learning_service._init_db()
    conversation_service._init_db()
    auth_service._init_db()
    user_profiler._init_db()
    teaching_data._init_db()
    try:
        knowledge_expander._init_extracted_db()
    except Exception:
        pass  # 该库按需创建，初始化失败不影响其它用例

    return tmpdir, data_dir, patchers


def stop_isolated_env(tmpdir, patchers):
    """关闭隔离环境并删除临时目录"""
    for p in patchers:
        try:
            p.stop()
        except RuntimeError:
            pass
    shutil.rmtree(tmpdir, ignore_errors=True)


class IsolatedDataTestCase(unittest.TestCase):
    """把整套 data/ 与所有 SQLite 库隔离到临时目录的测试基类"""

    # 需要在隔离环境里注册的学科（自动创建"已就绪"的知识库标记）
    SUBJECTS = ("数据结构",)

    def setUp(self):
        self._tmpdir, self.data_dir, self._patchers = start_isolated_env(self.SUBJECTS)
        self.tmpdir = self._tmpdir

    def tearDown(self):
        stop_isolated_env(self._tmpdir, self._patchers)


def register_and_login(username: str) -> str:
    """在隔离库里注册一个用户并返回令牌（供接口级测试使用）"""
    from services import auth_service
    user = auth_service.register_user(username, "test-pass-123")
    return auth_service.create_session(user["id"])["token"]


__all__ = [
    "IsolatedDataTestCase",
    "register_and_login",
    "start_isolated_env",
    "stop_isolated_env",
]
