"""题目知识点归属（2026-10-05）

背景（已确认的问题）：出题环节曾把同一批题目**整批**套用请求的知识点，
于是"最坏时间复杂度"的题被计入"快速排序稳定性判断"的掌握度 / 错题 / 复习。

统一归属规则（沿 出题→入库→作答证据→掌握度→错题→复习→Agent 全链路）：
1. 题目自带、且落在**允许集合**内的知识点 → 采纳；
2. 题目自带的归属不在允许集合 → **待确认**（绝不整批套用请求知识点）；
3. 没有自带归属 → **待确认**。

"待确认"的题目仍保留作答记录（判分照常），但不参与精确的知识点掌握估计，
也不据此生成该知识点的复习。本模块是纯函数，不做 LLM 调用，便于测试。
"""
from typing import Any, Dict, List, Optional, Tuple

from services.learning_data import normalize_kp_name

PENDING_LABEL = "待确认"


def is_pending(kp: Optional[str]) -> bool:
    return not (kp and str(kp).strip())


ATTRIBUTION_PROMPT = """你是严谨的命题审核员。下面有一批题目和允许的知识点。
请逐题判断：这道题**实际考查**的是哪个知识点。

## 允许的知识点
{allowed}

## 待审核的题目
{listing}

## 输出要求（严格 JSON，不要输出其他内容）
{{"assignments": [{{"index": 1, "tests": "这道题实际考查的内容（用几个词，如 时间复杂度 / 稳定性）",
  "knowledge_point": "允许列表中的知识点或 待确认"}}]}}

## 规则
1. 先写 tests（题干实际考查什么），再选 knowledge_point。
2. knowledge_point 只能取上面允许列表中的**一个**，或填"待确认"。
3. **knowledge_point 必须与 tests 对应**：若 tests 与某个知识点的核心词没有共同点，
   就不能选它。例如允许列表只有"快速排序稳定性判断"，而 tests 是"时间复杂度"，
   必须填"待确认"。
4. 不要因为同一批题放在一起就填同一个知识点。
"""


def _extract_json(text: str) -> Any:
    import json
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except Exception:
        return {}


def verify_attribution(items, allowed_kps, ask):
    """
    用一次（批量）判定把每道题归入允许的知识点，或标记为待确认。

    Args:
        items: 题目列表
        allowed_kps: 允许的知识点集合
        ask: 调用模型的可调用对象，签名 ask(prompt) -> str

    Returns:
        与 items 等长的列表，元素为允许集合内的知识点名或 None（待确认）。
    """
    items = [it for it in (items or []) if isinstance(it, dict)]
    if not items:
        return []
    allowed = [str(k).strip() for k in (allowed_kps or []) if str(k).strip()]
    norm = {}
    for k in allowed:
        n = normalize_kp_name(k)
        if n and n not in norm:
            norm[n] = k
    if not allowed:
        # 没有允许集合：只能信任题目自带的归属
        return [it.get("knowledge_point") or None for it in items]
    listing = "\n".join(
        f"{i}. {str(it.get('question') or it.get('stem') or '')[:160]}"
        for i, it in enumerate(items, 1))
    try:
        raw = ask(ATTRIBUTION_PROMPT.format(allowed="、".join(allowed), listing=listing))
    except Exception:
        # 审核失败：回退到"自带归属在集合内则采纳，否则待确认"
        out = []
        for it in items:
            key = normalize_kp_name(it.get("knowledge_point") or "")
            out.append(norm.get(key))
        return out
    data = _extract_json(raw)
    assign = data.get("assignments") if isinstance(data, dict) else data
    mapping = {}
    tests_map = {}
    for a in (assign or []):
        if not isinstance(a, dict):
            continue
        try:
            idx = int(a.get("index"))
        except (TypeError, ValueError):
            continue
        mapping[idx] = a.get("knowledge_point")
        tests_map[idx] = a.get("tests")
    out = []
    for i, it in enumerate(items, 1):
        key = normalize_kp_name(mapping.get(i) or "")
        kp = norm.get(key) if key in norm else None
        if kp and not tests_overlap(tests_map.get(i) or "", kp):
            # 审核给出的"实际考查内容"与该知识点核心词没有共同点 → 先撤回
            kp = None
        stem = str(it.get("question") or it.get("stem") or "")
        if not kp:
            # 审核写成待确认，或 tests 只用了名称末尾两个词、把"定义"这类前半段考点否掉。
            # 题目自带归属在允许集合内，且题干含该知识点后半段的区分词时，保留自带归属。
            # 不能无条件回退：题干对不上时仍是待确认，避免把"时间复杂度"算进"稳定性"。
            own_key = normalize_kp_name(it.get("knowledge_point") or "")
            own = norm.get(own_key)
            if own and stem_supports_kp(stem, own):
                kp = own
            elif key in norm and stem_supports_kp(stem, norm[key]):
                kp = norm[key]
        out.append(kp)
    return out


