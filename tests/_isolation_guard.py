"""
数据隔离闸门（2026-09-27）

背景：本项目已两次因为"脚本没指定隔离目录"把测试数据写进正式库
（`data/extracted_knowledge.db` 的知识点、`data/learning_loop.db` 的评测题）。
"先跑脏了再删"不是验收方式——正确做法是**在写入之前拒绝运行**。

本模块提供两种闸门：

1) `require_isolated(script, purpose)` —— 给**直接写库的脚本**用（评测、造数据脚本等）。
   判定：必须显式设置 `EDU_DATA_DIR`，且解析后的目录不等于项目正式 `data/`。
   不满足则打印设置方法并 `exit(2)`，**不产生任何写入**。
   `read_only=True` 时允许未设置（用于只读清单脚本），但仍会打印实际目录。

2) `require_backend_isolated(base_url, purpose)` —— 给**通过 HTTP 打后端的脚本**用
   （E2E/验收脚本自己不写库，但后端会写）。判定后端 `/api/health` 的
   `data_scope.isolated` 必须为 true，否则 `exit(2)`。
   `read_only=True` 时降级为 WARN，供纯展示类脚本使用。

用法：

    from tests._isolation_guard import require_isolated
    require_isolated("tests/eval/run_eval.py", "教学效果评测（会建立评测账号与课程）")

    from tests._isolation_guard import require_backend_isolated
    require_backend_isolated(BASE, "教师端 E2E（会在后端写入账号/课程/作答）")
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


from tests._console import safe_console  # noqa: E402

safe_console()

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DATA_DIR = (ROOT / "data").resolve()

_SETUP_HINT = """\
请先指定隔离数据目录（不会影响正式库）：

  PowerShell:
    $env:EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_work_data"
    python {script}
  bash:
    EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_work_data" python {script}

首次使用可先创建该目录（脚本会自行建表）：
    python -c "from pathlib import Path; Path(r'E:/WORKBUDDY/Game/git_text_work_data').mkdir(parents=True, exist_ok=True)"

说明：脚本必须与后端使用**同一个** EDU_DATA_DIR，否则会读写到不同的库。
"""


class IsolationError(SystemExit):
    """隔离不满足时的退出（exit code 2，便于自动化断言）"""


def data_dir_state() -> dict:
    """返回当前进程的数据目录状态（不写任何东西）"""
    raw = (os.getenv("EDU_DATA_DIR") or "").strip()
    from config.path_config import get_data_dir
    resolved = Path(get_data_dir()).resolve()
    return {
        "env_set": bool(raw),
        "env_value": raw,
        "resolved": str(resolved),
        "dir_name": resolved.name,
        "is_production": resolved == PRODUCTION_DATA_DIR,
    }


def require_isolated(script: str, purpose: str, *, read_only: bool = False) -> dict:
    """
    写库脚本的隔离闸门

    Args:
        script: 脚本路径（仅用于提示信息）
        purpose: 这个脚本会写什么（打印给使用者看）
        read_only: True 表示该脚本不写库（只读清单类），未设置时只告警不退出

    Returns:
        数据目录状态字典（含 resolved 路径）
    """
    state = data_dir_state()
    print("=" * 64)
    print(f"[数据隔离] {script}")
    print(f"[用途] {purpose}")
    print(f"[数据目录] {state['resolved']}"
          f"{'（正式数据目录）' if state['is_production'] else ''}")
    print(f"[隔离模式] {'EDU_DATA_DIR 已显式设置' if state['env_set'] else '未设置 EDU_DATA_DIR'}")
    print("=" * 64)

    if state["is_production"] or not state["env_set"]:
        reason = ("解析到项目正式 data/ 目录" if state["is_production"]
                  else "未显式设置 EDU_DATA_DIR（默认会落到正式 data/）")
        head = ("[注意] 只读脚本：未隔离，仍会继续（本脚本不写库）" if read_only
                else "[拒绝] 拒绝执行：这个脚本会写数据，但当前没有隔离数据目录")
        print(f"{head}\n   原因：{reason}\n")
        if not read_only:
            print(_SETUP_HINT.format(script=script))
            raise IsolationError(2)
        print("（若要避免任何风险，建议同样设置 EDU_DATA_DIR）\n")
    return state


def require_backend_isolated(base_url: str, purpose: str, *, read_only: bool = False,
                             timeout: int = 8) -> dict:
    """
    通过 HTTP 打后端的脚本的隔离闸门

    Returns:
        后端的 data_scope 字典
    """
    url = base_url.rstrip("/") + "/api/health"
    print("=" * 64)
    print(f"[后端隔离检查] {url}")
    print(f"[用途] {purpose}")
    print("=" * 64)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
        scope = ((body or {}).get("data") or {}).get("data_scope") or {}
    except Exception as e:
        print(f"[拒绝] 无法读取后端健康检查（{e}）\n"
              f"   请先启动后端：EDU_DATA_DIR=<隔离目录> python -m uvicorn api.app:app "
              f"--host 127.0.0.1 --port 8000")
        raise IsolationError(2)

    print(f"[后端数据目录] {scope.get('dir_name')}"
          f"{'（正式数据目录）' if not scope.get('isolated') else '（隔离）'}")
    if not scope.get("isolated"):
        head = ("[注意] 只读脚本：后端未隔离，仍会继续（本脚本不写数据）" if read_only
                else "[拒绝] 拒绝执行：后端运行在**正式数据目录**上，本脚本会在其中写入账号/课程/作答")
        print(head)
        if not read_only:
            print("\n请用隔离目录启动后端后重试：\n"
                  '  $env:EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_work_data"\n'
                  "  python -m uvicorn api.app:app --host 127.0.0.1 --port 8000\n")
            raise IsolationError(2)
    print("=" * 64)
    return scope


__all__ = ["IsolationError", "require_isolated", "require_backend_isolated",
           "data_dir_state", "PRODUCTION_DATA_DIR"]
