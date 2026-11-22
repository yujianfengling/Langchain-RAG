"""
智慧学伴比赛版 · 官方字段契约（可调别名表）
============================================================
真实赛题资料尚未到位。本模块按四人开发方案中记录的口径定义「规范字段」，
每个规范字段带一组候选别名（中文表头 / 英文 id）。读取 CSV 时按顺序匹配别名；
拿到官方 CSV 后，只需在 ALIASES 里补上真实表头，或写一份 mapping.json 覆盖，
无需改 pipeline.py 的计算逻辑。

设计原则：
- 不猜字段：匹配不到就报缺字段，不静默填 0 / 空。
- 不硬编码演示题号或演示得分：所有统计来自 CSV 行。
- 状态严格分开：有效得分（含 0）、待评分、未作答、本次未测（节点未覆盖）、缺记录。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# 五张表的规范字段 + 候选别名（拿到真实 CSV 后在此补别名即可）
# ---------------------------------------------------------------------------

# 课程要求（官方 9 个观察指标，字段来自官方《字段说明.csv》）
# 官方没有 node_ids / material_ids 列；节点覆盖由「测验节点.可用题目编号」决定（见 pipeline.compute）。
CURRICULUM_FIELDS = {
    "indicator_id": ["indicator_id", "指标编号", "观察指标编号", "指标ID"],
    "theme_id": ["theme_id", "主题编号", "主题ID"],
    "theme_name": ["主题", "theme_name", "主题名称"],
    "requirement": ["观察指标", "requirement", "课标要求", "课程要求", "要求"],
    "standard_id": ["课标整理编号", "standard_id"],
    "standard_page": ["课标文件页／正文页", "课标文件页/正文页", "standard_page"],
    "textbook_page": ["教材正文页", "textbook_page"],
    # 以下仅自造样例有；官方包无此列，标记为可选（_resolve_columns 不会因此报错）
    "node_ids": ["node_ids", "适用测验节点", "测验节点", "node_id"],
}
OPTIONAL_CURRICULUM = {"node_ids", "theme_name", "standard_id", "standard_page", "textbook_page"}

# 题目与材料（官方 54 道题 + 8 教材切片 + 3 学习卡）
ITEM_FIELDS = {
    "item_id": ["item_id", "材料编号", "题目编号", "题号", "题目ID"],
    "theme_id": ["theme_id", "主题编号", "主题ID"],
    "material_type": ["材料类型", "material_type"],
    "question_type": ["题型", "question_type"],
    "indicator_id": ["indicator_id", "指标编号", "观察指标编号"],
    "stem": ["stem", "题干或内容", "题干", "题目", "题目内容"],
    "full_score": ["full_score", "满分", "总分", "分值"],
    "rubric_points": ["评分点", "rubric_points"],
    "source_location": ["source_location", "来源或资料文件", "出处", "教材页", "PDF页"],
    "material_id": ["material_id", "材料编号", "材料ID"],
}
OPTIONAL_ITEM = {"material_type", "question_type", "rubric_points", "material_id"}

# 模拟学生（官方：D01—D40，DC01=21 人 / DC02=19 人；无姓名字段）
STUDENT_FIELDS = {
    "student_id": ["student_id", "学生编号", "学号", "学生ID"],
    "class_id": ["class_id", "模拟班级编号", "班级编号", "班级", "班级ID"],
    "name": ["name", "姓名", "学生姓名"],
}
OPTIONAL_STUDENT = {"name"}

# 测验节点（官方：N01 / N02，含可用题目编号）
NODE_FIELDS = {
    "node_id": ["node_id", "节点编号", "测验节点", "节点ID"],
    "name": ["name", "节点名称", "名称", "说明"],
    "theme_ids": ["theme_ids", "已教及测量主题", "覆盖主题", "主题", "theme_id"],
    "date": ["date", "模拟测验日期", "日期", "测验日期"],
    "available_items": ["可用题目编号", "available_items"],
}
OPTIONAL_NODE = {"name", "available_items"}

# 作答记录（官方 806 行 → 归并 799 事件；基准 790 已评分 / 4 待评分 / 5 未作答）
ANSWER_FIELDS = {
    "record_id": ["record_id", "记录编号", "记录号", "record_id"],
    "event_id": ["event_id", "作答事件编号", "事件编号", "event_id"],
    "version": ["version", "修订版本", "版本", "version"],
    "student_id": ["student_id", "学生编号", "学号", "学生ID"],
    "item_id": ["item_id", "材料编号", "题目编号", "题号", "题目ID"],
    "node_id": ["node_id", "节点编号", "测验节点", "节点ID"],
    "theme_id": ["主题编号", "theme_id"],
    "score": ["score", "得分", "分数", "score"],
    "full_score": ["full_score", "满分", "总分"],
    "status": ["status", "评分状态", "状态", "作答状态"],
    "final_answer": ["学生最终作答", "final_answer"],
    "answer_text": ["answer_text", "解答过程", "作答过程", "原始步骤", "作答内容", "steps"],
    "rubric_scores": ["rubric_scores", "评分点得分", "评分点", "rubric"],
}
# 主题编号用于核对「作答记录主题」与「题目表主题」是否一致（官方 DR0081 即为一例）；
# 自造样例可能没有该列，标为可选，缺失不影响统计。
OPTIONAL_ANSWER = {"final_answer", "theme_id"}

TABLES = {
    "课程要求": (CURRICULUM_FIELDS, True, OPTIONAL_CURRICULUM),
    "题目与材料": (ITEM_FIELDS, True, OPTIONAL_ITEM),
    "模拟学生": (STUDENT_FIELDS, True, OPTIONAL_STUDENT),
    "测验节点": (NODE_FIELDS, True, OPTIONAL_NODE),
    "作答记录": (ANSWER_FIELDS, True, OPTIONAL_ANSWER),
}

# 文件发现：真实包名未知，按「规范名 + 常见别名」找；明确列出命中与未命中。
FILE_HINTS = {
    "课程要求": ["课程要求.csv", "curriculum.csv", "1_课程要求.csv"],
    "题目与材料": ["题目与材料.csv", "items.csv", "2_题目与材料.csv"],
    "模拟学生": ["模拟学生.csv", "students.csv", "3_模拟学生.csv"],
    "测验节点": ["测验节点.csv", "nodes.csv", "4_测验节点.csv"],
    "作答记录": ["作答记录.csv", "answers.csv", "5_作答记录.csv"],
}

# ---------------------------------------------------------------------------
# 状态归一：把各种中文写法收敛到规范状态
# ---------------------------------------------------------------------------
STATUS_SCORED = "有效得分"       # 含有效 0 分
STATUS_PENDING = "待评分"
STATUS_UNANSWERED = "未作答"
STATUS_NOT_COVERED = "本次未测"  # 节点未覆盖
STATUS_NO_RECORD = "缺记录"

STATUS_ALIASES = {
    STATUS_SCORED: ["已评分", "有效", "有效得分", "scored", "graded", "已判分", "有效零分"],
    STATUS_PENDING: ["待评分", "待判分", "pending", "未评分"],
    STATUS_UNANSWERED: ["未作答", "未答", "unanswered", "缺考", "空白"],
    STATUS_NOT_COVERED: ["本次未测", "未覆盖", "not_covered", "节点未覆盖"],
}


@dataclass
class LoadIssue:
    """数据质量报告的一行。"""
    table: str
    record_id: str
    event_id: str
    version: str
    issue: str
    action: str
    detail: str = ""


@dataclass
class LoadResult:
    rows: Dict[str, List[dict]] = field(default_factory=dict)
    column_map: Dict[str, Dict[str, str]] = field(default_factory=dict)
    file_paths: Dict[str, str] = field(default_factory=dict)
    issues: List[LoadIssue] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)


def normalize_status(raw: str) -> str:
    """返回规范状态；无法识别时返回原值（会被下游列为待核）。"""
    text = (raw or "").strip()
    for canonical, aliases in STATUS_ALIASES.items():
        for a in aliases:
            if text == a or a in text:
                return canonical
    return text
