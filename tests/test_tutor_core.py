"""
统一伴学核心模块的回归测试（2026-10-07）

覆盖本轮新增的权威逻辑，避免"静默降级"式失效：
- 资料范围：稳定 doc_id、四态、失效项、同名隔离、签名随版本变化
- 统一上下文与轮次事件：after_seq 重放、取消、终态
- 学习地图：稳定 ID、未知来源剔除、先修环检测、重建保身份、下一步建议
- 学习成果：版本递增、用户编辑后不被覆盖
- 工具注册表：按能力过滤、拒绝未知/越权工具、忽略模型传入的身份
"""

import json
import uuid

import pytest

from services import artifacts_service, knowledge_map, material_scope, tutor_session, tutor_tools


# =====================================================================
# 资料范围
# =====================================================================

def _fake_doc(content, **meta):
    class _Doc:
        def __init__(self):
            self.page_content = content
            self.metadata = meta
    return _Doc()


def _patch_personal(monkeypatch, files):
    monkeypatch.setattr(material_scope, "_course_id_for_subject", lambda sid: None)
    monkeypatch.setattr(
        "services.knowledge_service.get_subject_documents",
        lambda sid: [{"name": n, "size": 10, "upload_time": "2026-10-01"} for n in files])


def test_scope_all_and_empty(monkeypatch):
    _patch_personal(monkeypatch, [])
    scope = material_scope.build_scope("u1", "s1", None)
    assert scope["mode"] == "empty"
    assert scope["selected"] == []

    _patch_personal(monkeypatch, ["a.txt", "b.txt"])
    scope = material_scope.build_scope("u1", "s1", None)
    assert scope["mode"] == "all"
    assert len(scope["selected"]) == 2
    assert scope["label"].startswith("全部资料")


def test_scope_selected_and_invalid(monkeypatch):
    _patch_personal(monkeypatch, ["a.txt", "b.txt"])
    scope = material_scope.build_scope("u1", "s1", ["f:a.txt", "f:missing.txt"])
    assert scope["mode"] == "selected"
    assert [m["file_name"] for m in scope["selected"]] == ["a.txt"]
    assert scope["invalid"] and scope["invalid"][0]["doc_id"] == "f:missing.txt"

    # 全部失效 → empty，绝不退化成 all
    scope2 = material_scope.build_scope("u1", "s1", ["f:missing.txt"])
    assert scope2["mode"] == "empty"
    assert scope2["selected"] == []


def test_scope_accepts_legacy_filenames(monkeypatch):
    _patch_personal(monkeypatch, ["讲义.txt"])
    scope = material_scope.build_scope("u1", "s1", ["讲义.txt"])
    assert scope["mode"] == "selected"
    assert scope["selected"][0]["doc_id"] == "f:讲义.txt"


def test_scope_signature_changes_with_version(monkeypatch):
    _patch_personal(monkeypatch, ["a.txt"])
    s1 = material_scope.build_scope("u1", "s1", ["f:a.txt"])
    s2 = material_scope.build_scope("u2", "s1", ["f:a.txt"])
    s3 = material_scope.build_scope("u1", "s2", ["f:a.txt"])
    assert s1["signature"] != s2["signature"], "不同用户的缓存键必须不同"
    assert s1["signature"] != s3["signature"], "不同学科的缓存键必须不同"


def test_scope_filter_uses_stable_material_id():
    """
    契约（有意设计，不要"优化"掉）：

    - 分块有 material_id 时，**只认 material_id**：同名但属于其他资料的块必须排除；
    - 分块没有 material_id（旧索引/未重建）时，回退按文件名认领——
      与 services/teaching_access 的既有回退语义一致，否则旧索引会整库检索不到。
    """
    scope = {"mode": "selected", "selected": [
        {"doc_id": "m:mat_1", "material_id": "mat_1", "file_name": "同名.txt"},
    ]}
    docs = [
        _fake_doc("好", material_id="mat_1", file_name="同名.txt"),
        _fake_doc("坏", material_id="mat_2", file_name="同名.txt"),
        _fake_doc("旧索引", file_name="同名.txt"),
    ]
    kept = material_scope.filter_documents_for_scope(scope, docs)
    assert [d.page_content for d in kept] == ["好", "旧索引"], "同名校验必须优先用稳定 material_id"
    assert "坏" not in [d.page_content for d in kept]


