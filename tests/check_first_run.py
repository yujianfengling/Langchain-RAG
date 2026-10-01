"""
首次启动自检（2026-09-27）

目的：新成员按文档启动前，先跑一次这个脚本，明确知道"缺什么、怎么补"，
而不是启动后看到一堆报错。

用法：
    python tests/check_first_run.py            # 只自检（不启动服务）
    python tests/check_first_run.py --url http://127.0.0.1:8000   # 顺带检查已启动的后端

检查项：
  1. Python 版本与关键依赖
  2. 数据目录（EDU_DATA_DIR 或 data/）可写、库能否初始化
  3. 文本模型密钥是否已配置（不打印密钥内容）
  4. Embedding 本地缓存是否就绪（没有会首次联网下载）
  5. 历史学科资料与知识库状态
  6. 本地语音栈（可选）：模型目录、语音服务健康
  7. 已启动后端的健康检查与能力（可选）

退出码：0 = 必需项齐全；1 = 有必需项缺失（会打印修复建议）。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 控制台编码兜底：Windows 默认 GBK，直接打印 emoji 会让脚本在 [1/7] 就崩掉。
# 必须在任何输出之前调用（这也是"新成员跑的第一条命令"）。
from tests._console import safe_console  # noqa: E402

safe_console()

OK = "[OK]"
WARN = "[注意] "
BAD = "[缺失]"

results = []


def check(name: str, ok: bool, detail: str = "", fix: str = "", required: bool = True):
    results.append({"name": name, "ok": ok, "detail": detail, "fix": fix,
                    "required": required})
    flag = OK if ok else (BAD if required else WARN)
    print(f"  {flag} {name}" + (f" — {detail}" if detail else ""))
    if not ok and fix:
        print(f"      ↳ 修复：{fix}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=None, help="已启动后端的地址（可选）")
    args = parser.parse_args()

    print("=" * 64)
    print("智慧教育 RAG 系统 · 首次启动自检")
    print("=" * 64)
    print("说明：本自检**不会**在目标数据目录里建表或写业务数据。")
    print("      「能否初始化数据库」这一项在系统临时目录里做，不碰正式库。")

    # ---------- 1. 运行环境 ----------
    print("\n[1/7] 运行环境")
    py = sys.version_info
    check("Python 版本 ≥ 3.10", py >= (3, 10), f"当前 {py.major}.{py.minor}.{py.micro}",
          "安装 Python 3.10+ 并重建虚拟环境")
    missing = []
    for mod in ("fastapi", "uvicorn", "langchain_core", "pydantic", "faiss", "rank_bm25"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    check("关键依赖已安装", not missing, "缺失：" + ", ".join(missing) if missing else "faiss / fastapi 等可导入",
          "pip install -r requirements.txt")

    # ---------- 2. 数据目录 ----------
    print("\n[2/7] 数据目录与数据库")
    from config.path_config import get_data_dir
    data_dir = get_data_dir()
    isolated = bool((os.getenv("EDU_DATA_DIR") or "").strip())
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except Exception as e:
        writable = False
        print(f"      写入失败：{e}")
    check("数据目录可写", writable, f"{data_dir}"
          + ("（隔离模式 EDU_DATA_DIR）" if isolated else "（正式目录）"),
          "检查目录权限，或设置 EDU_DATA_DIR 指向可写目录")

    # 只读地报告目标目录里已有哪些库（不建表、不写入）
    dbs = sorted(p.name for p in data_dir.glob("*.db"))
    check("目标数据目录已有数据库", bool(dbs),
          "已存在：" + ", ".join(dbs) if dbs
          else "暂无：首次启动后端时会自动建表",
          "首次启动后端即可自动创建；本自检不会代你建库", required=False)

    # 「能否初始化数据库」放到临时目录里验证，避免碰正式库
    import tempfile
    from unittest import mock
    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="edu_first_run_"))
        with mock.patch.dict(os.environ, {"EDU_DATA_DIR": str(tmp_dir)}):
            from config.path_config import get_data_dir as _gdd
            with mock.patch("config.path_config.get_data_dir", return_value=tmp_dir):
                import importlib
                from services import (auth_service as _auth, conversation_service as _conv,
                                      learning_data as _ld, learning_service as _ls,
                                      teaching_data as _td, user_profiler as _up)
                from services import (knowledge_expander as _ke, agent_runtime as _ar,
                                      learning_memory as _lm, plan_adjustment as _pa)
                patchers = []
                for mod, attr in ((_auth, "DB_PATH"), (_auth, "_DATA_DIR"), (_conv, "DB_PATH"),
                                  (_conv, "_DATA_DIR"), (_ld, "DB_PATH"), (_ld, "_DATA_DIR"),
                                  (_ls, "DB_PATH"), (_ls, "_DATA_DIR"), (_td, "DB_PATH"),
                                  (_td, "_DATA_DIR"), (_up, "DB_PATH"), (_up, "_DATA_DIR"),
                                  (_ar, "DB_PATH"), (_ar, "_DATA_DIR"), (_lm, "DB_PATH"),
                                  (_lm, "_DATA_DIR"), (_pa, "DB_PATH"), (_pa, "_DATA_DIR")):
                    path = (str(tmp_dir / "auth.db") if attr == "DB_PATH"
                            else str(tmp_dir))
                    patchers.append(mock.patch.object(mod, attr, path))
                patchers.append(mock.patch.object(
                    _ke, "EXTRACTED_KB_PATH", str(tmp_dir / "extracted_knowledge.db")))
                patchers.append(mock.patch.object(_ke, "_DATA_DIR", str(tmp_dir)))
                for p_ in patchers:
                    p_.start()
                try:
                    for mod in (_auth, _conv, _ld, _ls, _td, _up, _ar, _lm, _pa):
                        if hasattr(mod, "_init_db"):
                            mod._init_db()
                    created = sorted(x.name for x in Path(tmp_dir).glob("*.db"))
                finally:
                    for p_ in patchers:
                        p_.stop()
        check("数据库结构可以正常初始化", bool(created),
              f"在临时目录建出 {len(created)} 个库：{', '.join(created)}",
              "查看控制台报错；通常是依赖缺失或磁盘空间问题")
    except Exception as e:
        check("数据库结构可以正常初始化", False, str(e)[:140], "查看完整报错堆栈")
    finally:
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

    # ---------- 3. 文本模型密钥 ----------
    print("\n[3/7] 文本模型（DeepSeek）")
    try:
        from config.models import get_default_model, get_model_api_key
        from services.ai_service import resolve_model_info
        provider = get_default_model()
        key = get_model_api_key(provider)
        info = resolve_model_info()
        check(f"默认文本模型可解析（{provider}）", bool(info.get("model")),
              f"model={info.get('model')}", "检查 config/models.py 与 .env 的 LLM_MODEL")
        check(f"{provider} 密钥已配置", bool(key),
              f"来源：{'环境变量/本地未跟踪配置' if key else '未找到'}",
              "在 .env 或环境变量里配置对应 API Key（参考 config/env.example；"
              "不要把真实密钥写进代码或提交到 Git）")
    except Exception as e:
        check("文本模型配置读取", False, str(e)[:120], "检查 config/models.py")

    # ---------- 4. Embedding ----------
    print("\n[4/7] 向量化模型（本地 Embedding）")
    try:
        from config import settings
        mode = getattr(settings, "EMBEDDING_LOAD_MODE", "auto")
        local_dir = getattr(settings, "EMBEDDING_LOCAL_DIR", "") or ""
        cached = bool(local_dir) and Path(local_dir).exists()
        check("Embedding 加载模式", True, f"EMBEDDING_LOAD_MODE={mode}"
              + (f"，本地目录存在" if cached else "，首次检索会自动下载（需联网）"),
              required=False)
    except Exception as e:
        check("Embedding 配置读取", False, str(e)[:120], required=False)

    # ---------- 5. 学科与知识库 ----------
    print("\n[5/7] 学科与知识库")
    try:
        from services import knowledge_service, subject_service
        subjects = subject_service.get_subjects()
        names = [s.get("id") for s in subjects]
        check("学科清单可读", True,
              f"{len(names)} 个：{', '.join(str(n) for n in names[:5])}"
              + ("…" if len(names) > 5 else ""), required=False)
        bases = knowledge_service.list_knowledge_bases()
        ready = [b for b in bases if str(b.get("status")) in ("ready", "hybrid_ready")]
        check("已有可用知识库", bool(ready),
              f"{len(ready)} 个可用" if ready else "暂无：需要先在教师工作台或知识库页上传资料并构建",
              "教师端：课程资料 → 上传 → 重建知识库；或前端「知识库」页构建",
              required=False)
    except Exception as e:
        check("学科/知识库状态", False, str(e)[:120], required=False)

    # ---------- 6. 本地语音（可选） ----------
    print("\n[6/7] 本地语音（可选，不启动只影响语音功能）")
    stack = Path(os.getenv("SPEECH_STACK_DIR") or (ROOT.parent / "speech_stack"))
    check("语音栈目录存在", stack.exists(), str(stack),
          "按 docs/LOCAL_SPEECH.md 第四节安装（或在 .env 里设置 SPEECH_STACK_DIR）",
          required=False)
    url = (os.getenv("LOCAL_SPEECH_URL") or "http://127.0.0.1:8100").rstrip("/")
    try:
        with urllib.request.urlopen(url + "/health", timeout=5) as r:
            health = json.loads(r.read().decode())
        engines = health.get("engines") or {}
        state = {k: (v or {}).get("state") for k, v in engines.items()}
        check("本地语音服务在线", all(s == "ready" for s in state.values()),
              f"引擎状态 {state}", "启动 speech_service.server；未就绪时模型仍在加载",
              required=False)
    except Exception:
        check("本地语音服务在线", False, f"{url} 未响应",
              "需要语音功能时再启动（文字问答、学习闭环不受影响）", required=False)

    # ---------- 7. 已启动后端（可选） ----------
    if args.url:
        print("\n[7/7] 后端健康检查")
        try:
            with urllib.request.urlopen(args.url.rstrip("/") + "/api/health", timeout=8) as r:
                data = json.loads(r.read().decode()).get("data") or {}
            caps = data.get("capabilities") or {}
            scope = data.get("data_scope") or {}
            check("后端在线", True,
                  f"隔离={scope.get('isolated')}（{scope.get('dir_name')}）")
            check("文本问答可用", bool((caps.get("text") or {}).get("available")),
                  f"{caps.get('text', {}).get('provider')}/{caps.get('text', {}).get('model')}",
                  "检查模型密钥与网络")
            for key, label in (("vision", "视觉"), ("local_asr", "本地语音识别"),
             ):
                cap = caps.get(key) or {}
                check(f"{label}状态", bool(cap.get("available")),
                      cap.get("reason") or "可用", required=False)
        except Exception as e:
            check("后端在线", False, str(e)[:120], "确认已启动 uvicorn 且端口正确")

    # ---------- 汇总 ----------
    bad = [r for r in results if not r["ok"] and r["required"]]
    warn = [r for r in results if not r["ok"] and not r["required"]]
    print("\n" + "=" * 64)
    print(f"自检结果：必需项 {len(results) - len(warn) - len(bad)}/{len(results) - len(warn)} 通过，"
          f"可选项 {len(warn)} 项待补，必需项缺失 {len(bad)} 项")
    if bad:
        print("\n必须先解决：")
        for r in bad:
            print(f"  - {r['name']}：{r['detail']}\n    ↳ {r['fix']}")
    if warn:
        print("\n可选项（不影响文字问答与学习闭环）：")
        for r in warn:
            print(f"  - {r['name']}：{r['detail']}")
    print("\n演示路径见 docs/DEMO_GUIDE.md；评测见 docs/TEACHING_EVAL.md")
    print("=" * 64)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
