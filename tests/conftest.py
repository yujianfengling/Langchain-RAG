"""
pytest 会话级数据隔离（2026-09-27）

问题：多数单元测试并不显式指定数据目录，一旦某个用例漏了隔离 fixture，
就会把数据写进项目正式 `data/`（本项目已经发生过两次：extracted_knowledge.db、
learning_loop.db）。人工事后清理不是可靠做法。

做法：**会话开始时就强制隔离**——
- 若未设置 `EDU_DATA_DIR`，自动指向 `git_text_test_data/`（仓库同级，已在 .gitignore 中），
  并在会话开始时打印实际目录，绝不落到正式 `data/`；
- 若设置了 `EDU_DATA_DIR` 且指向正式 `data/`，直接**拒绝整个测试会话**（退出码 2）。
- 单个测试仍可用 `IsolatedDataTestCase`（临时目录）做更强的隔离，两者不冲突。

这样即使某个用例忘了隔离，也不可能写脏正式库。
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PRODUCTION_DATA_DIR = (ROOT / "data").resolve()
DEFAULT_TEST_DIR = (ROOT.parent / "git_text_test_data").resolve()

_BANNER = "=" * 64


def _configure_isolation() -> str:
    raw = (os.getenv("EDU_DATA_DIR") or "").strip()
    if raw:
        resolved = Path(raw).expanduser().resolve()
        if resolved == PRODUCTION_DATA_DIR:
            print(f"{_BANNER}\n"
                  f"[拒绝] 拒绝运行测试：EDU_DATA_DIR 指向项目正式数据目录：{resolved}\n"
                  f"   测试会写入账号/作答/题库等数据，不能落在正式库上。\n"
                  f"   请改用独立目录，例如：\n"
                  f'     $env:EDU_DATA_DIR="{DEFAULT_TEST_DIR}"\n'
                  f"   （或直接清空该变量，pytest 会自动使用上面的默认测试目录）\n"
                  f"{_BANNER}")
            raise SystemExit(2)
        return str(resolved)
    # 未设置：自动落到专用测试目录（绝不使用正式 data/）
    DEFAULT_TEST_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["EDU_DATA_DIR"] = str(DEFAULT_TEST_DIR)
    return str(DEFAULT_TEST_DIR)


def pytest_configure(config):
    """会话开始前确定数据目录并显式打印（便于排查"数据写到哪去了"）"""
    active = _configure_isolation()

    # xdist 并行执行时，给每个 worker 分配**独立**的数据目录（2026-10-02）。
    #
    # 不这么做的话所有 worker 共用同一个 git_text_test_data：并发读写同一批
    # SQLite 库会让两类用例随机失败——跑真实子进程的（test_isolation_guard）
    # 和时序敏感的（test_runtime_responsiveness 断言"长任务期间健康检查仍响应"）。
    # 实测：同一批测试并行跑三次，失败的用例互不相同；串行则全绿。
    # 这类"偶尔红一下"最消耗人：分不清是回归还是环境。
    #
    # 只在"自动落到默认测试目录"时才分 worker：用户显式指定的目录可能是
    # 刻意要的（例如真实模型验收统一数据），不擅自改。
    worker = getattr(config, "workerinput", None)
    if worker and Path(active).resolve() == DEFAULT_TEST_DIR.resolve():
        worker_id = str(worker.get("workerid") or "unknown")
        worker_dir = DEFAULT_TEST_DIR / f"worker-{worker_id}"
        worker_dir.mkdir(parents=True, exist_ok=True)
        os.environ["EDU_DATA_DIR"] = str(worker_dir)
        active = str(worker_dir)
        print(f"[pytest 数据隔离] xdist {worker_id} 使用独立目录：{worker_dir}")

    # 视觉增强的"过小图片"过滤在测试中关闭：
    # 测试用的是合成小图（远小于 8KB 的生产阈值），若沿用默认阈值会被跳过，
    # 导致识图分支完全不可测。生产默认值仍为 8KB。
    os.environ.setdefault("VISION_MIN_IMAGE_BYTES", "0")

    # 外部联网在测试会话里**默认关闭**（2026-10-02）。
    #
    # 背景：知识库证据不足时出题会降级到"联网搜索"，而真实请求会
    #   1) 让测试变慢、在有网/无网环境下结果不一致；
    #   2) 消耗真实 Tavily 额度；
    #   3) 更隐蔽的是——用 mock.patch("...requests.post") 的测试会连它一起截住，
    #      拿到假响应后静默走错分支（test_default_model_switch 就这样失败过）。
    # 需要验证联网路径的用例，自己 mock services.web_search_service 即可。
    # 直接赋值（而非 setdefault）：.env 里可能是 true，而环境变量优先级更高。
    os.environ["WEB_SEARCH_ENABLED"] = "false"

    from config.path_config import get_data_dir
    resolved = Path(get_data_dir()).resolve()
    assert resolved != PRODUCTION_DATA_DIR, "隔离失败：仍指向正式数据目录"
    print(f"\n{_BANNER}\n"
          f"[pytest 数据隔离] 本会话数据目录：{resolved}\n"
          f"[pytest 数据隔离] 正式数据目录：{PRODUCTION_DATA_DIR}（本会话不会写入）\n"
          f"{_BANNER}\n")
    config._edu_isolated_dir = str(resolved)


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """
    保证每个用例开始前存在"当前事件循环"。

    背景（实测）：本项目有若干测试用 `asyncio.get_event_loop()` 跑 httpx.AsyncClient，
    而另一些测试调用 `asyncio.run(...)`；Python 3.11 里 `asyncio.run()` 结束后会把
    当前事件循环置空，于是**同一个 pytest 会话里，先跑 asyncio.run 的文件会让后跑的
    文件连环报错**：`RuntimeError: There is no current event loop in thread 'MainThread'`。
    单个文件跑都通过，一起跑就大面积失败——这会让人误判为"功能坏了"。

    这里在每个用例前补齐事件循环；用例自己调 asyncio.run() 仍然照常工作。
    """
    try:
        asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    yield
