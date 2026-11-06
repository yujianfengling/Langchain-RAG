"""知识图谱节点从原文摘抄公式和定义，公式排在定义前面。"""

from rag.knowledge_graph import graph_builder as gb


class _Doc:
    def __init__(self, content, **meta):
        self.page_content = "【上下文】这是检索前缀，不要被摘进去。" + content
        self.metadata = {"original_content": content, **meta}


def test_formula_is_listed_before_definition_and_text_is_verbatim():
    formula = "欧拉公式 $$F_{cr}=\\frac{\\pi^{2}EI}{(\\mu l)^{2}}$$"
    definition = "欧拉公式是指压杆临界力的计算公式。"
    nodes = [{"id": "欧拉公式", "label": "欧拉公式"}]
    gb.attach_source_excerpts(nodes, [
        _Doc(definition, file_name="第1章.pdf", page=1, page_base=0),
        _Doc(formula, file_name="第1章.pdf", page=2, page_base=0),
    ])
    kinds = [item["kind"] for item in nodes[0]["excerpts"]]
    assert kinds[0] == "formula"
    assert "definition" in kinds
    assert nodes[0]["excerpts"][0]["text"] in formula
    assert any(item["text"] in definition for item in nodes[0]["excerpts"])
    assert nodes[0]["excerpts"][0]["file_name"] == "第1章.pdf"
    assert nodes[0]["excerpts"][0]["page"] == 3
    assert all("检索前缀" not in item["text"] for item in nodes[0]["excerpts"])


def test_plain_equation_line_counts_as_formula():
    text = "临界力\nF = π²EI / (μl)²"
    nodes = [{"id": "临界力", "label": "临界力"}]
    gb.attach_source_excerpts(nodes, [_Doc(text, file_name="笔记.md", page=4)])
    assert nodes[0]["excerpts"]
    assert nodes[0]["excerpts"][0]["kind"] == "formula"
    assert nodes[0]["excerpts"][0]["text"] in text
    assert nodes[0]["excerpts"][0]["page"] == 4


def test_missing_term_gets_empty_excerpts():
    nodes = [{"id": "不存在的概念", "label": "不存在的概念"}]
    gb.attach_source_excerpts(nodes, [_Doc("这里只讲弯曲应力。", file_name="a.pdf")])
    assert nodes[0]["excerpts"] == []


def test_plain_mention_is_kept_when_it_is_not_a_formula_or_definition():
    text = "本章介绍岩石的基本力学性质，包括强度与变形。"
    nodes = [{"id": "岩石的基本力学性质", "label": "岩石的基本力学性质"}]
    gb.attach_source_excerpts(nodes, [_Doc(text, file_name="第1章.pdf", page=5)])
    assert nodes[0]["excerpts"][0]["kind"] == "passage"
    assert nodes[0]["excerpts"][0]["text"] in text


def test_chinese_equals_in_prose_is_not_a_formula():
    text = "岩体=岩块+弱面，这是岩石力学里的一种约定。"
    nodes = [{"id": "岩石力学", "label": "岩石力学"}]
    gb.attach_source_excerpts(nodes, [_Doc(text, file_name="第1章.pdf", page=6)])
    assert nodes[0]["excerpts"]
    assert all(item["kind"] != "formula" for item in nodes[0]["excerpts"])
    assert nodes[0]["excerpts"][0]["text"] in text


def test_outline_loses_to_the_explanatory_sentence():
    outline = "第2章 岩石的基本力学性质\n2.1 岩石的强度特性\n2.1.1 概念\n2.1.2 岩块的强度"
    body = "岩石的基本力学性质主要包括两大类，即变形性质和强度性质。"
    nodes = [{"id": "岩石的基本力学性质", "label": "岩石的基本力学性质"}]
    gb.attach_source_excerpts(nodes, [
        _Doc(outline, file_name="目录.pdf", page=1),
        _Doc(body, file_name="第2章.pdf", page=2),
    ])
    assert nodes[0]["excerpts"][0]["kind"] == "passage"
    assert nodes[0]["excerpts"][0]["text"] in body
    assert nodes[0]["excerpts"][0]["file_name"] == "第2章.pdf"
    assert len(nodes[0]["excerpts"]) == 1


