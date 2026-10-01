"""
朗读文本处理（2026-09-26：本地语音升级）

职责：
1. 把教学回答整理成"适合朗读"的文本：Markdown / 代码块 / 链接 / 公式 / 表格
2. 按句子切分成短段（供逐句合成、顺序播放），**绝不丢字**
3. 复杂公式、表格不做逐字符朗读，改为"请查看屏幕"类的可读提示

设计要点（对应本轮要求）：
- 修掉旧实现的两个"静默截断"：
  * `audio_service.prepare_tts_text(max_chars=...)` 在边界处砍掉剩余文字
  * 前端 `splitTtsSegments` 对超长句子直接 `slice(0, max)`
  现在切分只做"分段"，不丢内容；确实需要截断时由调用方显式决定并拿到 `truncated` 标记。
- 句子边界覆盖中英文标点；数字、单位、英文缩写保持原样（不拆开小数点、不在缩写处断句）。
"""

import re
from typing import Dict, List, Optional

# 段落/句子边界后的"软换行"上限：单段最长字符数（超过则在次级边界再切）
DEFAULT_MAX_SEGMENT_CHARS = 180
# 单段最短字符数（相邻短句会合并，避免频繁请求）
DEFAULT_MIN_SEGMENT_CHARS = 20
# 超过该长度的"单句"才在次级边界（逗号等）处再切
HARD_SPLIT_THRESHOLD = 220

# ---------- 正则 ----------

_MD_FENCE_RE = re.compile(r"```.*?```", re.S)
_CODE_INLINE_RE = re.compile(r"`([^`]+)`")
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_MD_QUOTE_RE = re.compile(r"^\s{0,3}>\s?", re.M)
_MD_LIST_RE = re.compile(r"^\s{0,3}([-*+]|\d+[.)])\s+", re.M)
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:\-|]+\|[\s:\-|]*$", re.M)
_MD_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.M)
_MD_HR_RE = re.compile(r"^\s*([-*_])\1{2,}\s*$", re.M)
_MD_INLINE_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|~~)")
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u2190-\u21FF\u2B00-\u2BFF]"
)
# 复杂公式：LaTeX 命令、上下标、矩阵、行间公式
_LATEX_RE = re.compile(r"\$\$[^$]*\$\$|\$[^$\n]+\$")
_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+(\{[^{}]*\})*")
# 上下标/数学符号密集的片段（如 a_1^2 ≤ b² 之类）
_MATHY_RE = re.compile(r"[\^_]\{?[^}\s]{1,12}\}?|[≤≥≠≈∑∏∫√∞∈∪∩→]")
# 内部 UI 信息（不应朗读）
_UI_INFO_RE = re.compile(
    r"(模型|Model)\s*[:：]\s*\S+|首字响应\s*[:：]?\s*[\d.]+\s*s|总耗时\s*[:：]?\s*[\d.]+\s*s"
    r"|（?\s*来源\s*[:：][^）\n]*）?|参考来源\s*[:：]\s*[^\n]*"
)
# \u884c\u5185\u5f15\u7528\u6807\u8bb0\uff08[1] / [1,2] / [1-3] / \u30101\u3011\uff09\uff1a\u662f\u5c4f\u5e55\u4e0a\u7684\u6765\u6e90\u5e8f\u53f7\uff0c\u4e0d\u5e94\u6717\u8bfb
# \uff08\u82e5\u4e0d\u5904\u7406\uff0c\u6570\u5b57\u5f52\u4e00\u5316\u4f1a\u628a [1] \u53d8\u6210 [\u4e00]\uff0c\u542c\u4f17\u4f1a\u542c\u5230\u4e00\u4e2a\u5b64\u96f6\u96f6\u7684"\u4e00"\uff09
_CITATION_RE = re.compile(r"[\[\u3010]\s*\d+(?:\s*[,\-\u3001~]\s*\d+)*\s*[\]\u3011]")
_WS_RE = re.compile(r"[ \t\u3000]{2,}")