def test_scope_filter_all_and_empty():
    docs = [_fake_doc("x", material_id="mat_1")]
    assert material_scope.filter_documents_for_scope({"mode": "all"}, docs) == docs
    assert material_scope.filter_documents_for_scope({"mode": "empty"}, docs) == []


# =====================================================================
# 统一上下文与轮次
# =====================================================================

def test_context_roundtrip_and_scope():
    tutor_session.set_material_scope("ctx_user", "ctx_sub", ["m:a", "m:b"])
    ctx = tutor_session.load_context("ctx_user", "ctx_sub")
    assert ctx["material_scope"]["selected_doc_ids"] == ["m:a", "m:b"]
    assert ctx["material_scope"]["mode"] == "selected"

    # 传入不可写字段（user_id 等）不报错，但必须被忽略
    tutor_session.update_context("ctx_user", "ctx_sub", ability="practice", user_id="hacker")
    ctx2 = tutor_session.load_context("ctx_user", "ctx_sub")
    assert ctx2["ability"] == "practice"
    assert ctx2["user_id"] == "ctx_user", "上下文的 user_id 不可被外部改写"
    # save_context 也必须强制回权威身份
    saved = tutor_session.save_context("ctx_user", "ctx_sub",
                                       {"user_id": "hacker", "subject_id": "hacked"})
    assert saved["user_id"] == "ctx_user" and saved["subject_id"] == "ctx_sub"


def test_turn_events_and_replay():
    turn = tutor_session.create_turn("ev_user", "ev_sub", "chat", "你好")
    a = tutor_session.append_event(turn, "start", {"a": 1})
    b = tutor_session.append_event(turn, "delta", {"b": 2})
    assert (a, b) == (1, 2)
    assert len(tutor_session.list_events(turn)) == 2
    tail = tutor_session.list_events(turn, after_seq=1)
    assert [e["type"] for e in tail] == ["delta"]

    tutor_session.finish_turn(turn, tutor_session.STATE_SUCCEEDED, answer="ok", steps=2)
    got = tutor_session.get_turn(turn, "ev_user")
    assert got["status"] == tutor_session.STATE_SUCCEEDED
    assert got["answer"] == "ok" and got["steps"] == 2
    assert tutor_session.get_turn(turn, "other_user") is None, "轮次不能跨用户读取"


def test_turn_cancel_semantics():
    turn = tutor_session.create_turn("cx_user", "cx_sub", "chat", "hi")
    assert tutor_session.request_cancel(turn, "other") is False
    assert tutor_session.request_cancel(turn, "cx_user") is True
    assert tutor_session.is_cancelled(turn) is True
    tutor_session.finish_turn(turn, tutor_session.STATE_CANCELLED)
    assert tutor_session.request_cancel(turn, "cx_user") is False, "终态不可再次取消"


# =====================================================================
# 学习地图
# =====================================================================

RAW_MAP = {
    "title": "演示课",
    "nodes": [
        {"label": "树", "level": "chapter", "source_docs": ["a.txt"], "type": "concept"},
        {"label": "遍历", "level": "section", "parent": "树", "source_docs": ["a.txt"],
         "prerequisites": ["树"], "type": "procedure"},
        {"label": "环A", "level": "point", "source_docs": ["b.txt"], "prerequisites": ["环B"]},
        {"label": "环B", "level": "point", "source_docs": ["b.txt"], "prerequisites": ["环A"]},
        {"label": "引用不存在资料", "level": "point", "source_docs": ["ghost.txt"]},
        {"label": "遍历", "level": "point", "source_docs": ["a.txt"]},
    ],
}


def test_validate_map_structure():
    skeleton, diags = knowledge_map._validate_map(RAW_MAP, ["a.txt", "b.txt"])
    labels = [n["label"] for n in skeleton["nodes"]]
    assert labels.count("遍历") == 1, "同名节点应被合并"
    assert "引用不存在资料" in labels
    codes = {d["code"] for d in diags}
    assert "unknown_source" in codes
    assert "duplicate_label" in codes
    assert "prereq_cycle" in codes
    # 未知来源被剔除，但节点保留
    ghost = next(n for n in skeleton["nodes"] if n["label"] == "引用不存在资料")
    assert ghost["source_docs"] == []


