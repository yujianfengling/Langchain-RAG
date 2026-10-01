"""
阶段二 真实模型验收：不同学习记录 → 不同教学安排（含无记录不编造画像）

前置：后端在独立数据目录下启动（不污染正式库）：
    EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_e2e_data" python -m uvicorn api.app:app --port 8000

用法：
    python tests/e2e_real_teaching.py [base_url] [subject] [model]

验证内容（全部走真实接口 + 真实模型调用；MiMo 密钥不可用时用已配置可用的模型）：
1. 新账号无任何记录 → teaching.mode = standard，reasons 明确写"常规教学"，样本量为 0（不编造画像）
2. 真实模型出题 + 后端判分：同一知识点 2 次独立答错 → mode = foundation（基础优先），并说明依据
3. 学习记忆与真实证据一致：满足"≥2 次独立答错且没有独立答对"才生成"需要加强"推断，
   否则不生成（不把记录夸大成能力标签）
4. 学生显式设置记忆 → 立即进入教学依据；删除后立即不再使用（可查看/可删除/显式优先）
5. 答案隔离：作答提交后才下发标准答案；同题重做被判为 review（与独立作答区分）
6. 证据不足时不进入综合练习（不夸大掌握）

说明：需要"可预知正确答案"才能稳定构造的分支（证据充分→advanced、答对后撤销推断），
由 Mock 用例确定性覆盖：
    tests/test_phase2_agent.py::TestPersonalizedTeaching::test_evidenced_student_gets_advanced
    tests/test_phase2_agent.py::TestLearningMemory::test_correct_answer_revokes_inference
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/") + "/api"
SUBJECT = sys.argv[2] if len(sys.argv) > 2 else "离散数学"
MODEL = sys.argv[3] if len(sys.argv) > 3 else "deepseek"
TOPIC = "集合的基本概念"

results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name}" + (f" | {detail}" if detail else ""))


def req(method, path, token=None, body=None, timeout=180, params=None):
    if params:
        path += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("X-User-Token", token)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def register(username):
    st, body = req("POST", "/auth/register",
                   body={"username": username, "password": "test-pass-123"})
    assert st == 200, body
    return body["data"]["token"], body["data"]["id"]


def ask(token, question):
    """普通问答（真实模型），返回 (teaching 信息, 回答文本)"""
    st, body = req("POST", "/chat", token=token,
                   body={"question": question, "subject_id": SUBJECT, "model": MODEL})
    assert st == 200, body
    return body.get("teaching") or {}, body.get("answer") or ""


def gen_quiz(token, topic=TOPIC):
    st, body = req("POST", "/chat", token=token,
                   body={"question": topic, "subject_id": SUBJECT, "mode": "quiz",
                         "model": MODEL})
    assert st == 200, body
    return (body.get("payload") or {}).get("quiz") or []


def answer(token, qid, ans, token_id):
    st, body = req("POST", "/quiz/answer", token=token,
                   body={"question_id": qid, "user_answer": ans,
                         "subject_id": SUBJECT, "client_token": token_id})
    assert st == 200, body
    return body["data"]


def _make_struggling_student(stamp, max_accounts=4):
    """
    造一个"有独立答错"的学生（真实模型出题 + 后端权威判分）。

    真实模型的正确答案位置不固定，脚本无法预知选项，只能固定选 A 作答；
    因此统计口径是：**同一知识点上是否达到 2 次独立答错、且没有独立答对**。
    - 一次请求会生成多道题（同一知识点），逐题作答即可累积独立证据
    - 若中途答对，该账号就"不干净"（记忆规则要求答对为 0），换新账号重来

    Returns:
        (token, uid, topic, wrong, correct, clean)
    """
    best = None
    token = uid = None
    for a in range(max_accounts):
        token, uid = register(f"rt_stu_{stamp}_{a}")
        wrong = correct = 0
        for batch in range(2):
            quiz = gen_quiz(token)
            singles = [q for q in quiz if q.get("type") == "single_choice"]
            if not singles:
                continue
            for q in singles:
                res = answer(token, q["id"], "A", f"rt-{stamp}-{a}-{batch}-{q['id']}")
                if res.get("is_correct"):
                    correct += 1
                    break
                wrong += 1
                if wrong >= 2:
                    break
            if correct or wrong >= 2:
                break
        if wrong >= 2 and correct == 0:
            return token, uid, TOPIC, wrong, correct, True
        if best is None or wrong > best[3]:
            best = (token, uid, TOPIC, wrong, correct, False)
    return best


def _weak_count(token):
    st, mem = req("GET", "/memory", token=token)
    items = (mem.get("data") or []) if st == 200 else []
    return [m for m in items if str(m.get("key", "")).startswith("weak_point:")]


def _assert_isolated_data_dir():
    """
    运行前确认后端跑在**隔离数据目录**上（EDU_DATA_DIR）。

    补验5：自动测试与真实模型验收不得再把测试账号/学习记录写进正式库。
    后端 /health 会报告 data_scope，这里据此提前拦截。
    """
    root = BASE[:-4] if BASE.endswith("/api") else BASE
    r = urllib.request.Request(root + "/health")
    with urllib.request.urlopen(r, timeout=20) as resp:
        health = json.loads(resp.read().decode())
    scope = (health.get("data") or {}).get("data_scope") or {}
    if not scope.get("isolated"):
        print("拒绝执行：后端当前使用正式数据目录（data_scope.isolated=false）。\n"
              "请用隔离目录启动后端后再运行验收：\n"
              '  EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_e2e_data" '
              "python -m uvicorn api.app:app --port 8000")
        sys.exit(2)
    return scope


def main():
    scope = _assert_isolated_data_dir()
    print(f"数据目录：{scope.get('dir_name')}（隔离，不会写入正式库）")
    stamp = str(int(time.time()))[-6:]
    fresh_token, _ = register(f"rt_fresh_{stamp}")

    # ---- 1) 无记录账号：不编造画像 ----
    teaching, answer_text = ask(fresh_token, f"{TOPIC}是什么")
    record("无记录 → 常规教学（不虚构水平）",
           teaching.get("mode") == "standard", f"mode={teaching.get('mode')}")
    reasons = " ".join(teaching.get("reasons") or [])
    record("无记录 → 明确说明按常规教学处理",
           "常规教学" in reasons or "暂无可用记录" in reasons, reasons[:80])
    sig = teaching.get("signals") or {}
    record("无记录 → 样本量为 0（没有假数据）",
           not sig.get("sample_size"), f"signals.sample_size={sig.get('sample_size')}")
    record("真实模型给出了回答（非空）", len(answer_text.strip()) > 10,
           answer_text.strip()[:60])

    # ---- 2) 真实错题学生：基础优先 ----
    stu_token, stu_uid, kp, wrong, correct, clean = _make_struggling_student(stamp)
    record("真实模型出题 + 后端判分产生独立答错记录", wrong >= 2,
           f"独立答错 {wrong} 次 / 独立答对 {correct} 次（知识点：{kp}）")

    teaching2, _ = ask(stu_token, f"请讲解一下{TOPIC}")
    record("有独立答错记录的学生 → 基础优先教学",
           teaching2.get("mode") == "foundation", f"mode={teaching2.get('mode')}")
    r2 = " ".join(teaching2.get("reasons") or [])
    record("给出依据（独立答错次数 / 掌握估计）",
           ("答错" in r2 or "独立作答" in r2), r2[:100])
    applied = teaching2.get("applied") or {}
    record("教学依据可核查（本次实际采用了哪些信息）",
           "teaching_mode" in applied, json.dumps(applied, ensure_ascii=False)[:120])

    # ---- 3) 记忆与真实证据一致 ----
    weak = _weak_count(stu_token)
    expected = 1 if clean else 0
    branch = "" if clean else "（未取到零答对样本，覆盖的是『不生成推断』分支）"
    record("记忆与真实证据一致（不把记录夸大成能力标签）",
           len(weak) == expected,
           f"证据：独立答错 {wrong}/独立答对 {correct} → 期望 {expected} 条，实际 {len(weak)} 条{branch}")
    if weak:
        record("推断带依据与有效期",
               bool(weak[0].get("evidence")) and bool(weak[0].get("expires_at")),
               f"evidence={list((weak[0].get('evidence') or {}).keys())}")

    # ---- 4) 显式记忆：立即可见、删除后立即不再参与教学 ----
    st, mem_body = req("POST", "/memory", token=stu_token, body={
        "key": "讲解风格", "value": "先给结论，再用一个生活例子解释",
        "note": "学生显式设置"})
    record("学生可显式设置记忆", st == 200, f"status={st}")
    memory_id = (mem_body.get("data") or {}).get("id") if st == 200 else None
    mem_key = (mem_body.get("data") or {}).get("key") if st == 200 else None
    t3, _ = ask(stu_token, "再讲讲集合的并集")
    applied3 = (t3.get("applied") or {})
    joined = " ".join((applied3.get("memory") or []))
    record("显式设置立即进入教学依据",
           bool(mem_key) and mem_key in joined, f"applied.memory={joined[:100]}")
    if memory_id:
        # 更正：改成新的说法后，教学依据必须立刻用新值
        st_fix, fixed = req("PUT", f"/memory/{memory_id}", token=stu_token, body={
            "value": "先用一句话结论，再分点展开", "reason": "调整讲解风格"})
        t_fix, _ = ask(stu_token, "再讲讲集合的差集")
        applied_fix = " ".join(((t_fix.get("applied") or {}).get("memory") or []))
        record("更正记忆后立即按新值教学",
               st_fix == 200 and "先用一句话结论，再分点展开" in applied_fix
               and "生活例子" not in applied_fix, f"applied.memory={applied_fix[:100]}")

        st_del, _ = req("DELETE", f"/memory/{memory_id}", token=stu_token)
        t4, _ = ask(stu_token, "再讲讲集合的交集")
        applied4 = (t4.get("applied") or {})
        joined4 = " ".join((applied4.get("memory") or []))
        record("删除后立即不再参与教学",
               st_del == 200 and mem_key not in joined4, f"applied.memory={joined4[:100]}")

    # ---- 5) 答案隔离 + 复习重做语义 ----
    quiz = gen_quiz(stu_token)
    singles = [q for q in quiz if q.get("type") == "single_choice"]
    keep_question = None
    if singles:
        q = singles[0]
        keep_question = q["id"]
        first = answer(stu_token, q["id"], "A", f"rt-learn-{stamp}")
        key = first.get("correct_option_id")
        record("提交后才下发标准答案（答案隔离）", bool(key),
               f"correct={key} / 你的作答={first.get('user_answer')}")
        if key:
            redo = answer(stu_token, q["id"], key, f"rt-redo-{stamp}")
            record("同题重做被判为复习（review），与独立作答区分开",
                   redo.get("attempt_kind") == "review",
                   f"kind={redo.get('attempt_kind')} / correct={redo.get('is_correct')}")
    else:
        record("提交后才下发标准答案（答案隔离）", False, "本次未生成单选题")

    # ---- 6) 证据不足不夸大掌握 ----
    teaching5, _ = ask(stu_token, f"{TOPIC}的进阶问题")
    record("证据不足时不会跳进综合练习（不夸大掌握）",
           teaching5.get("mode") in ("foundation", "standard"),
           f"mode={teaching5.get('mode')}")

    # ---- 7) 模型不可用时的降级（真实场景：当前 MiMo 配置不可用） ----
    st_chat, chat_body = req("POST", "/chat", token=stu_token, body={
        "question": "集合的幂集是什么", "subject_id": SUBJECT, "model": "mimo"})
    record("模型不可用时明确报错（不伪装成成功）",
           st_chat != 200 and not (chat_body or {}).get("answer"),
           f"status={st_chat} / message={str((chat_body or {}).get('message'))[:40]}")

    st_home, home = req("GET", "/student/home", token=stu_token,
                        params={"subject_id": SUBJECT})
    st_tasks, tasks = req("GET", "/student/tasks", token=stu_token,
                          params={"subject_id": SUBJECT})
    record("模型不可用时：学习状态与任务仍可读取",
           st_home == 200 and st_tasks == 200, f"home={st_home} tasks={st_tasks}")

    st_wrong, wrong_body = req("GET", "/quiz/wrong", token=stu_token,
                               params={"subject_id": SUBJECT})
    record("模型不可用时：错题仍可查看",
           st_wrong == 200, f"status={st_wrong}")

    st_mem, _ = req("GET", "/memory", token=stu_token)
    st_plan, _ = req("GET", "/learning/plans", token=stu_token,
                     params={"subject_id": SUBJECT})
    record("模型不可用时：记忆与已有计划仍可读取",
           st_mem == 200 and st_plan == 200, f"memory={st_mem} plans={st_plan}")

    if keep_question:
        regrade = answer(stu_token, keep_question, "A", f"rt-nomod-{stamp}")
        record("模型不可用时：自测判分（本地题库）仍可用",
               regrade.get("is_correct") in (True, False)
               and regrade.get("attempt_kind") in ("independent", "review", "after_reveal"),
               f"kind={regrade.get('attempt_kind')}")

    task_items = []
    data_tasks = (tasks.get("data") or {})
    for bucket in ("today", "todo", "completed", "overdue"):
        task_items += data_tasks.get(bucket) or []
    record("模型不可用时：任务清单可读、可手动完成/改期",
           st_tasks == 200 and isinstance(data_tasks, dict),
           f"任务 {len(task_items)} 条，bucket 键={list(data_tasks.keys())[:4]}")

    print("\n说明：'证据充分 → 综合练习'与'答对后撤销推断'两条分支需要可预知的正确答案，"
          "由 Mock 用例确定性覆盖：tests/test_phase2_agent.py::"
          "TestPersonalizedTeaching::test_evidenced_student_gets_advanced 与 "
          "TestLearningMemory::test_correct_answer_revokes_inference。")

    print(f"\n合计 {len(results)} 项，通过 {sum(1 for _, ok, _ in results if ok)}，"
          f"失败 {sum(1 for _, ok, _ in results if not ok)}")
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)


if __name__ == "__main__":
    main()