def stem_supports_kp(stem: str, kp_name: str) -> bool:
    """题干是否真在考这个知识点，而不是只共用了名称前缀。

    两个词以内：任一出现即可。更长的名称只用**后半段**区分词
    （"岩石力学定义与研究范畴" → 定义/研究/范畴；
     "快速排序稳定性判断" → 稳定性/判断）。
    这样"根据岩石力学的定义"能计到定义这个知识点，
    而"快速排序的最坏时间复杂度"不会因为带了"快速排序"就被算进稳定性。
    """
    text = normalize_kp_name(stem)
    if not text:
        return False
    toks = _terms(kp_name)
    if not toks:
        return False
    core = toks if len(toks) <= 2 else toks[len(toks) // 2:]
    for token in core:
        nt = normalize_kp_name(token)
        if nt and nt in text:
            return True
    return False


def tests_overlap(tests: str, kp_name: str) -> bool:
    """
    题目的"实际考查内容"是否与该知识点的**区分性核心词**有共同点。

    核心词取知识点分词后的**末两个词**（"快速排序稳定性判断" → 稳定性/判断），
    用来避开与同课程其他知识点共有的主题词（快速/排序）。审核未给出 tests 时，
    保守接受（不因审核缺字段而误判为待确认）。
    """
    tt = normalize_kp_name(tests)
    if not tt:
        return True
    toks = _terms(kp_name)
    if not toks:
        return False
    core = toks[-2:] if len(toks) >= 3 else toks
    for t in core:
        nt = normalize_kp_name(t)
        if nt and nt in tt:
            return True
        if len(t) >= 2 and normalize_kp_name(t[:2]) in tt:
            return True
    return False


def _terms(name: str) -> List[str]:
    import jieba
    return [t.strip() for t in jieba.cut(str(name or "")) if len(t.strip()) >= 2]



def resolve_items(
    items: List[Dict[str, Any]],
    allowed_kps: Optional[List[str]],
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """
    把题目按**各自**的知识点归属分组；无法可靠归属的进入 pending。

    Returns:
        (by_kp: {知识点名: [题目...]}, pending: [题目...])
    """
    allowed = [str(k).strip() for k in (allowed_kps or []) if str(k).strip()]
    norm: Dict[str, str] = {}
    for k in allowed:
        n = normalize_kp_name(k)
        if n and n not in norm:
            norm[n] = k

    by_kp: Dict[str, List[Dict[str, Any]]] = {}
    pending: List[Dict[str, Any]] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        kp = it.get("knowledge_point")
        key = normalize_kp_name(kp) if kp else ""
        if key and key in norm:
            by_kp.setdefault(norm[key], []).append(it)
        elif not allowed and key:
            # 未限定允许集合：信任模型给出的知识点
            by_kp.setdefault(str(kp).strip(), []).append(it)
        else:
            pending.append(it)
    return by_kp, pending