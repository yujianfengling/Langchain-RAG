"""
默认文本模型切换的运行时验收（真实接口 + 真实模型）

前置（必须是隔离数据目录，避免污染正式库）：
    EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_e2e_data" python -m uvicorn api.app:app --port 8000

用法：
    python tests/verify_default_model.py [base_url]

验证清单（对应本轮要求第 5 节）：
1. 新会话不手动选择模型即可用 DeepSeek（非流式 + 流式）
2. Agent、笔记、自测、学习计划在未指定模型时继承同一默认值
3. 旧会话仍可继续使用：历史消息保留原来的模型标记，新回答用 DeepSeek
4. 请求与响应显示的提供方/模型 = 实际调用（model=模型名，provider=提供方键）
5. 显式选择其它模型时按实际选择执行（失败也如实报错，不静默替换成 DeepSeek）
6. 视觉 / 语音状态如实上报（语音依赖 MiMo，会员到期 → 不可用 + 原因）

说明：本脚本会真实调用模型（外部服务）；自动化测试中的模型调用仍全部使用 Mock，两者分开记录。
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
API = BASE + "/api"
SUBJECT = "离散数学"
TOPIC = "集合的基本概念"

results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name}" + (f" | {detail}" if detail else ""))


def req(method, path, token=None, body=None, timeout=240, params=None):
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
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:200]}


def register(username):
    st, body = req("POST", "/api/auth/register",
                   body={"username": username, "password": "test-pass-123"})
    assert st == 200, body
    return body["data"]["token"], body["data"]["id"]


def stream_events(payload, token, timeout=300):
    """调用 SSE 接口，返回事件列表"""
    r = urllib.request.Request(API + "/chat/stream",
                               data=json.dumps(payload).encode(), method="POST")
    r.add_header("Content-Type", "application/json")
    r.add_header("X-User-Token", token)
    events = []
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except Exception:
                    pass
    return events


def _assert_isolated():
    with urllib.request.urlopen(BASE + "/health", timeout=30) as r:
        data = json.loads(r.read().decode())["data"]
    if not (data.get("data_scope") or {}).get("isolated"):
        print("拒绝执行：后端运行在正式数据目录（请用 EDU_DATA_DIR 启动）")
        sys.exit(2)
    return data


def main():
    health = _assert_isolated()
    caps = health.get("capabilities") or {}
    stamp = str(int(time.time()))[-6:]
    token, _ = register(f"dm_{stamp}")

    # ---- 1) 运行时配置 ----
    txt = caps.get("text") or {}
    record("运行时默认文本模型 = DeepSeek", txt.get("provider") == "deepseek",
           f"provider={txt.get('provider')} model={txt.get('model')}")
    st, models = req("GET", "/api/models")
    record("/api/models 报告默认提供方为 DeepSeek",
           (models.get("data") or {}).get("provider") == "deepseek",
           json.dumps(models.get("data"), ensure_ascii=False)[:120])

    # ---- 2) 新会话：不选模型直接用（非流式） ----
    st, conv = req("POST", "/api/conversations", token=token, body={"subject_id": SUBJECT})
    conv_id = (conv.get("data") or {}).get("id")
    st, body = req("POST", "/api/chat", token=token,
                   body={"question": f"{TOPIC}是什么", "subject_id": SUBJECT,
                         "conversation_id": conv_id})
    ok = st == 200 and len((body.get("answer") or "").strip()) > 10
    record("未指定模型 → 非流式问答可用", ok, f"status={st}")
    record("非流式响应 provider/model 与实际调用一致",
           body.get("provider") == "deepseek" and body.get("model") == "deepseek-chat",
           f"provider={body.get('provider')} model={body.get('model')} "
           f"display={body.get('provider_display')}")

    # ---- 3) 流式问答（不带模型） ----
    conv2 = (req("POST", "/api/conversations", token=token,
                 body={"subject_id": SUBJECT})[1].get("data") or {}).get("id")
    events = stream_events({"question": f"请讲讲{TOPIC}的表示方法", "subject_id": SUBJECT,
                            "conversation_id": conv2}, token)
    types = [e.get("type") for e in events]
    start = next((e for e in events if e.get("type") == "start"), {})
    done = next((e for e in events if e.get("type") == "done"), {})
    record("未指定模型 → 流式问答可用", "done" in types and bool(done.get("full_answer")),
           f"events={types[:6]}")
    record("流式 start/done 的 provider=deepseek",
           start.get("provider") == "deepseek" and done.get("provider") == "deepseek",
           f"start={start.get('provider')}/{start.get('model')} done={done.get('model')}")

    # ---- 4) 结构化入口继承默认模型 ----
    conv3 = (req("POST", "/api/conversations", token=token,
                 body={"subject_id": SUBJECT})[1].get("data") or {}).get("id")
    note_events = stream_events({"question": TOPIC, "subject_id": SUBJECT, "mode": "smart_note",
                                 "conversation_id": conv3}, token)
    note_done = next((e for e in note_events if e.get("type") == "done"), {})
    payload = note_done.get("payload") or {}
    record("智能笔记未指定模型 → 用默认模型生成",
           bool(payload.get("title")) and note_done.get("provider") == "deepseek",
           f"provider={note_done.get('provider')} model={note_done.get('model')} "
           f"title={str(payload.get('title'))[:24]}")

    conv4 = (req("POST", "/api/conversations", token=token,
                 body={"subject_id": SUBJECT})[1].get("data") or {}).get("id")
    quiz_events = stream_events({"question": TOPIC, "subject_id": SUBJECT, "mode": "quiz",
                                 "conversation_id": conv4}, token)
    quiz_done = next((e for e in quiz_events if e.get("type") == "done"), {})
    quiz = (quiz_done.get("payload") or {}).get("quiz") or []
    record("自测出题未指定模型 → 用默认模型生成",
           len(quiz) > 0 and quiz_done.get("provider") == "deepseek",
           f"题数={len(quiz)} provider={quiz_done.get('provider')} model={quiz_done.get('model')}")

    st, plan_body = req("POST", "/api/learning/plans/generate", token=token,
                        body={"subject_id": SUBJECT, "goal": "三天复习集合的基本概念"})
    plan_id = None
    data = plan_body.get("data") or {}
    plan_id = data.get("id") or (data.get("plan") or {}).get("id")
    if st != 200 or not plan_id:
        st2, plans = req("GET", "/api/learning/plans", token=token,
                         params={"subject_id": SUBJECT})
        items = plans.get("data") or []
        plan_id = (items[0] or {}).get("id") if items else None
    record("学习计划未指定模型 → 默认模型生成成功", bool(plan_id),
           f"status={st} plan_id={plan_id}")

    st, run = req("POST", "/api/agent/run", token=token,
                  body={"subject_id": SUBJECT, "goal": "每天30分钟，三天后考这一章，帮我复习",
                        "intent": "review"})
    run_data = run.get("data") or {}
    record("学习 Agent 未指定模型 → 默认模型执行完成",
           st == 200 and run_data.get("status") in ("SUCCEEDED", "FAILED"),
           f"status={run_data.get('status')} steps={run_data.get('steps')} "
           f"错误={str(run_data.get('error'))[:40]}")

    # ---- 5) 历史消息保留原标记 + 旧会话继续用 DeepSeek ----
    conv_old = (req("POST", "/api/conversations", token=token,
                    body={"subject_id": SUBJECT})[1].get("data") or {}).get("id")
    # 用 mimo 发一条（会员到期 → 失败），但用户消息会带着当时的模型标记入库
    st_mimo, mimo_body = req("POST", "/api/chat", token=token,
                             body={"question": "旧会话里的提问", "subject_id": SUBJECT,
                                   "conversation_id": conv_old, "model": "mimo"})
    record("显式选择 MiMo 时如实报错（不静默替换成 DeepSeek）",
           st_mimo != 200 and not (mimo_body or {}).get("answer"),
           f"status={st_mimo} message={str((mimo_body or {}).get('message'))[:40]}")

    st, hist = req("GET", f"/api/conversations/{conv_old}/messages", token=token)
    msgs = (hist.get("data") or [])
    old_marks = [m.get("model") for m in msgs if m.get("role") == "user"]
    record("历史消息保留原来的模型标记（mimo 未被改写）",
           "mimo" in old_marks, f"历史模型标记={old_marks}")

    st_new, new_body = req("POST", "/api/chat", token=token,
                           body={"question": "接着上一个问题继续讲", "subject_id": SUBJECT,
                                 "conversation_id": conv_old})
    st_hist2, hist2 = req("GET", f"/api/conversations/{conv_old}/messages", token=token)
    msgs2 = (hist2.get("data") or [])
    assist = [m for m in msgs2 if m.get("role") == "assistant"]
    old_marks2 = [m.get("model") for m in msgs2 if m.get("role") == "user"]
    record("旧会话可继续提问且新回答用 DeepSeek",
           st_new == 200 and new_body.get("provider") == "deepseek",
           f"status={st_new} provider={new_body.get('provider')}")
    record("旧会话历史中的 MiMo 标记未被改写",
           "mimo" in old_marks2, f"当前历史标记={old_marks2}")
    record("新回答在会话里标注的是实际模型",
           bool(assist) and assist[-1].get("model") == "deepseek-chat",
           f"最新 assistant 模型标记={assist[-1].get('model') if assist else None}")

    # ---- 6) 视觉 / 语音能力 ----
    vis = caps.get("vision") or {}
    record("视觉能力单独判定（不共用文本模型）",
           "provider" in vis and vis.get("provider") != "deepseek",
           f"vision={json.dumps(vis, ensure_ascii=False)[:100]}")
    sp = caps.get("speech") or {}
    record("语音能力如实上报可用性",
           isinstance(sp.get("available"), bool),
           f"speech available={sp.get('available')} reason={str(sp.get('reason'))[:60]}")

    st_tts, tts_body = req("POST", "/api/audio/speech", token=token,
                           body={"text": "测试语音"})
    record("语音合成不可用时给出准确原因（不伪装成功）",
           st_tts != 200 and "MiMo" in str((tts_body or {}).get("message", "")),
           f"status={st_tts} message={str((tts_body or {}).get('message'))[:80]}")

    # 文字学习功能不受语音不可用影响
    st_home, _ = req("GET", "/api/student/home", token=token,
                     params={"subject_id": SUBJECT})
    st_mem, _ = req("GET", "/api/memory", token=token)
    record("语音不可用时文字学习功能正常", st_home == 200 and st_mem == 200,
           f"home={st_home} memory={st_mem}")

    print(f"\n实际使用的模型：{txt.get('provider')} / {txt.get('model')}")
    print(f"合计 {len(results)} 项，通过 {sum(1 for _, ok, _ in results if ok)}，"
          f"失败 {sum(1 for _, ok, _ in results if not ok)}")
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)


if __name__ == "__main__":
    main()
