"""
演示数据准备（一次性、可重复运行）

用途：演示/验收前，跑一条命令把"老师 + 班级 + 演示学生 + 一份讲义 + 一道作业"准备好，
避免现场手动建十几个账号。

**命名规则（对应"演示数据必须明确标注"的要求）**：
- 课程名带「(演示数据)」后缀
- 演示账号统一 `demo_` 前缀
- 演示结束后直接删掉隔离数据目录即可，不会污染正式库

安全：
- 启动前过数据隔离闸门：后端必须跑在**隔离数据目录**上，否则拒绝运行（退出码 2）；
- 口令从环境变量 `EDU_DEMO_PASSWORD` 读取，不写在命令行、不打印明文以外的内容；
- 脚本不修改任何已有账号的角色，只新建 `demo_*` 账号。

用法：
    $env:EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_demo_data"
    $env:ADMIN_USERS="demo_admin"
    python -m uvicorn api.app:app --host 127.0.0.1 --port 8000

    $env:EDU_DEMO_PASSWORD="demo-pass-123"
    python tests/seed_demo_data.py --base http://127.0.0.1:8000 --students 3
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

from tests._console import safe_console  # noqa: E402
from tests._isolation_guard import require_backend_isolated  # noqa: E402

safe_console()

DEMO_COURSE_TITLE = "数据结构与算法（演示数据）"
DEMO_CLASS_NAME = "演示班（演示数据）"
DEMO_ASSIGNMENT_TITLE = "演示练习：二叉树（演示数据）"
ADMIN_NAME = "demo_admin"

DEMO_MATERIAL = """# 二叉树（演示讲义）

## 定义
二叉树是每个结点最多有两个子树的树结构，子树有左右之分。

## 三种遍历
- 前序遍历：根 → 左 → 右
- 中序遍历：左 → 根 → 右
- 后序遍历：左 → 右 → 根

