"""tutor 按需读取工具的权限与边界测试（2026-10-28）。"""

from __future__ import annotations

from pathlib import Path

from config.path_config import get_subjects_dir
from services import tutor_tools


def _ctx(subject_id: str = "read_tools_subject", scope: dict | None = None) -> tutor_tools.ToolContext:
    return tutor_tools.ToolContext(
        user_id="u_test", subject_id=subject_id, scope=scope or {"mode": "all"})


def test_read_source_rejects_path_traversal():
    ctx = _ctx()
    for name in ("../secret.txt", "a/b.pdf", "a\\b.pdf", ""):
        out = tutor_tools._t_read_source(ctx, name)
        assert out["ok"] is False


def test_read_source_missing_file():
    out = tutor_tools._t_read_source(_ctx(), "not_exists.pdf")
    assert out["ok"] is False
    assert "不存在" in out["error"]


def test_read_source_respects_selected_scope(tmp_path):
    subject_dir = Path(get_subjects_dir()) / "read_tools_scope"
    subject_dir.mkdir(parents=True, exist_ok=True)
    (subject_dir / "a.txt").write_text("hello", encoding="utf-8")

    ctx = _ctx("read_tools_scope", {"mode": "selected", "selected": []})
    out = tutor_tools._t_read_source(ctx, "a.txt")
    assert out["ok"] is False
    assert "范围" in out["error"]


def test_read_source_reads_text_file():
    subject_dir = Path(get_subjects_dir()) / "read_tools_subject"
    subject_dir.mkdir(parents=True, exist_ok=True)
    (subject_dir / "note.txt").write_text("数据库系统概论", encoding="utf-8")

    out = tutor_tools._t_read_source(_ctx(), "note.txt", max_chars=100)
    assert out["ok"] is True
    assert out["file_name"] == "note.txt"
    assert "数据库系统概论" in out["text"]


def test_read_memory_shape():
    out = tutor_tools._t_read_memory(_ctx())
    assert set(out) >= {"summary", "preferences", "memory_items", "recent_activity", "count"}
    assert isinstance(out["recent_activity"], list)


def test_new_tools_registered_with_valid_schema():
    for name in ("read_memory", "read_source"):
        spec = tutor_tools.TOOLS[name]
        assert spec.name == name
        assert spec.writes is False
        assert spec.parameters["type"] == "object"
        assert set(spec.parameters.get("required") or []) <= set(
            spec.parameters.get("properties") or {})


def test_tool_result_budget_truncates_cumulative_content():
    """单条不超限、但累计超预算时也要截断（否则 12 次 ×6000 字符会顶穿窗口）。"""
    from services import tutor_agent

    limit = 1000
    first, cut1 = tutor_agent._fit_tool_result("x" * 700, 0, limit)
    assert cut1 is False and len(first) == 700

    second, cut2 = tutor_agent._fit_tool_result("y" * 700, 700, limit)
    assert cut2 is True
    assert len(second) <= 400 + len("…（本轮工具结果已达上下文预算，此处截断）")

    third, cut3 = tutor_agent._fit_tool_result("z" * 700, limit, limit)
    assert cut3 is True and len(third) < 100
