"""Build an isolated private evaluation dataset from the five contest CSVs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import analysis
import bundle
import export_package
import pipeline
import review_plan


def import_dataset(csv_dir: Path, output: Path) -> None:
    csv_dir, output = csv_dir.resolve(), output.resolve()
    if not csv_dir.is_dir():
        raise ValueError("CSV 输入目录不存在")
    if output.exists():
        raise ValueError("请指定一个尚未存在的输出目录，避免覆盖已有数据")
    repo = Path(__file__).resolve().parent.parent
    if output == repo or repo in output.parents:
        raise ValueError("评测输出须位于仓库外的私有目录")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".competition-import-", dir=output.parent) as temp:
        work = Path(temp) / "dataset"
        src = work / "analysis-source"
        mapped = src / "analysis"
        app = work / "bundle"
        result = pipeline.run(csv_dir, src)
        if not result["counts"].get("students"):
            raise ValueError("名册为空，无法生成评测数据")
        analysis.run(src, csv_dir, mapped)
        manifest = bundle.build(src, csv_dir, app, source_label="official")
        manifest["independentEvaluation"] = True
        (app / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        students = json.loads((app / "students.json").read_text(encoding="utf-8"))
        quizzes = json.loads((app / "quizzes.json").read_text(encoding="utf-8"))
        classes = json.loads((app / "classes.json").read_text(encoding="utf-8"))
        if not students or not quizzes or not classes or not quizzes[0]["topicIds"]:
            raise ValueError("独立评测需要有效的学生、班级、测验和主题范围")
        node, class_id, theme = quizzes[0]["id"], classes[0]["id"], quizzes[0]["topicIds"][0]
        members = [s["id"] for s in students if s["classId"] == class_id]
        plan = review_plan.generate(src, mapped, members, node, class_id, theme)
        serialized = json.dumps(plan, ensure_ascii=False, indent=2)
        (src / "review_plans.json").write_text(serialized, encoding="utf-8")
        (app / "review_plans.json").write_text(serialized, encoding="utf-8")
        export_package.build(src, mapped, work / "deliverable")
        work.rename(output)
    print(f"[导入完成] {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="导入独立评测五张 CSV，生成私有页面数据和成果包")
    parser.add_argument("--csv-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        import_dataset(args.csv_dir, args.out)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"导入失败：{exc}\n")


if __name__ == "__main__":
    main()