def test_definition_colon_beats_header_noise():
    noise = "2025/12/29 《岩石力学》 26\n321σσσ=>\n123σσσ>>"
    body = "定义：岩石力学是研究岩石的力学性质的一门理论与应用科学；它是力学的一个分支。"
    nodes = [{"id": "岩石力学", "label": "岩石力学"}]
    gb.attach_source_excerpts(nodes, [
        _Doc(noise, file_name="幻灯.pdf", page=26),
        _Doc(body, file_name="第1章.pdf", page=13),
    ])
    assert nodes[0]["excerpts"][0]["kind"] == "definition"
    assert "一门理论与应用科学" in nodes[0]["excerpts"][0]["text"]
    assert all(item["kind"] != "formula" for item in nodes[0]["excerpts"])


def test_slide_header_equation_does_not_outrank_definition():
    noise = "2025/11/27 《岩石力学》 13\ntK\nφsin1−="
    body = "定义：岩石力学是研究岩石的力学性质的一门理论与应用科学；它是力学的一个分支。"
    nodes = [{"id": "岩石力学", "label": "岩石力学"}]
    gb.attach_source_excerpts(nodes, [
        _Doc(noise, file_name="幻灯.pdf", page=13),
        _Doc(body, file_name="第1章.pdf", page=12),
    ])
    assert nodes[0]["excerpts"][0]["kind"] == "definition"
    assert "一门理论与应用科学" in nodes[0]["excerpts"][0]["text"]
    assert all(item["kind"] != "formula" for item in nodes[0]["excerpts"])


def test_detached_sigma_fills_the_blank_in_reading_order():
    text = (
        "当     为常数时岩石的变形特性(图(b))\n"
        "随着      的增大，岩石的屈服应力有所提高。\n"
        "3σ\n"
        "2σ"
    )
    nodes = [{"id": "岩石的变形特性", "label": "岩石的变形特性"}]
    gb.attach_source_excerpts(nodes, [_Doc(text, file_name="第2章.pdf", page=82)])
    got = nodes[0]["excerpts"][0]["text"]
    assert "当σ₃为常数" in got
    assert "随着σ₂的增大" in got
    assert "     " not in got


def test_label_split_across_lines_is_still_excerpted():
    text = "岩石的基本力学性质主要包括两大类，即岩石的变形性质和岩石的强\n度性质。"
    nodes = [{"id": "岩石的强度性质", "label": "岩石的强度性质"}]
    gb.attach_source_excerpts(nodes, [_Doc(text, file_name="第2章.pdf", page=1)])
    got = nodes[0]["excerpts"][0]["text"]
    assert "岩石的强" in got
    assert "度性质" in got


def test_definition_split_across_lines_stays_a_definition():
    text = "定义：岩石试件在无侧限和单轴压力作用下抵抗破坏的极限\n能力；"
    nodes = [{"id": "无侧限和单轴压力作用下抵抗破坏的极限能力", "label": "无侧限和单轴压力作用下抵抗破坏的极限能力"}]
    gb.attach_source_excerpts(nodes, [_Doc(text, file_name="第2章.pdf", page=8)])
    assert nodes[0]["excerpts"][0]["kind"] == "definition"
    assert "极限" in nodes[0]["excerpts"][0]["text"]
    assert "能力" in nodes[0]["excerpts"][0]["text"]


def test_optional_de_still_uses_the_original_sentence():
    text = "目的：科学、合理、安全地维护井巷的稳定性，降低维护成本。"
    nodes = [{"id": "井巷稳定性", "label": "井巷稳定性"}]
    gb.attach_source_excerpts(nodes, [_Doc(text, file_name="第1章.pdf", page=13)])
    assert "井巷的稳定性" in nodes[0]["excerpts"][0]["text"]


def test_one_extra_modifier_still_finds_the_phrase():
    text = "解决途径：配置先进的闭环控制系统，才能记下峰后曲线。"
    nodes = [{"id": "配置闭环控制系统", "label": "配置闭环控制系统"}]
    gb.attach_source_excerpts(nodes, [_Doc(text, file_name="第2章.pdf", page=7)])
    assert "配置先进的闭环控制系统" in nodes[0]["excerpts"][0]["text"]


