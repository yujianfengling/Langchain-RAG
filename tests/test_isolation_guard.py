"""
数据隔离闸门的针对性验证（2026-09-27）

要求：**脚本没有显式指定隔离数据目录时，必须在写入之前拒绝运行**，
而不是"先跑、写脏了再删"。

这里用子进程真跑一遍来证明：
1. 直接调用闸门：未设置 EDU_DATA_DIR / 指向正式 data → 非零退出且给出设置方法；
   显式隔离目录 → 正常通过。
2. 真实脚本：在未隔离的环境下运行 `tests/eval/run_eval.py` 等 → 退出码 2，
   且**正式库逐库哈希与行数完全不变**（证明确实没写进去）。
3. pytest 会话：未设置时自动隔离；若被指向正式 data/ → 整个会话拒绝运行。
4. 后端闸门：健康检查返回正式目录时，HTTP 类脚本拒绝执行。

说明：本文件自身也在隔离会话中运行（见 `tests/conftest.py`）。
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PRODUCTION_DATA = ROOT / "data"
GUARD = ROOT / "tests" / "_isolation_guard.py"


def _snapshot_production() -> dict:
    """正式库快照：文件名 → (内容哈希, 总行数)。只读，不写任何东西。"""
    import sqlite3
    out = {}
    for db in sorted(PRODUCTION_DATA.glob("*.db")):
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        rows = 0
        try:
            for (t,) in con.execute("select name from sqlite_master where type='table'"):
                try:
                    rows += con.execute(f'select count(*) from "{t}"').fetchone()[0]
                except Exception:
                    pass
        finally:
            con.close()
        out[db.name] = (hashlib.sha256(db.read_bytes()).hexdigest()[:16], rows)
    return out


def _clean_env(**overrides) -> dict:
    """构造一个不含 EDU_DATA_DIR 的环境（除非显式传入）"""
    env = dict(os.environ)
    env.pop("EDU_DATA_DIR", None)
    env.update({k: str(v) for k, v in overrides.items()})
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class TestGuardDirect(unittest.TestCase):
    """直接调用闸门函数的行为"""

    def _run_guard(self, code: str, env: dict) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              cwd=str(ROOT), env=env, timeout=180)

    def test_refuses_without_env(self):
        code = (
            "import sys; sys.path.insert(0, r'%s')\n"
            "from tests._isolation_guard import require_isolated\n"
            "require_isolated('demo_script.py', '会写数据的脚本')\n"
            "print('SHOULD_NOT_REACH_HERE')\n" % ROOT
        )
        r = self._run_guard(code, _clean_env())
        self.assertEqual(r.returncode, 2, f"未隔离时应拒绝运行：{r.stdout}{r.stderr}")
        self.assertIn("拒绝执行", r.stdout)
        self.assertIn("EDU_DATA_DIR", r.stdout, "应提示设置方法")
        self.assertNotIn("SHOULD_NOT_REACH_HERE", r.stdout, "不应继续执行到写数据")

    def test_refuses_when_pointing_at_production(self):
        code = (
            "import sys; sys.path.insert(0, r'%s')\n"
            "from tests._isolation_guard import require_isolated\n"
            "require_isolated('demo_script.py', '会写数据的脚本')\n"
            "print('SHOULD_NOT_REACH_HERE')\n" % ROOT
        )
        r = self._run_guard(code, _clean_env(EDU_DATA_DIR=PRODUCTION_DATA))
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("拒绝执行", r.stdout)
        self.assertNotIn("SHOULD_NOT_REACH_HERE", r.stdout)

    def test_allows_explicit_isolated_dir(self):
        with tempfile.TemporaryDirectory(prefix="guard_ok_") as tmp:
            code = (
                "import sys; sys.path.insert(0, r'%s')\n"
                "from tests._isolation_guard import require_isolated\n"
                "state = require_isolated('demo_script.py', '会写数据的脚本')\n"
                "print('OK', state['dir_name'])\n" % ROOT
            )
            r = self._run_guard(code, _clean_env(EDU_DATA_DIR=tmp))
            self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")
            self.assertIn("OK", r.stdout)
            self.assertIn(Path(tmp).name, r.stdout)

    def test_refusal_survives_gbk_console(self):
        """
        拒绝提示必须能在**默认 GBK 控制台**下打出来。

        实测踩过：Windows 默认 cp936 下打印 ❌/⚠️ 这类字符会抛
        UnicodeEncodeError——"拒绝运行"这一步自己先崩了，退出码也不是 2。
        这里故意不设置 PYTHONIOENCODING，模拟用户直接在 PowerShell 里跑。
        """
        env = _clean_env()
        env.pop("PYTHONIOENCODING", None)
        code = (
            "import sys; sys.path.insert(0, r'%s')\n"
            "from tests._isolation_guard import require_isolated\n"
            "require_isolated('demo_script.py', '会写数据的脚本')\n"
            "print('SHOULD_NOT_REACH_HERE')\n" % ROOT
        )
        r = self._run_guard(code, env)
        self.assertEqual(r.returncode, 2, f"应干净退出 2，实际 {r.returncode}：{r.stdout}{r.stderr}")
        self.assertNotIn("UnicodeEncodeError", r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertIn("EDU_DATA_DIR", r.stdout, "拒绝时必须告诉用户怎么设置隔离目录")

    def test_read_only_script_warns_but_continues(self):
        code = (
            "import sys; sys.path.insert(0, r'%s')\n"
            "from tests._isolation_guard import require_isolated\n"
            "require_isolated('readonly.py', '只读清单', read_only=True)\n"
            "print('CONTINUED')\n" % ROOT
        )
        r = self._run_guard(code, _clean_env())
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("CONTINUED", r.stdout)
        self.assertIn("只读脚本", r.stdout)


class TestRealScriptsRefuse(unittest.TestCase):
    """真跑脚本：未隔离时必须拒绝，并且正式库一个字节都没变"""

    WRITING_SCRIPTS = [
        ("tests/eval/run_eval.py", ["--base", "http://127.0.0.1:59999"]),
        ("tests/speech_bakeoff.py", []),
    ]

    def test_writing_scripts_refuse_and_leave_production_untouched(self):
        before = _snapshot_production()
        for script, extra in self.WRITING_SCRIPTS:
            with self.subTest(script=script):
                r = subprocess.run([sys.executable, script, *extra],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", cwd=str(ROOT), env=_clean_env(),
                                   timeout=300)
                self.assertNotEqual(r.returncode, 0,
                                    f"{script} 未隔离却成功退出：{r.stdout[-500:]}")
                self.assertIn("EDU_DATA_DIR", r.stdout + r.stderr,
                              f"{script} 应提示如何设置隔离目录")
        after = _snapshot_production()
        self.assertEqual(before, after,
                         "未隔离运行脚本后正式库发生了变化（隔离闸门失效）")

    def test_first_run_check_does_not_write_target_dir(self):
        """首次启动自检：在未隔离环境下也只读，不建表、不写业务数据"""
        before = _snapshot_production()
        before_extra = sorted(p.name for p in PRODUCTION_DATA.iterdir())
        r = subprocess.run([sys.executable, "tests/check_first_run.py"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(ROOT), env=_clean_env(), timeout=300)
        after = _snapshot_production()
        self.assertEqual(before, after,
                         "自检修改了正式库（应只读，建库检查在临时目录做）")
        self.assertEqual(before_extra, sorted(p.name for p in PRODUCTION_DATA.iterdir()),
                         "自检在正式数据目录里新增/删除了文件")
        self.assertIn("首次启动自检", r.stdout)


class TestPytestSessionIsolation(unittest.TestCase):
    """pytest 会话级强制隔离"""

    def test_session_is_isolated(self):
        env = (os.getenv("EDU_DATA_DIR") or "").strip()
        self.assertTrue(env, "pytest 会话未设置 EDU_DATA_DIR（conftest 应自动隔离）")
        from config.path_config import get_data_dir
        self.assertNotEqual(Path(get_data_dir()).resolve(), PRODUCTION_DATA.resolve(),
                            "pytest 会话落在了正式数据目录上")

    def test_conftest_refuses_production_dir(self):
        """
        把 EDU_DATA_DIR 指向正式 data/ → 会话必须拒绝启动

        注意：pytest 的 conftest 只对**其所在目录子树**生效，
        所以探针文件必须放在 tests/ 内（用临时文件名，跑完即删）。
        """
        probe = ROOT / "tests" / "_tmp_conftest_probe_test.py"
        probe.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/_tmp_conftest_probe_test.py", "-q",
                 "-p", "no:cacheprovider"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(ROOT), env=_clean_env(EDU_DATA_DIR=PRODUCTION_DATA), timeout=300)
            self.assertNotEqual(r.returncode, 0, "指向正式目录时不应跑测试")
            self.assertIn("拒绝运行测试", r.stdout + r.stderr)
        finally:
            probe.unlink(missing_ok=True)


class TestBackendGuard(unittest.TestCase):
    """HTTP 类脚本的后端隔离闸门（用假后端验证判定逻辑）"""

    def test_refuses_when_backend_not_isolated(self):
        import json as _json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = _json.dumps({"success": True, "data": {
                    "data_scope": {"isolated": False, "dir_name": "data"}}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):  # 静音
                pass

        srv = HTTPServer(("127.0.0.1", 0), Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            code = (
                "import sys; sys.path.insert(0, r'%s')\n"
                "from tests._isolation_guard import require_backend_isolated\n"
                "require_backend_isolated('http://127.0.0.1:%d', 'E2E 脚本')\n"
                "print('SHOULD_NOT_REACH_HERE')\n" % (ROOT, port)
            )
            r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               cwd=str(ROOT), env=_clean_env(), timeout=120)
            self.assertEqual(r.returncode, 2, f"{r.stdout}{r.stderr}")
            self.assertIn("正式数据目录", r.stdout)
            self.assertNotIn("SHOULD_NOT_REACH_HERE", r.stdout)
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
