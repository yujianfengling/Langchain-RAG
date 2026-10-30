"""章节大纲与学习路线"章节覆盖"回归测试（2026-10-30）

锁定本轮修复的行为契约：
- 章节标题识别只认"整行标题"，正文里的"第1章中已经介绍过…"不算章；
- 页眉重复出现的章标题按章号聚合，OCR 少字变体按票数取最完整写法；
- 多份拆分资料（part1/2/3）的同一章合并成一条清单；
- 学习路线生成：原文章节清单拼进提示词；模型漏章 → uncovered_chapters
  如实上报（不编造缺章节点），提示词里写过的章必须能被核对。
"""

import pytest

from services import chapter_outline
from services import knowledge_map


# ==================== 章节标题识别 ====================

def test_chapter_hits_rejects_body_mentions():
    """正文句子里的"第N章"不是标题：带句号/「中已」等尾巴的一律不产出。"""
    text = ("第1章中已经介绍过这一概念，本节不再重复。\n"
            "第2章 关系模型\n"
            "第3章 数据定义。句子还没结束\n")
    hits = list(chapter_outline.chapter_hits(text))
    assert [n for n, _ in hits] == [2]
    assert hits[0][1] == "关系模型"


def test_chapter_hits_takes_title_from_next_line():
    """「第N章」单独一行、标题在下一行的情况。"""
    text = "第4章\n\n数据完整性\n正文内容……\n"
    hits = list(chapter_outline.chapter_hits(text))
    assert hits and hits[0] == (4, "数据完整性")


def test_outline_from_pages_aggregates_headers():
    """页眉每页都写"第3章 …"：按章号聚合，页码取首次出现，标题取票数最多的写法。"""
    pages = [
        {"page": 0, "text": "第1章 绪论\n数据……"},
        {"page": 9, "text": "第3章 关系数据库标准语言SQL\n正文"},
        {"page": 10, "text": "第3章 关系数据库标准语言SQL\n正文"},
        {"page": 11, "text": "第3章 关系数据库标准语言SQL\n正文"},
        {"page": 12, "text": "第3章 论\n正文"},  # OCR 少字变体，票数少
    ]
    chapters = chapter_outline.outline_from_pages(pages)
    by_number = {c["number"]: c for c in chapters}
    assert by_number[1]["title"] == "绪论"
    assert by_number[1]["page"] == 0
    assert by_number[3]["title"] == "关系数据库标准语言SQL"  # 不是"论"
    assert by_number[3]["page"] == 9


# ==================== 多份资料合并 ====================

def test_merged_chapters_combines_parts():
    """同一本书拆成 part1/part2：第4章在两份都出现 → 合并为一条，页码取最早。"""
    outlines = {
        "教材_part1.pdf": {"chapters": [
            {"number": 1, "title": "绪论", "page": 0},
            {"number": 4, "title": "数据查询", "page": 30},
        ]},
        "教材_part2.pdf": {"chapters": [
            {"number": 4, "title": "数据查询", "page": 2},
            {"number": 5, "title": "数据更新", "page": 20},
        ]},
    }
    merged = chapter_outline.merged_chapters(outlines)
    by_number = {c["number"]: c for c in merged}
    assert set(by_number) == {1, 4, 5}
    assert by_number[4]["source_docs"] == ["教材_part1.pdf", "教材_part2.pdf"]
    assert by_number[4]["page"] == 2  # 跨资料取最早出现的页


# ==================== 校验：漏章如实上报 ====================

def _expected(number, title, page=0):
    return {"number": number, "title": title, "page": page}


def test_validate_map_reports_missing_chapters():
    """原文有 2 章、模型只生成了第 1 章 → 第 2 章进 uncovered_chapters 并报 chapter_missing。"""
    raw = {"title": "路线", "nodes": [
        {"label": "第1章 绪论", "level": "chapter"},
        {"label": "绪论要点", "level": "point", "parent": "第1章 绪论"},
    ]}
    expected = [_expected(1, "绪论"), _expected(2, "数据定义")]
    skeleton, diags = knowledge_map._validate_map(raw, ["教材.pdf"], expected)
    missing_labels = [c["label"] for c in skeleton["uncovered_chapters"]]
    assert missing_labels == ["第2章 数据定义"]
    assert any(d["code"] == "chapter_missing" and d["evidence"] == "第2章 数据定义"
               for d in diags)


def test_validate_map_accepts_renumbered_title_as_covered():
    """模型改写了章标题（"第2章 数据库"）但章号对上 → 算交代过，不算缺章。"""
    raw = {"title": "路线", "nodes": [
        {"label": "第1章 绪论", "level": "chapter"},
        {"label": "第2章 数据库", "level": "chapter"},
    ]}
    expected = [_expected(1, "绪论"), _expected(2, "数据定义")]
    skeleton, _diags = knowledge_map._validate_map(raw, ["教材.pdf"], expected)
    assert skeleton["uncovered_chapters"] == []


def test_validate_map_without_expected_keeps_old_contract():
    """没有章节清单（未建页/无书签）时行为不变：uncovered_chapters 为空、不误报。"""
    raw = {"title": "路线", "nodes": [{"label": "绪论", "level": "chapter"}]}
    skeleton, diags = knowledge_map._validate_map(raw, ["课件.pdf"])
    assert skeleton["uncovered_chapters"] == []
    assert not any(d["code"] == "chapter_missing" for d in diags)


# ==================== 生成链路接线 ====================

class _FakeProvider:
    """捕获提示词并返回固定 JSON，验证章节清单真的进了提示词与校验。"""

    def __init__(self, answer: str):
        self.answer = answer
        self.captured_prompt = ""

    def chat(self, messages, **kwargs):
        self.captured_prompt = messages[-1]["content"]
        return {"answer": self.answer, "finish_reason": "stop"}


def test_generate_map_prompt_carries_chapter_list(monkeypatch):
    """接线契约：outline 里的章必须出现在提示词里；模型漏章被如实上报。"""
    outlines = {"教材.pdf": {"chapters": [
        _expected(1, "绪论"), _expected(2, "数据定义"),
    ]}}
    evidence = [{"doc_id": "f:教材.pdf", "file_name": "教材.pdf",
                 "snippet": "第1章 绪论 数据……", "page": 0, "chunk_id": None,
                 "kind": "chapter", "chapter": "第1章 绪论"}]
    # 模型只生成了第 1 章（模拟"第1章直接跳第6章"的漏章场景）
    fake = _FakeProvider('{"title": "路线", "nodes": ['
                         '{"label": "第1章 绪论", "level": "chapter"}]}')
    monkeypatch.setattr(knowledge_map, "collect_evidence",
                        lambda *a, **k: (evidence, outlines))
    import services.model_service as model_service
    monkeypatch.setattr(model_service, "get_model_provider", lambda model=None: fake)

    payload = knowledge_map.generate_map("u1", "s1", {"selected": [
        {"file_name": "教材.pdf"}], "signature": "sig1"},
        subject_name="数据库", force=True)

    # 提示词里有权威章节清单（含第 2 章），且占位符已被替换掉
    assert "第2章 数据定义" in fake.captured_prompt
    assert "{chapter_section}" not in fake.captured_prompt
    # 漏掉的第 2 章如实上报，没有被编造成节点
    labels = [n["label"] for n in payload["nodes"]]
    assert labels == ["第1章 绪论"]
    assert [c["label"] for c in payload["uncovered_chapters"]] == ["第2章 数据定义"]
    assert any(d["code"] == "chapter_missing" for d in payload["diagnostics"])