def test_stable_node_id_and_regeneration_keeps_identity():
    skeleton1, _ = knowledge_map._validate_map(RAW_MAP, ["a.txt", "b.txt"])
    subject = "map_sub_1"
    payload1 = {
        "map_id": "map_1", "title": "t", "nodes": skeleton1["nodes"], "uncovered": [],
        "source_signature": "sig1", "diagnostics": [],
    }
    knowledge_map._save_map(subject, payload1)
    first = {n["label"]: n["node_id"] for n in skeleton1["nodes"]}

    # 同一个 label 在不同父路径下 ID 必须不同；相同输入必须稳定
    assert knowledge_map.stable_node_id("遍历", "root") == knowledge_map.stable_node_id(" 遍历 ", "root")
    assert knowledge_map.stable_node_id("遍历", "root") != knowledge_map.stable_node_id("遍历", "second")

    # 重建时按语义指纹匹配回旧 ID
    skeleton2, _ = knowledge_map._validate_map(RAW_MAP, ["a.txt", "b.txt"])
    by_fp = {knowledge_map.normalize_label(n["label"]): n for n in skeleton1["nodes"]}
    for node in skeleton2["nodes"]:
        old = by_fp.get(knowledge_map.normalize_label(node["label"]))
        if old:
            node["node_id"] = old["node_id"]
    assert {n["label"]: n["node_id"] for n in skeleton2["nodes"]} == first


def test_map_progress_is_per_user_and_next_recommendation():
    subject = "map_sub_2"
    skeleton, _ = knowledge_map._validate_map(RAW_MAP, ["a.txt", "b.txt"])
    knowledge_map._save_map(subject, {
        "map_id": "map_2", "title": "t", "nodes": skeleton["nodes"], "uncovered": [],
        "source_signature": "sig", "diagnostics": [],
    })
    tree = next(n for n in skeleton["nodes"] if n["label"] == "树")
    knowledge_map.set_node_status("u_a", subject, tree["node_id"], knowledge_map.STATUS_PASSED)
    assert knowledge_map.list_progress("u_b", subject) == {}, "学习进度必须按用户隔离"

    data = knowledge_map.map_with_progress("u_b", subject)
    assert data["next_node"] is not None
    assert data["next_node"]["label"] in {n["label"] for n in skeleton["nodes"]}

    with pytest.raises(ValueError):
        knowledge_map.set_node_status("u_a", subject, tree["node_id"], "not_a_status")


def test_recommend_next_prefers_review_then_prereq():
    nodes = [
        {"node_id": "n1", "label": "基础", "status": "new", "prerequisites": []},
        {"node_id": "n2", "label": "进阶", "status": "new", "prerequisites": ["n1"]},
    ]
    assert knowledge_map.recommend_next(nodes)["node_id"] == "n1"

    nodes2 = [
        {"node_id": "n1", "label": "基础", "status": "passed", "prerequisites": []},
        {"node_id": "n2", "label": "进阶", "status": "new", "prerequisites": ["n1"]},
    ]
    assert knowledge_map.recommend_next(nodes2)["node_id"] == "n2"

    nodes3 = [
        {"node_id": "n1", "label": "基础", "status": "needs_review", "prerequisites": []},
        {"node_id": "n2", "label": "进阶", "status": "new", "prerequisites": ["n1"]},
    ]
    assert knowledge_map.recommend_next(nodes3)["node_id"] == "n1"


# =====================================================================
# 学习成果
# =====================================================================

def test_artifact_regenerate_respects_user_edit():
    # 用唯一标识，保证测试可重复运行（测试库会跨次保留）
    tag = uuid.uuid4().hex[:8]
    user, subject = "art_user_" + tag, "art_sub_" + tag
    title = "笔记X-" + tag
    a1 = artifacts_service.save_generated(user, subject, "note", title, {"v": 1},
                                          source_signature="s1")
    assert a1["version"] == 1 and a1["edited"] is False

    a2 = artifacts_service.save_generated(user, subject, "note", title, {"v": 2},
                                          source_signature="s1")
    assert a2["id"] == a1["id"] and a2["version"] == 2, "未被编辑时原地更新并递增版本"

    updated = artifacts_service.update_artifact(user, a1["id"], content={"v": "user"})
    assert updated["edited"] is True

    a3 = artifacts_service.save_generated(user, subject, "note", title, {"v": 3},
                                          source_signature="s2")
    assert a3["id"] != a1["id"], "已编辑的成果不能被悄悄覆盖"
    assert artifacts_service.get_artifact(user, a1["id"])["superseded_by"] == a3["id"]

    listed = artifacts_service.list_artifacts(user, subject, None, "s3")
    assert len(listed) == 1 and listed[0]["stale"] is True