## 二叉搜索树
对任意结点，左子树所有结点的值小于它，右子树所有结点的值大于它。
平均情况下查找、插入、删除的时间复杂度为 O(log n)；退化成链表时是 O(n)。
"""


def _api(base, method, path, token=None, body=None):
    req = urllib.request.Request(base.rstrip("/") + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-User-Token", token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=180) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"message": str(e)}


def _ensure_user(base, username, password):
    """注册；已存在则直接登录（脚本可重复运行）"""
    status, res = _api(base, "POST", "/api/auth/register",
                       body={"username": username, "password": password})
    if status == 200:
        return res["data"]["token"], "created"
    status, res = _api(base, "POST", "/api/auth/login",
                       body={"username": username, "password": password})
    if status == 200:
        return res["data"]["token"], "existed"
    raise SystemExit(f"账号 {username} 无法创建也无法登录：{res.get('message') or res}")


def main() -> int:
    ap = argparse.ArgumentParser(description="准备演示数据（新建 demo_* 账号与演示班级）")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--students", type=int, default=3, help="演示学生数量（默认 3）")
    args = ap.parse_args()

    # 闸门：本脚本只通过 HTTP 写数据，落在**后端**的数据目录上，
    # 所以判定的是后端的隔离状态（而不是当前 shell 里的 EDU_DATA_DIR）。
    scope = require_backend_isolated(
        args.base, "准备演示数据（会新建账号、课程、班级、资料与作业）")
    print(f"[演示数据] 后端数据目录：{scope.get('dir_name')}（隔离）")

    password = os.getenv("EDU_DEMO_PASSWORD") or ""
    if not password:
        print("请先设置环境变量 EDU_DEMO_PASSWORD（演示用口令，脚本不会写进任何文件）")
        return 2

    print(f"[演示数据] 后端：{args.base}")
    admin_token, how = _ensure_user(args.base, ADMIN_NAME, password)
    print(f"[演示数据] 管理员 {ADMIN_NAME}（{how}）—— 需在后端 ADMIN_USERS 白名单中")

    # 1) 教师：用管理员签发的教师邀请码开通（走真实受控通道，而不是直接改库）
    teacher_name = "demo_teacher"
    teacher_token, _ = _ensure_user(args.base, teacher_name, password)
    status, res = _api(args.base, "POST", "/api/teacher/invites",
                       token=admin_token, body={"max_uses": 1})
    if status != 200:
        print(f"签发教师邀请码失败：{res.get('message') or res}")
        return 2
    invite = res["data"]["code"]
    status, res = _api(args.base, "POST", "/api/teacher/apply-invite",
                       token=teacher_token, body={"code": invite})
    role = (res.get("data") or {}).get("role") if status == 200 else None
    print(f"[演示数据] 教师 {teacher_name}：凭邀请码开通 → {role or res.get('message')}")

    # 2) 课程
    status, res = _api(args.base, "GET", "/api/teacher/courses", token=teacher_token)
    courses = (res.get("data") or {}).get("courses") or []
    course = next((c for c in courses if c["title"] == DEMO_COURSE_TITLE), None)
    if not course:
        status, res = _api(args.base, "POST", "/api/teacher/courses", token=teacher_token,
                           body={"title": DEMO_COURSE_TITLE,
                                 "description": "课堂演示用课程，**不是真实教学数据**"})
        if status != 200:
            print(f"建课失败：{res.get('message') or res}")
            return 2
        course = res["data"]
        print(f"[演示数据] 课程已创建：{course['title']}")
    else:
        print(f"[演示数据] 课程已存在：{course['title']}")
    course_id = course["id"]

    # 3) 班级
    status, res = _api(args.base, "GET", f"/api/teacher/courses/{course_id}/classes",
                       token=teacher_token)
    classes = (res.get("data") or {}).get("classes") or []
    klass = next((c for c in classes if c["name"] == DEMO_CLASS_NAME), None)
    if not klass:
        status, res = _api(args.base, "POST", f"/api/teacher/courses/{course_id}/classes",
                           token=teacher_token, body={"name": DEMO_CLASS_NAME})
        if status != 200:
            print(f"建班失败：{res.get('message') or res}")
            return 2
        klass = res["data"]
    else:
        # 拿最新的有效邀请码
        status, res = _api(args.base, "GET",
                           f"/api/teacher/classes/{klass['id']}/join-code", token=teacher_token)
        if status == 200:
            klass["join_code"] = (res.get("data") or {}).get("join_code", klass.get("join_code"))
    join_code = klass.get("join_code")
    print(f"[演示数据] 班级：{klass['name']}，班级邀请码：{join_code}")

    # 4) 讲义（上传后后端会自动构建课程知识库）
    status, res = _api(args.base, "POST",
                       f"/api/teacher/courses/{course_id}/materials?visibility=students",
                       token=teacher_token)
    mat_path = ROOT / "tests" / "_demo_material.md"
    mat_path.write_text(DEMO_MATERIAL, encoding="utf-8")
    try:
        import mimetypes
        import uuid
        boundary = "----demo" + uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="二叉树演示讲义.md"\r\n'
            f"Content-Type: text/markdown\r\n\r\n"
        ).encode() + DEMO_MATERIAL.encode("utf-8") + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            args.base.rstrip("/")
            + f"/api/teacher/courses/{course_id}/materials?visibility=students", method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("X-User-Token", teacher_token)
        with urllib.request.urlopen(req, body, timeout=300) as r:
            mres = json.loads(r.read().decode("utf-8"))
        kb = (mres.get("data") or {}).get("kb") or {}
        print(f"[演示数据] 讲义已上传；课程知识库状态：{kb.get('stats', {}).get('status')}"
              f"（收录 {kb.get('document_count')} 份）")
    except urllib.error.HTTPError as e:
        print(f"上传讲义失败（HTTP {e.code}）：{e.read().decode('utf-8', 'ignore')[:200]}")
    finally:
        mat_path.unlink(missing_ok=True)

    # 5) 学生：注册并入班
    student_names = []
    for i in range(1, max(1, args.students) + 1):
        name = f"demo_s{i}"
        tok, _ = _ensure_user(args.base, name, password)
        status, res = _api(args.base, "POST", "/api/student/courses/join", token=tok,
                           body={"code": join_code})
        ok = status == 200 or (res.get("data") or {}).get("already_member")
        student_names.append(name)
        print(f"[演示数据] 学生 {name}：入班 {'成功' if ok else '失败：' + str(res.get('message'))}")

    # 6) 作业（草稿 + AI 出题挂上去；出题需要模型可用）
    status, res = _api(args.base, "GET", f"/api/teacher/courses/{course_id}/assignments",
                       token=teacher_token)
    existing = (res.get("data") or {}).get("assignments") or []
    asg = next((a for a in existing if a["title"] == DEMO_ASSIGNMENT_TITLE), None)
    if not asg:
        status, res = _api(args.base, "POST", f"/api/teacher/courses/{course_id}/assignments",
                           token=teacher_token,
                           body={"class_id": klass["id"], "title": DEMO_ASSIGNMENT_TITLE,
                                 "description": "演示用练习，不是真实作业",
                                 "assign_type": "quiz", "estimated_minutes": 15})
        if status == 200:
            asg = res["data"]
        else:
            print(f"建作业失败：{res.get('message') or res}")
    if asg and asg.get("status") == "draft":
        status, res = _api(args.base, "POST",
                           f"/api/teacher/courses/{course_id}/assignments/generate-questions",
                           token=teacher_token,
                           body={"assignment_id": asg["id"], "count": 3})
        if status == 200:
            print(f"[演示数据] AI 出题成功：{res['data'].get('generated')} 道")
            status, res = _api(args.base, "POST",
                               f"/api/teacher/assignments/{asg['id']}/publish",
                               token=teacher_token)
            print(f"[演示数据] 发布作业：{'成功' if status == 200 else res.get('message')}")
        else:
            print(f"[演示数据] AI 出题失败（模型不可用？）：{res.get('message') or res}")

    print("\n===== 演示账号（口令统一为 EDU_DEMO_PASSWORD 的值，本脚本不打印明文）=====")
    print(f"  管理员：{ADMIN_NAME}（需在后端 ADMIN_USERS 中）")
    print(f"  教师  ：{teacher_name}")
    print(f"  学生  ：{', '.join(student_names)}")
    print(f"  课程  ：{DEMO_COURSE_TITLE}   班级邀请码：{join_code}")
    print("\n提醒：以上全部是**演示数据**，课程名已带「(演示数据)」标注；"
          "演示结束后删除隔离数据目录即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
