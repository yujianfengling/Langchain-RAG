"""
试用数据导出（去标识化）

用途：小班试用结束后，把**班级级聚合指标**导出成 CSV，供大创汇报使用。
设计原则（对应本轮要求）：

1. **默认不产生个人数据**：只导出班级级聚合（参与率、错误率、趋势），
   从源头上避免在展示材料里出现学生姓名、联系方式或个人问答。
2. 需要按学生核对时用 `--with-mapping` 单独生成"账号 ↔ 代号"文件，
   文件里只有账号名和代号，且明确标注**不要放进汇报材料**。
3. 导出前先过数据隔离闸门：后端必须跑在隔离数据目录上（否则拒绝运行），
   并在开头打印实际使用的数据目录。

用法：
    # 口令从环境变量读，避免出现在命令历史/文档里
    $env:EDU_TRIAL_PASSWORD="..."
    python tests/export_trial_data.py --username teacher1 --class-id class_xxx --out data/trial_export

    # 可选：--days 30（默认），--with-mapping
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests._console import safe_console  # noqa: E402
from tests._isolation_guard import require_backend_isolated  # noqa: E402

safe_console()


def _api(base: str, method: str, path: str, token: str = None, body=None):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(base.rstrip("/") + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-User-Token", token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"message": str(e)}


def _write_csv(path: Path, rows: list, header: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig：Excel 直接双击打开不乱码
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="试用数据导出（去标识化）")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="后端地址")
    ap.add_argument("--username", required=True, help="教师账号（需是该班级的授课教师）")
    ap.add_argument("--class-id", required=True, help="班级 ID")
    ap.add_argument("--days", type=int, default=30, help="统计天数（默认 30）")
    ap.add_argument("--out", default="data/trial_export", help="输出目录")
    ap.add_argument("--with-mapping", action="store_true",
                    help="额外生成「账号↔代号」映射（不要放进汇报材料）")
    args = ap.parse_args()

    # ---- 闸门：必须先确认后端跑在隔离数据目录上 ----
    state = require_backend_isolated(args.base, "试用数据导出（读取该目录里的班级学情）",
                                     read_only=True)
    print(f"[导出] 数据目录：{state['dir_name']}（隔离={state['isolated']}）")

    password = os.getenv("EDU_TRIAL_PASSWORD") or ""
    if not password:
        print("缺少教师账号口令：请设置环境变量 EDU_TRIAL_PASSWORD 后重试。")
        print("（口令只用于登录，不会被打印或写入导出文件）")
        return 2

    status, res = _api(args.base, "POST", "/api/auth/login",
                       body={"username": args.username, "password": password})
    if status != 200 or not (res.get("data") or {}).get("token"):
        print(f"登录失败（{status}）：{res.get('message') or res.get('detail')}")
        return 2
    token = res["data"]["token"]
    print(f"[导出] 已登录：{args.username}（角色={res['data'].get('role')}）")

    status, res = _api(args.base, "GET",
                       f"/api/teacher/classes/{args.class_id}/analytics?days={args.days}",
                       token=token)
    if status != 200:
        print(f"获取学情失败（{status}）：{res.get('message') or res.get('detail')}")
        return 2
    data = res["data"]
    if not data.get("enough_data"):
        print(f"[注意] 样本不足：{data.get('insufficient_reason')}——"
              f"导出的数字仅供参考，不要在汇报里下结论。")

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    outdir = (ROOT / args.out / stamp) if not Path(args.out).is_absolute() else Path(args.out) / stamp
    outdir.mkdir(parents=True, exist_ok=True)

    meta = data.get("meta") or {}
    cls_name = data.get("class_name") or meta.get("class_name") or args.class_id
    rng = data.get("range") or {}

    # 1) 班级级聚合（无人名）
    participation = data.get("participation") or {}
    task = data.get("task_completion") or {}
    overview_rows = [
        ["班级", data.get("class_name") or ""],
        ["课程", data.get("course_title") or ""],
        ["统计范围", f"{(rng or {}).get('start', '')} ~ {(rng or {}).get('end', '')}"],
        ["在班学生数", participation.get("student_count")],
        ["有作答记录的学生数", participation.get("answered_student_count")],
        ["学生参与率", participation.get("attempt_student_ratio")],
        ["作答题次（含重复提交）", participation.get("attempt_count")],
        ["去重作答数（学生×题目）", participation.get("distinct_question_count")],
        ["已判分数", participation.get("graded_count")],
        ["任务完成人数", task.get("completed") if task else ""],
        ["任务应完成人数", task.get("total") if task else ""],
        ["数据是否充足", "是" if data.get("enough_data") else "否（样本不足，不要下结论）"],
    ]
    _write_csv(outdir / "班级学情_去标识化.csv", overview_rows, ["指标", "数值"])

    # 2) 知识点错误率（班级级聚合，无学生姓名）
    kp_rows = []
    for kp in data.get("knowledge_points") or []:
        kp_rows.append([
            kp.get("knowledge_point"), kp.get("attempt_count"),
            kp.get("wrong_count"), kp.get("wrong_rate"),
            kp.get("student_count"), "是" if kp.get("enough_data") else "否",
        ])
    _write_csv(outdir / "知识点错误率_去标识化.csv", kp_rows,
               ["知识点", "样本量(作答次数)", "错误次数", "错误率", "涉及学生数", "数据是否充足"])

    # 3) 每日趋势
    trend_rows = [[t.get("date"), t.get("attempt_count"), t.get("wrong_count"),
                   t.get("student_count"), t.get("wrong_rate")]
                  for t in (data.get("trend") or [])]
    _write_csv(outdir / "每日作答趋势_去标识化.csv", trend_rows,
               ["日期", "作答次数", "错误次数", "参与学生数", "当日错误率"])

    # 4) 可选：账号 ↔ 代号 映射（明确标注不要外传）
    if args.with_mapping:
        status, mres = _api(args.base, "GET",
                            f"/api/teacher/classes/{args.class_id}/members", token=token)
        members = ((mres.get("data") or {}).get("members") or []) if status == 200 else []
        active = [m for m in members if (m.get("status") or "active") == "active"]
        map_rows = [[f"S{i + 1}", m.get("username") or "", m.get("user_id") or ""]
                    for i, m in enumerate(active)]
        _write_csv(outdir / "学生代号映射.csv", map_rows, ["学生代号", "账号名", "用户ID"])

    # 5) 导出说明（口径提醒，避免误读）
    (outdir / "导出说明.md").write_text(
        f"""# 试用数据导出说明

- 导出时间：{datetime.now().isoformat(timespec="seconds")}
- 后端数据目录：`{state['dir_name']}`（隔离目录，非项目正式 data/）
- 班级：{cls_name}
- 统计范围：{(rng or {}).get('start', '')} ~ {(rng or {}).get('end', '')}
- 导出账号：{args.username}

## 口径提醒（写材料前必读）

- 本目录**只有班级级聚合数据**，不含学生姓名、联系方式或个人问答原文。
- 「作答题次」是行为次数，**不是学习时长**；不要换算成"平均使用 X 分钟"。
- 数据是否充足为「否」时，任何知识点层面的结论都不成立。
- 前后测分数、满意度、使用时长**不在本导出里**，需要人工记录（见 `docs/TRIAL_PACKAGE.md` 第 8 节）。
- 真实教学效果仍以"真实学生试用"为准；自动评测与浏览器走查只证明链路可用。
""", encoding="utf-8")

    print(f"[导出] 完成 → {outdir}")
    for f in sorted(outdir.iterdir()):
        tag = "（不要放进汇报材料）" if f.name == "学生代号映射.csv" else ""
        print(f"  - {f.name}{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