def test_artifact_user_isolation():
    tag = uuid.uuid4().hex[:8]
    subject = "iso_sub_" + tag
    artifacts_service.save_generated("owner_a_" + tag, subject, "outline", "提纲", {})
    assert artifacts_service.list_artifacts("owner_b_" + tag, subject) == []


# =====================================================================
# 工具注册表
# =====================================================================

def test_tools_filtered_by_ability():
    chat_tools = {t["function"]["name"] for t in tutor_tools.tools_for_ability("chat")}
    practice_tools = {t["function"]["name"] for t in tutor_tools.tools_for_ability("practice")}
    organize_tools = {t["function"]["name"] for t in tutor_tools.tools_for_ability("organize")}
    assert "search_materials" in chat_tools
    assert "generate_practice" in practice_tools
    # 说明：chat 也保留 generate_practice（学生可以直接说"出两道题"），
    # 但"生成知识地图"只属于 map/organize，"提出计划调整"只属于 organize。
    assert "generate_knowledge_map" not in chat_tools
    assert "propose_adjustment" in organize_tools
    assert "propose_adjustment" not in chat_tools, "能力边界必须真实生效"
    assert "generate_knowledge_map" in {t["function"]["name"] for t in tutor_tools.tools_for_ability("map")}


def test_execute_rejects_unknown_and_forbidden_tool():
    ctx = tutor_tools.ToolContext(user_id="u", subject_id="s", ability="chat", scope={"mode": "all"})
    unknown = tutor_tools.execute_tool("nope", {}, ctx)
    assert unknown["ok"] is False and unknown["error_type"] == "unknown_tool"

    forbidden = tutor_tools.execute_tool("propose_adjustment", {}, ctx)
    assert forbidden["ok"] is False and forbidden["error_type"] == "forbidden_tool"


def test_execute_ignores_model_supplied_identity(monkeypatch):
    seen = {}

    def _spy(ctx, **kwargs):
        seen["user_id"] = ctx.user_id
        seen["subject_id"] = ctx.subject_id
        seen["kwargs"] = kwargs
        return {"ok": True, "value": 1}

    spec = tutor_tools.TOOLS["search_materials"]
    monkeypatch.setattr(spec, "handler", _spy)
    ctx = tutor_tools.ToolContext(user_id="real_user", subject_id="real_sub",
                                  ability="chat", scope={"mode": "all"})
    out = tutor_tools.execute_tool(
        "search_materials",
        {"query": "x", "user_id": "attacker", "subject_id": "other", "scope": {"mode": "empty"}},
        ctx)
    assert out["ok"] is True
    assert seen["user_id"] == "real_user"
    assert seen["subject_id"] == "real_sub"
    assert "user_id" not in seen["kwargs"], "模型传入的身份字段必须被剥离"


def test_execute_reports_structured_error(monkeypatch):
    def _boom(ctx, **kwargs):
        raise RuntimeError("后端故障")

    monkeypatch.setattr(tutor_tools.TOOLS["search_materials"], "handler", _boom)
    ctx = tutor_tools.ToolContext(user_id="u", subject_id="s", ability="chat", scope={"mode": "all"})
    out = tutor_tools.execute_tool("search_materials", {"query": "x"}, ctx)
    assert out["ok"] is False and out["error_type"] == "tool_error"
    assert "后端故障" in out["error"]


def test_tool_schemas_are_valid_json_schema():
    for name, spec in tutor_tools.TOOLS.items():
        assert spec.name == name
        assert spec.description
        assert spec.parameters.get("type") == "object"
        assert "properties" in spec.parameters
        json.dumps(spec.parameters)  # 必须可序列化
        for required in spec.parameters.get("required", []):
            assert required in spec.parameters["properties"]