FORMULA_HINT = "这里有一个公式，请看屏幕上的公式。"
TABLE_HINT = "这里有一张表格，请看屏幕上的表格。"
CODE_HINT = "这里有一段代码，请看屏幕上的代码。"


def strip_markdown(text: str) -> str:
    """去掉 Markdown 结构，保留可朗读的文字（不丢正文）"""
    if not text:
        return ""
    out = text
    out = _MD_FENCE_RE.sub(CODE_HINT, out)              # 代码块 → 提示
    out = _MD_IMAGE_RE.sub("", out)
    out = _MD_LINK_RE.sub(r"\1", out)                    # 链接保留文字
    out = _CITATION_RE.sub("", out)                      # 行内引用标记 [1] 不朗读
    out = _URL_RE.sub("", out)
    out = _MD_HR_RE.sub("\n", out)
    # 表格：整块替换成提示（表格逐格朗读体验很差）
    lines = out.splitlines()
    result_lines: List[str] = []
    table_buf: List[str] = []
    for line in lines:
        if _MD_TABLE_ROW_RE.match(line) or _MD_TABLE_SEP_RE.match(line):
            table_buf.append(line)
            continue
        if table_buf:
            result_lines.append(TABLE_HINT)
            table_buf = []
        result_lines.append(line)
    if table_buf:
        result_lines.append(TABLE_HINT)
    out = "\n".join(result_lines)
    out = _MD_HEADING_RE.sub("", out)
    out = _MD_QUOTE_RE.sub("", out)
    out = _MD_LIST_RE.sub("", out)
    out = _MD_INLINE_EMPHASIS_RE.sub("", out)
    out = _CODE_INLINE_RE.sub(r"\1", out)
    return out


def normalize_math(text: str) -> str:
    """
    公式处理：能读的保留（如 x 等于 3），不能可靠朗读的替换为提示

    原则：宁可提示"请看屏幕"，也不要把公式读成莫名其妙的内容。
    """
    if not text:
        return ""
    # 行间/行内 LaTeX 整体替换（$$...$$ 与 $...$）
    out = _LATEX_RE.sub(FORMULA_HINT, text)
    # 残留的 LaTeX 命令（\frac{..}{..} 等）
    out = _LATEX_CMD_RE.sub(FORMULA_HINT, out)
    # 上下标/数学符号：仅当一句里出现多次时才判定为"公式句"
    def _maybe_hint(sentence: str) -> str:
        marks = _MATHY_RE.findall(sentence)
        if len(marks) >= 2:
            # 保留中文解释部分，把符号密集片段替换为提示（不整句丢弃）
            return _MATHY_RE.sub("", sentence).strip() + " " + FORMULA_HINT
        return sentence
    out = re.sub(r"[^。！？\n]+", lambda m: _maybe_hint(m.group(0)), out)
    # 合并重复提示
    out = re.sub(rf"({re.escape(FORMULA_HINT)}\s*){{2,}}", FORMULA_HINT, out)
    return out


# ---------- 数字 / 单位 / 缩写的朗读归一化 ----------
#
# 背景（实测）：CosyVoice-300M-SFT 的文本正则化前端（wetext）在缺依赖/下载失败时会静默失效
# （日志 "no frontend is available"），此时 "3.14 倍" 会被当作 "3。14倍" 读成乱码。
# 与其依赖模型侧前端，不如在业务侧做一层可控、可测的归一化（本模块）。

_CN_DIGITS = "零一二三四五六七八九"
_CN_UNITS = ["", "十", "百", "千", "万", "十万", "百万", "千万", "亿"]

