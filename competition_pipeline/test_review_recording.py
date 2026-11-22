"""讲评记录审计的标准库 unittest；私有官方数据仅只读，不生成 bundle。

运行：python -B -m unittest discover -s <competition_pipeline> -p test_review_recording.py -v
私有数据不存在时只跳过官方集成测试，合成事实测试仍执行。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_plan  # noqa: E402


UNIT = "学生×题目×评分点观察项（不是得分）"


def point(record_id="R1", **updates):
    result = {"student": "S1", "item": "Q1", "indicator": "I1", "point_id": "P1",
              "label": "评分点", "record_id": record_id, "earned": 0, "full": 1}
    result.update(updates)
    return result


def facts(points=(), students=()):
    return {"lost": list(points), "done": {}, "scored_count": {},
            "insufficient": [{"student": student, "indicator": "I1", "effective": 0,
                              "min": 1, "pretest_questions": ["Q1"]} for student in students]}


def unassigned(points):
    return review_plan.make_plan("naive", 0, facts(points), [], False)


def public_point(value):
    # 独立断言输出 schema，不使用被测模块的 key/ref 辅助函数。
    return {"student": value["student"], "item": value["item"],
            "point_id": value["point_id"], "label": value["label"],
            "record_id": value["record_id"], "live": value.get("live", False)}


def assigned_tasks(plan):
    return ({(student, item["item_id"], "lecture") for item in plan["lecture_items"]
             for student in item["lost_students"]}
            | {(student, item["item_id"], "pretest") for item in plan["pretest_tasks"]
               for student in item["students"]})


class RecordingUnitTests(unittest.TestCase):
    def test_same_question_different_types_are_two_tasks(self):
        source = facts([point(), point("R2")], ["S1"])
        snapshot = copy.deepcopy(source)
        ranked = [{"item": "Q1", "indicator": "I1", "students": {"S1"}, "located": 2}]
        plan = review_plan.make_plan("common", 1, source, ranked, True)
        self.assertEqual(source, snapshot)
        self.assertEqual(ranked[0]["students"], {"S1"})
        self.assertEqual(plan["workload"], {
            "lecture_budget": 1, "pretest_budget": 3,
            "lecture_questions": 1, "pretest_questions": 1, "total_unique_questions": 1,
            "lecture_assignments": 1, "pretest_assignments": 1, "total_assignments": 2,
            "groups": [{"item_id": "Q1", "indicator": "I1", "students": ["S1"],
                        "questions_per_student": 1, "assignments": 1}],
            "student_tasks": [{"student": "S1", "lecture_items": ["Q1"],
                               "pretest_items": ["Q1"], "task_count": 2}],
        })
        self.assertEqual(plan["coverage_audit"], {
            "total_points": 2, "covered_points": 2, "uncovered_points": 0,
            "official_points": 2, "live_points": 0, "unit": UNIT,
        })
        self.assertEqual({p["record_id"] for p in plan["lecture_items"][0]["lost_points"]}, {"R1", "R2"})

    def test_assignment_identity_includes_type(self):
        source = facts([point()], ["S1"])
        ranked = [{"item": "Q1", "indicator": "I1", "students": {"S1"}}]
        lecture = review_plan.make_plan("naive", 1, source, ranked, False)
        pretest = review_plan.make_plan("pretest", 1, source, ranked, False)
        delta = review_plan.compare_plans(lecture, pretest)
        self.assertEqual(delta["assignments"], {
            "changed": True,
            "added": [{"student": "S1", "item_id": "Q1", "type": "pretest"}],
            "removed": [{"student": "S1", "item_id": "Q1", "type": "lecture"}],
            "unchanged_count": 0,
        })
        self.assertEqual(delta["added_points"], [public_point(point())])
        self.assertIn("同题不同 type 算两个任务", delta["explanation"])

    def test_same_items_but_different_students_changes_assignments(self):
        plans = []
        for student in ("S1", "S2"):
            plans.append(review_plan.make_plan(
                "pretest", 1, facts(students=[student]),
                [{"item": "Q1", "indicator": "I1", "students": {student}}], False))
        delta = review_plan.compare_plans(*plans)
        self.assertTrue(delta["assignments"]["changed"])
        self.assertEqual(delta["assignments"]["added"], [
            {"student": "S2", "item_id": "Q1", "type": "pretest"}])
        self.assertEqual(delta["assignments"]["removed"], [
            {"student": "S1", "item_id": "Q1", "type": "pretest"}])

    def test_each_observation_identity_field_is_preserved(self):
        original = point()
        for field, value in (("student", "S2"), ("item", "Q2"), ("point_id", "P2"),
                             ("record_id", "R2"), ("live", True)):
            with self.subTest(field=field):
                extra = point(**{field: value})
                before, after = unassigned([original]), unassigned([original, extra])
                delta = review_plan.compare_plans(before, after)
                self.assertEqual(delta["added_points"], [public_point(extra)])
                self.assertEqual(delta["removed_points"], [])
                reverse = review_plan.compare_plans(after, before)
                self.assertEqual(reverse["added_points"], [])
                self.assertEqual(reverse["removed_points"], [public_point(extra)])

    def test_record_replacement_is_not_canceled_by_same_student_item_point(self):
        before, after = unassigned([point("R1")]), unassigned([point("R2")])
        delta = review_plan.compare_plans(before, after)
        self.assertEqual(delta["added_points"], [public_point(point("R2"))])
        self.assertEqual(delta["removed_points"], [public_point(point("R1"))])
        self.assertNotIn("观察项总数增加", delta["explanation"])

    def test_label_is_display_only_and_duplicate_counts_are_not_lost(self):
        before = unassigned([point()])
        renamed = unassigned([point(label="更新展示标签")])
        delta = review_plan.compare_plans(before, renamed)
        self.assertEqual(delta["added_points"], [])
        self.assertEqual(delta["removed_points"], [])
        doubled = unassigned([point(), point()])
        self.assertEqual(review_plan.compare_plans(before, doubled)["added_points"],
                         [public_point(point())])

    def test_live_provenance_survives_moving_into_coverage(self):
        live = point("现场测试", point_id="LIVE-MID", live=True)
        before = unassigned([live])
        after = review_plan.make_plan("common", 1, facts([live]), [
            {"item": "Q1", "indicator": "I1", "students": {"S1"}}], False)
        self.assertTrue(after["lecture_items"][0]["lost_points"][0]["live"])
        self.assertEqual(after["coverage_audit"]["official_points"], 0)
        self.assertEqual(after["coverage_audit"]["live_points"], 1)
        delta = review_plan.compare_plans(before, after)
        self.assertEqual(delta["removed_points"], [public_point(live)])
        self.assertEqual(delta["added_points"], [])
        self.assertEqual(delta["before"]["total_points"], delta["after"]["total_points"])
        self.assertNotIn("观察项总数增加", delta["explanation"])

    def test_empty_facts_and_compare_are_pure_and_json_serializable(self):
        before = unassigned([])
        snapshot = copy.deepcopy(before)
        delta = review_plan.compare_plans(before, before)
        self.assertEqual(before, snapshot)
        self.assertEqual(delta["assignments"], {
            "changed": False, "added": [], "removed": [], "unchanged_count": 0})
        self.assertEqual(before["workload"]["student_tasks"], [])
        self.assertEqual(before["workload"]["total_assignments"], 0)
        self.assertEqual(before["coverage_audit"]["total_points"], 0)
        self.assertEqual(before["coverage_audit"]["unit"], UNIT)
        json.dumps({"plan": before, "comparison": delta}, ensure_ascii=False)


class OfficialRecordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(os.environ.get("COMPETITION_OFFICIAL_SRC_DIR", "") or
                    "D:/Document/langchain-rag-work/competition-data/official")
        cls.src = root if (root / "evidence_index.json").is_file() else root / "src"
        cls.analysis = root / "analysis"
        bundle = Path(os.environ.get("COMPETITION_OFFICIAL_BUNDLE_DIR", "") or
                      "D:/Document/langchain-rag-work/competition-data/official-bundle")
        required = [cls.src / "evidence_index.json", cls.src / "scope_index.json",
                    cls.analysis / "diagnoses.json", cls.analysis / "curriculum_map.json"]
        if not all(path.is_file() for path in required):
            raise unittest.SkipTest("本地私有 official/src 与 analysis 不可用")
        roots = [root] + ([bundle] if bundle.is_dir() else [])

        def hashes():
            return {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                    for directory in roots for path in directory.rglob("*") if path.is_file()}

        initial_hashes = hashes()

        def verify_unchanged():
            if hashes() != initial_hashes:
                raise AssertionError("官方数据或 bundle 的文件清单/SHA256 发生变化")
            print(f"Official/bundle SHA256 unchanged: {len(initial_hashes)} files")

        cls.addClassCleanup(verify_unchanged)
        cls.ctx = review_plan.load_context(cls.src, cls.analysis, "N01", "DC01", "T01")
        student_class = cls.ctx["scope"].get("student_class", {})
        cls.members = sorted(student for student, class_id in student_class.items() if class_id == "DC01")
        if not cls.members and (bundle / "students.json").is_file():
            cls.members = sorted(student["id"] for student in json.loads(
                (bundle / "students.json").read_text(encoding="utf-8")) if student.get("classId") == "DC01")
        if not cls.members:
            raise AssertionError("官方成员表中找不到 DC01；不从答卷反推成员")
        cls.base_facts = review_plan.build_facts(cls.ctx, cls.members)
        cls.plans = cls.generate()
        cls.common = cls.plans["strategies"]["common"]["budgets"]["3"]
        cls.saved_plans = root / "review_plans.json"

    @classmethod
    def generate(cls, extra=None):
        return review_plan.generate(cls.src, cls.analysis, cls.members,
                                    "N01", "DC01", "T01", extra=extra)

    def test_common_budget_and_actual_workload(self):
        plan = self.common
        w = plan["workload"]
        self.assertEqual(len(self.members), 21)
        self.assertEqual([item["item_id"] for item in plan["lecture_items"]], ["Q014", "Q018", "Q006"])
        self.assertEqual((w["lecture_budget"], w["pretest_budget"]), (3, 3))
        self.assertEqual((w["lecture_questions"], w["pretest_questions"], w["total_unique_questions"]), (3, 3, 6))
        self.assertEqual([(g["item_id"], len(g["students"]), g["assignments"])
                          for g in w["groups"]], [("Q001", 10, 10), ("Q002", 9, 9), ("Q003", 8, 8)])
        self.assertTrue(all(g["questions_per_student"] == 1 for g in w["groups"]))
        self.assertEqual((w["lecture_assignments"], w["pretest_assignments"], w["total_assignments"]), (19, 27, 46))
        self.assertEqual(len(w["student_tasks"]), 19)
        self.assertEqual(sum(row["task_count"] for row in w["student_tasks"]), 46)
        # 独立从返回的原始分配重建逐生任务，不能只验证合计自洽。
        expected = assigned_tasks(plan)
        rebuilt = {(row["student"], item, kind) for row in w["student_tasks"]
                   for kind in ("lecture", "pretest") for item in row[f"{kind}_items"]}
        self.assertEqual(rebuilt, expected)
        self.assertEqual([row["student"] for row in w["student_tasks"]], sorted({s for s, _, _ in expected}))
        for row in w["student_tasks"]:
            self.assertEqual(row["task_count"], len(row["lecture_items"]) + len(row["pretest_items"]))
        self.assertEqual(plan["coverage_audit"], {
            "total_points": 97, "covered_points": 44, "uncovered_points": 53,
            "official_points": 97, "live_points": 0, "unit": UNIT})
        self.assertTrue(all(p["record_id"] and p["live"] is False for p in plan["uncovered"]["lost_points"]))
        print("common budget=3: lecture_assignments=19, pretest_assignments=27, "
              "student_tasks=19, total_assignments=46")

    def test_lower_budget_keeps_three_pretest_groups(self):
        lower = self.plans["strategies"]["common"]["budgets"]["2"]
        w = lower["workload"]
        self.assertEqual((w["lecture_budget"], w["pretest_budget"]), (2, 3))
        self.assertEqual((w["lecture_questions"], w["pretest_questions"], w["total_unique_questions"]), (2, 3, 5))
        self.assertEqual(w["groups"], self.common["workload"]["groups"])
        self.assertEqual((w["lecture_assignments"], w["pretest_assignments"], w["total_assignments"]), (15, 27, 42))
        self.assertEqual(len(w["student_tasks"]), 18)
        delta = review_plan.compare_plans(self.common, lower)
        self.assertEqual((delta["before"]["total_points"], delta["after"]["total_points"]), (97, 97))
        self.assertEqual((delta["before"]["covered_points"], delta["after"]["covered_points"]), (44, 36))
        self.assertEqual(len(delta["added_points"]), 8)
        self.assertEqual({p["item"] for p in delta["added_points"]}, {"Q006"})
        self.assertEqual(delta["removed_points"], [])
        self.assertTrue(delta["assignments"]["changed"])
        self.assertEqual(delta["assignments"]["added"], [])
        self.assertEqual(delta["assignments"]["removed"], [
            {"student": s, "item_id": "Q006", "type": "lecture"} for s in ["D02", "D06", "D11", "D12"]])
        self.assertEqual(delta["assignments"]["unchanged_count"], 42)
        self.assertNotIn("观察项总数增加", delta["explanation"])
        print("common budget=2: lecture_assignments=15, pretest_assignments=27, "
              "student_tasks=18, total_assignments=42")

    def test_all_strategy_budgets_and_original_official_numbers(self):
        saved = json.loads(self.saved_plans.read_text(encoding="utf-8")) if self.saved_plans.is_file() else None
        for budget in ("2", "3"):
            for strategy in ("common", "naive", "pretest"):
                with self.subTest(strategy=strategy, budget=budget):
                    def select(plans):
                        return (plans["comparison"][budget]["naive_plan"] if strategy == "naive"
                                else plans["strategies"][strategy]["budgets"][budget])
                    plan = select(self.plans)
                    w, audit = plan["workload"], plan["coverage_audit"]
                    self.assertEqual(w["lecture_budget"], 0 if strategy == "pretest" else int(budget))
                    self.assertEqual(w["pretest_budget"], int(budget) if strategy == "pretest" else
                                     (3 if strategy == "common" else 0))
                    self.assertEqual(audit["total_points"], audit["covered_points"] + audit["uncovered_points"])
                    self.assertEqual(audit["official_points"] + audit["live_points"], 97)
                    self.assertEqual(w["total_assignments"], len(assigned_tasks(plan)))
                    if saved:
                        original = select(saved)
                        self.assertEqual(plan["covered_lost_points"], original["covered_lost_points"])
                        self.assertEqual(plan["covered_students"], original["covered_students"])
                        self.assertEqual(plan["pretest_tasks"], original["pretest_tasks"])
                        self.assertEqual([(i["item_id"], i["lost_students"]) for i in plan["lecture_items"]],
                                         [(i["item_id"], i["lost_students"]) for i in original["lecture_items"]])
                        self.assertEqual(len(plan["uncovered"]["lost_points"]), len(original["uncovered"]["lost_points"]))

    def test_mid_wrong_increases_denominator_not_student_tasks(self):
        extra = [{"student_id": "D01", "item_id": "Q009", "kind": "mid_wrong", "record_id": "现场测试"}]
        before_snapshot = copy.deepcopy(self.common)
        after = self.generate(extra)["strategies"]["common"]["budgets"]["3"]
        delta = review_plan.compare_plans(self.common, after)
        self.assertEqual(self.common, before_snapshot)
        self.assertEqual(after["coverage_audit"], {
            "total_points": 98, "covered_points": 44, "uncovered_points": 54,
            "official_points": 97, "live_points": 1, "unit": UNIT})
        self.assertEqual(delta["before"], self.common["coverage_audit"])
        self.assertEqual(delta["after"], after["coverage_audit"])
        self.assertEqual(delta["added_points"], [{
            "student": "D01", "item": "Q009", "point_id": "LIVE-MID",
            "label": "去括号/符号（现场观察）", "record_id": "现场测试", "live": True}])
        self.assertEqual(delta["removed_points"], [])
        self.assertEqual(delta["assignments"], {
            "changed": False, "added": [], "removed": [], "unchanged_count": 46})
        self.assertEqual(after["workload"], self.common["workload"])
        self.assertIn("观察项总数增加，学生任务未变化，不代表教学成效下降", delta["explanation"])

    def test_correct_only_adds_explicit_session_evidence(self):
        original = copy.deepcopy(self.base_facts)
        context_snapshot = copy.deepcopy(self.ctx)
        self.assertEqual(review_plan.build_facts(self.ctx, self.members, []), original)
        key = ("D01", self.ctx["item_ind"]["Q009"])
        self.assertEqual(original["scored_count"][key], 1)
        extra = [{"student_id": "D01", "item_id": "Q009", "kind": "correct", "record_id": "正确模拟1"}]
        current = review_plan.build_facts(self.ctx, self.members, extra)
        expected_counts = dict(original["scored_count"])
        expected_counts[key] += 1
        self.assertEqual(dict(current["scored_count"]), expected_counts)
        self.assertEqual(current["lost"], original["lost"])
        self.assertEqual(current["done"], original["done"])
        # 第二次独立正确观察才满足 D01/T01I2 的最小证据数；此时任务确实减少。
        extra.append({**extra[0], "record_id": "正确模拟2"})
        after = self.generate(extra)["strategies"]["common"]["budgets"]["3"]
        delta = review_plan.compare_plans(self.common, after)
        self.assertEqual(after["coverage_audit"], self.common["coverage_audit"])
        self.assertEqual(delta["assignments"], {
            "changed": True, "added": [],
            "removed": [{"student": "D01", "item_id": "Q003", "type": "pretest"}],
            "unchanged_count": 45})
        self.assertEqual(after["workload"]["pretest_assignments"], 26)
        self.assertEqual(review_plan.build_facts(self.ctx, self.members), original)
        self.assertEqual(self.ctx, context_snapshot)

    def test_manual_unknown_and_out_of_scope_are_ignored(self):
        for entry in (
            {"student_id": "D01", "item_id": "Q009", "kind": "manual"},
            {"student_id": "D01", "item_id": "Q009", "kind": "unknown"},
            {"student_id": "D01", "item_id": "Q009"},
            {"student_id": "D22", "item_id": "Q009", "kind": "correct"},
            {"student_id": "D01", "item_id": "NOT-IN-THEME", "kind": "correct"},
        ):
            with self.subTest(entry=entry):
                self.assertEqual(review_plan.build_facts(self.ctx, self.members, [entry]), self.base_facts)
        manual = self.generate([{"student_id": "D01", "item_id": "Q009", "kind": "manual"}])
        self.assertEqual(manual["strategies"], self.plans["strategies"])
        self.assertEqual(manual["comparison"], self.plans["comparison"])

    def test_all_wrong_kinds_are_traceable_session_observations(self):
        kinds = {
            "mid_wrong": ("LIVE-MID", "去括号/符号（现场观察）"),
            "final_wrong": ("LIVE-FINAL", "结果核对（现场观察）"),
            "combine_wrong": ("LIVE-COMBINE", "合并同类项（现场观察）"),
            "transcription_wrong": ("LIVE-TRANSCRIPTION", "过程末行与最终填答不一致（现场观察）"),
        }
        for kind, (point_id, label) in kinds.items():
            with self.subTest(kind=kind):
                extra = [{"student_id": "D01", "item_id": "Q009", "kind": kind, "record_id": "现场测试"}]
                result = review_plan.build_facts(self.ctx, self.members, extra)
                self.assertEqual(result["scored_count"], self.base_facts["scored_count"])
                self.assertEqual(result["lost"][:-1], self.base_facts["lost"])
                self.assertEqual(public_point(result["lost"][-1]), {
                    "student": "D01", "item": "Q009", "point_id": point_id,
                    "label": label, "record_id": "现场测试", "live": True})
                after = self.generate(extra)["strategies"]["common"]["budgets"]["3"]
                self.assertEqual(after["coverage_audit"]["official_points"], 97)
                self.assertEqual(after["coverage_audit"]["live_points"], 1)
                self.assertEqual(review_plan.compare_plans(self.common, after)["added_points"],
                                 [public_point(result["lost"][-1])])


if __name__ == "__main__":
    unittest.main(verbosity=2)