def test_missing_character_in_the_label_can_use_the_shorter_source_term():
    text = "净水压力（侧压系数 λ）下围岩应力仍按弹性计算。"
    nodes = [{"id": "侧压力系数", "label": "侧压力系数"}]
    gb.attach_source_excerpts(nodes, [_Doc(text, file_name="第6章.pdf", page=4)])
    assert "侧压系数" in nodes[0]["excerpts"][0]["text"]


def test_phenomenon_suffix_uses_the_word_in_the_source():
    text = "破坏时，变形较大，有明显的“剪胀”效应，一般发生在较软弱岩石。"
    nodes = [{"id": "剪胀现象", "label": "剪胀现象"}]
    gb.attach_source_excerpts(nodes, [_Doc(text, file_name="第2章.pdf", page=4)])
    assert "剪胀" in nodes[0]["excerpts"][0]["text"]


def test_long_paraphrase_keeps_the_longest_real_phrase():
    text = "式中：σc 为单轴抗压强度。\nP 为无侧限条件下岩石试件的轴向破坏荷载；\nA 为试件的截面面积。"
    nodes = [{"id": "轴向破坏荷载与截面面积之比", "label": "轴向破坏荷载与截面面积之比"}]
    gb.attach_source_excerpts(nodes, [_Doc(text, file_name="第2章.pdf", page=8)])
    assert "轴向破坏荷载" in nodes[0]["excerpts"][0]["text"]
    assert "截面面积之比" not in nodes[0]["excerpts"][0]["text"]


def test_shared_suffix_does_not_steal_another_discipline():
    other = "弹塑性力学的研究方法在岩石力学研究中一直占据主导的地位。"
    own = "1.3 岩石力学的研究范畴、内容、方法和工程应用。"
    nodes = [{"id": "岩石力学的研究方法", "label": "岩石力学的研究方法"}]
    gb.attach_source_excerpts(nodes, [
        _Doc(other, file_name="第1章.pdf", page=26),
        _Doc(own, file_name="第1章.pdf", page=3),
    ])
    assert "研究范畴" in nodes[0]["excerpts"][0]["text"]
    assert "弹塑性" not in nodes[0]["excerpts"][0]["text"]


def test_richer_sentence_beats_a_short_fragment():
    short = "会出现剪胀随之减弱的现象。"
    rich = "剪胀或扩容：当岩石受外力作用后，发生非线性的体积膨胀，且这一体积膨胀是不可逆的。"
    nodes = [{"id": "剪胀现象", "label": "剪胀现象"}]
    gb.attach_source_excerpts(nodes, [
        _Doc(short, file_name="第2章.pdf", page=86),
        _Doc(rich, file_name="第2章.pdf", page=85),
    ])
    assert "体积膨胀" in nodes[0]["excerpts"][0]["text"]


def test_short_overlap_does_not_steal_another_concept():
    text = "定义：岩石力学是研究岩石的力学性质的一门理论与应用科学。"
    nodes = [{"id": "岩石力学工程应用研究", "label": "岩石力学工程应用研究"}]
    gb.attach_source_excerpts(nodes, [_Doc(text, file_name="第1章.pdf", page=13)])
    assert nodes[0]["excerpts"] == []


def test_ocr_lookalike_is_not_guessed():
    text = "深埋巷道属于无限城问题，影响圈内自重可以忽略。"
    nodes = [{"id": "无限域问题", "label": "无限域问题"}]
    gb.attach_source_excerpts(nodes, [_Doc(text, file_name="第5章.pdf", page=24)])
    assert nodes[0]["excerpts"] == []


def test_ensure_does_not_reread_index_when_excerpts_exist(monkeypatch):
    graph = {
        "nodes": [{"id": "应力", "label": "应力", "excerpts": []}],
        "edges": [],
        "stats": {"excerpt_version": 7},
    }

    def _boom(_subject_id):
        raise AssertionError("已有摘录字段时不应再读知识库")

    monkeypatch.setattr(gb, "_load_index_documents", _boom)
    assert gb.ensure_node_excerpts("subj", graph) is graph