# 单位替换表（按长度降序匹配，避免 GB 被 G 抢先）
_UNIT_MAP = [
    ("GB/s", "G B 每秒"), ("MB/s", "M B 每秒"), ("KB/s", "K B 每秒"),
    ("GB", "G B"), ("MB", "M B"), ("KB", "K B"), ("TB", "T B"),
    ("GHz", "G赫兹"), ("MHz", "兆赫兹"), ("Hz", "赫兹"),
    ("ms", "毫秒"), ("km/h", "千米每小时"), ("km", "千米"), ("kg", "千克"),
    ("℃", "摄氏度"), ("°C", "摄氏度"), ("％", "%"),
    ("μm", "微米"), ("mm", "毫米"), ("cm", "厘米"),
]

# 常见英文缩写（按字母逐个读，避免被当成单词念错）
_ACRONYMS = ["GPU", "CPU", "API", "RAG", "LLM", "TTS", "ASR", "OCR", "RRF", "FAQ",
             "URL", "HTTP", "JSON", "SQL", "CSV", "PDF", "RTX", "GTX", "VRAM", "RAM",
             "GPU显存", "NLP", "RNN", "CNN", "LSTM", "BERT", "ID", "UI", "OS"]


def _int_to_chinese(num: int) -> str:
    """整数 → 中文读法（支持到亿；处理"一十几"读成"十几"）"""
    if num == 0:
        return "零"
    if num < 0:
        return "负" + _int_to_chinese(-num)
    s = str(num)
    groups = []
    while s:
        groups.append(s[-4:])
        s = s[:-4]
    parts = []
    for gi, g in enumerate(groups):
        gv = int(g)
        if gv == 0:
            if parts and not parts[-1].endswith("零"):
                parts.append("零")
            continue
        txt = ""
        for i, ch in enumerate(g):
            d = int(ch)
            pos = len(g) - i - 1
            if d == 0:
                if txt and not txt.endswith("零"):
                    txt += "零"
                continue
            unit = _CN_UNITS[pos] if pos < len(_CN_UNITS) else ""
            if gi > 0 and pos == 0 and len(g) == 4:
                unit = "万" * 0  # 组内个位不加单位，"万/亿"由组名补
            txt += _CN_DIGITS[d] + unit
        txt = txt.rstrip("零")
        if gi == 1:
            txt += "万"
        elif gi == 2:
            txt += "亿"
        elif gi >= 3:
            txt += "亿" * gi
        parts.append(txt)
    out = "".join(reversed(parts)).replace("零零", "零")
    out = re.sub(r"^一十", "十", out)
    return out.rstrip("零") or "零"


def _digits_to_chinese(digits: str) -> str:
    """逐位读（用于型号、年份等：4060 → 四零六零）"""
    return "".join(_CN_DIGITS[int(c)] if c.isdigit() else c for c in digits)


def _decimal_to_chinese(text: str) -> str:
    """小数/整数 → 中文（3.14 → 三点一四；1.5 → 一点五）"""
    def _repl(m):
        int_part, frac = m.group(1), m.group(2)
        return _int_to_chinese(int(int_part)) + "点" + _digits_to_chinese(frac)
    return re.sub(r"(\d+)\.(\d+)", _repl, text)


