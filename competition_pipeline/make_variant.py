"""
智慧学伴比赛版 · 自造变体（工程验证，不是官方数据）
============================================================
按同结构生成一组变体 CSV：换班级编号、学生编号、修订版本，
用于验证 pipeline / bundle / analysis 换输入仍可重跑。
变体放在本地私有目录，不进 Git，不解读为教学成效。
"""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path

RENAME_CLASS = {"DC01": "XC01", "DC02": "XC02"}
RENAME_STUDENT = {f"D{i:02d}": f"V{i:02d}" for i in range(1, 41)}


def transform_cell(value: str, header: str) -> str:
    for k, v in RENAME_CLASS.items():
        value = value.replace(k, v)
    # 学生编号整值或事件编号内的学生段都要换；按最长优先，避免 D1 误匹配 D10
    for k in sorted(RENAME_STUDENT, key=len, reverse=True):
        value = value.replace(k, RENAME_STUDENT[k])
    if header == "修订版本" and value.isdigit():
        return str(int(value) + 10)  # 版本号整体上移，检验归并取最大值而非固定 1/2
    return value


def build(csv_dir: Path, out: Path) -> None:
    csv_dir, out = Path(csv_dir), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for src in sorted(csv_dir.glob("*.csv")):
        with src.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            rows = list(reader)
        header = rows[0]
        with (out / src.name).open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            for row in rows[1:]:
                w.writerow([transform_cell(c, h) for c, h in zip(row, header)])
    # 字段说明原样复制（结构未变）
    spec = csv_dir / "字段说明.csv"
    if spec.is_file():
        shutil.copy(spec, out / "字段说明.csv")
    print(f"[variant] {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成自造变体 CSV")
    ap.add_argument("--csv-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    build(Path(a.csv_dir), Path(a.out))


if __name__ == "__main__":
    main()