def normalize_for_speech(text: str) -> str:
    """
    数字、单位、英文缩写 → 适合中文朗读的形式

    规则（对应"数字、单位与专业名词的读法"）：
    - 百分数：95% → 百分之九十五
    - 小数：3.14 → 三点一四
    - 单位：8GB → 八 G B；1.5 秒 → 一点五秒；65.5℃ → 六十五点五摄氏度
    - 紧跟在拉丁字母后的 3~4 位数字视作型号：RTX 4060 → RTX 四零六零
    - 4 位年份 + 年：2026年 → 二零二六年
    - 纯大写英文缩写按字母读：GPU → G P U
    """
    if not text:
        return ""
    out = text

    # 1) 百分数
    out = re.sub(r"(\d+(?:\.\d+)?)\s*[%％]",
                 lambda m: "百分之" + _decimal_to_chinese(m.group(1)), out)

    # 2) 温度
    out = re.sub(r"(\d+(?:\.\d+)?)\s*(?:℃|°C)",
                 lambda m: _decimal_to_chinese(m.group(1)) + "摄氏度", out)

    # 3) 单位（长单位优先）
    for unit, cn in _UNIT_MAP:
        out = re.sub(rf"(\d+(?:\.\d+)?)\s*{re.escape(unit)}",
                     lambda m, cn=cn: _decimal_to_chinese(m.group(1)) + " " + cn, out)

    # 4) 型号：拉丁字母/空白 + 3~4 位数字 → 逐位读（RTX 4060 → RTX 四零六零）
    out = re.sub(
        r"([A-Za-z][A-Za-z0-9\-]*\s+)(\d{3,4})(?![\d.])",
        lambda m: m.group(1) + _digits_to_chinese(m.group(2)), out)

    # 5) 年份
    out = re.sub(r"(?<!\d)(1[89]\d{2}|20\d{2})\s*年",
                 lambda m: _digits_to_chinese(m.group(1)) + "年", out)

    # 6) 其余整数 / 小数
    out = _decimal_to_chinese(out)
    out = re.sub(r"(?<![\d.])0(?![\d.])", "零", out)
    out = re.sub(r"(?<![\dA-Za-z.])(\d+)(?![\d.])",
                 lambda m: _int_to_chinese(int(m.group(1))), out)

    # 7) 英文缩写按字母读
    for acr in _ACRONYMS:
        out = re.sub(rf"(?<![A-Za-z]){acr}(?![A-Za-z])", " ".join(acr), out)

    # 8) 收尾：合并多余空格
    out = re.sub(r"[ 	]{2,}", " ", out)
    return out.strip()


def readable_text(text: str, max_chars: Optional[int] = None) -> Dict[str, object]:
    """
    生成"适合朗读"的文本

    Args:
        text: 原始回答（含 Markdown）
        max_chars: 可选的朗读长度上限；**不做静默截断**，
                   超过时返回 truncated=True 并给出被省略的字符数

    Returns:
        {"text": 可朗读文本, "truncated": bool, "omitted_chars": int, "hints": [str]}
    """
    if not text:
        return {"text": "", "truncated": False, "omitted_chars": 0, "hints": []}

    hints: List[str] = []
    cleaned = strip_markdown(text)
    cleaned = normalize_math(cleaned)          # 先处理公式（避免数字归一化破坏公式）
    cleaned = normalize_for_speech(cleaned)    # 数字/单位/缩写 → 可朗读形式
    cleaned = _UI_INFO_RE.sub("", cleaned)
    cleaned = _EMOJI_RE.sub("", cleaned)
    for hint in (CODE_HINT, TABLE_HINT, FORMULA_HINT):
        if hint in cleaned:
            hints.append(hint)
    cleaned = _WS_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    truncated = False
    omitted = 0
    if max_chars is not None and len(cleaned) > max_chars:
        # 在句子边界切；切点之后的内容**明确标记为省略**，由调用方决定是否续读
        segments = split_sentences(cleaned)
        kept: List[str] = []
        total = 0
        for seg in segments:
            if total + len(seg) > max_chars and kept:
                break
            kept.append(seg)
            total += len(seg)
        kept_text = "".join(kept)
        omitted = len(cleaned) - len(kept_text)
        truncated = omitted > 0
        cleaned = kept_text

    return {"text": cleaned, "truncated": truncated,
            "omitted_chars": omitted, "hints": hints}


# ---------- 句子切分 ----------

# 句末标点（其后可断句）：中文句号/问号/感叹号/分号 + 英文 .!?;
# 注意：
# - 英文句点要避免在小数（3.14）、缩写（e.g.）处误切
# - 列表序号（"3." / "（1）"）后面也不能当句子结束，否则会单独合成"3."这种碎片
_SENT_END_RE = re.compile(r"(?<=[。！？；!?;])|(?<=[.])(?=\s|$)")


def _is_list_number(segment: str) -> bool:
    """判断一段是不是"列表序号"（如 '3.' / '12.'），这种不该单独朗读"""
    return bool(re.fullmatch(r"\s*\d{1,3}[.、)]\s*", segment or ""))


def _split_long_sentence(sentence: str, max_chars: int) -> List[str]:
    """
    超长单句在次级边界（，、：,/等）继续切；仍超长则**按字数硬切但不丢字**。

    旧实现是 `sentence[:max]`，直接丢掉后半句 —— 这里保证拼接回去等于原句。
    """
    if len(sentence) <= max_chars:
        return [sentence]

    parts: List[str] = []
    buf = ""
    for ch in sentence:
        buf += ch
        if len(buf) >= max_chars and ch in "，、：,:；;）)":
            parts.append(buf)
            buf = ""
    if buf:
        # 剩余部分可能仍超长：按标点/长度硬切，但逐块保留
        while len(buf) > max_chars:
            window = buf[:max_chars]
            cut = max((window.rfind(c) for c in "，、：,:；; "), default=-1)
            cut = cut + 1 if cut > max_chars * 0.5 else max_chars
            parts.append(buf[:cut])
            buf = buf[cut:]
        if buf:
            parts.append(buf)
    return [p for p in parts if p]


def split_sentences(text: str, max_chars: int = DEFAULT_MAX_SEGMENT_CHARS,
                    min_chars: int = DEFAULT_MIN_SEGMENT_CHARS) -> List[str]:
    """
    按句子切分成朗读短段

    保证（有测试覆盖）：
    - 顺序拼接所有返回段 == 去掉空白差异后的原文（**不漏字、不重复**）
    - 每段不超过 max_chars（除非该段是无法再切的最小单元）
    - 数字/单位/英文缩写不被误切（如 "3.14"、"e.g."、"GPT-SoVITS"）
    """
    if not text:
        return []
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []

    # 1) 先按段落切（空行是天然的朗读停顿）
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", normalized) if p.strip()]

    segments: List[str] = []

    def _merge(parts: List[str]) -> List[str]:
        """段内合并过短句（不跨段合并：段落边界是天然的朗读停顿）"""
        out: List[str] = []
        buf = ""
        for piece in parts:
            if not piece:
                continue
            if not buf:
                buf = piece
            elif len(buf) + len(piece) <= max_chars and len(buf) < min_chars:
                buf += piece
            elif len(buf) + len(piece) <= max_chars:
                out.append(buf)
                buf = piece
            else:
                out.append(buf)
                buf = piece
        if buf:
            out.append(buf)
        return out

    for para in paragraphs:
        # 段内按句子切
        raw_sentences = [s.strip() for s in _SENT_END_RE.split(para) if s and s.strip()]
        if not raw_sentences:
            continue
        # 列表序号（"3."）与其后的内容合并，避免读出孤零零的"3."
        merged_sentences: List[str] = []
        for sent in raw_sentences:
            if merged_sentences and _is_list_number(merged_sentences[-1]):
                merged_sentences[-1] = merged_sentences[-1] + sent
            else:
                merged_sentences.append(sent)
        raw_sentences = merged_sentences
        # 超长句先切分（保证不丢字）
        pieces: List[str] = []
        for sent in raw_sentences:
            pieces.extend(_split_long_sentence(sent, max_chars))
        segments.extend(_merge(pieces))

    return [s.strip() for s in segments if s.strip()]


def segments_to_text(segments: List[str]) -> str:
    """把切分结果拼回文本（测试用：用于验证"不漏字"）"""
    return "".join(segments)


def text_key(text: str) -> str:
    """比较用：去掉所有空白后的文本"""
    return re.sub(r"\s+", "", text or "")


__all__ = [
    "readable_text",
    "normalize_for_speech",
    "split_sentences",
    "strip_markdown",
    "normalize_math",
    "segments_to_text",
    "text_key",
    "FORMULA_HINT",
    "TABLE_HINT",
    "CODE_HINT",
    "DEFAULT_MAX_SEGMENT_CHARS",
]
