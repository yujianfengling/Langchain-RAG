"""
朗读文本处理测试（2026-09-26）

重点验证本轮要求：
- 长回答切分**不丢字**（修掉旧实现 `seg[:max]` 直接截断剩余文字的问题）
- Markdown / 代码块 / 链接 / 公式 / 表格 的朗读处理
- 数字、单位、英文缩写不被误切
- 需要截断时**显式返回** truncated / omitted_chars，不静默丢弃
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.tts_text import (  # noqa: E402
    CODE_HINT, FORMULA_HINT, TABLE_HINT,
    normalize_for_speech, readable_text, split_sentences, text_key,
)


class TestSentenceSplitNoLoss(unittest.TestCase):
    """切分的核心契约：拼接回去等于原文（去空白），且每段长度受控"""

    CASES = [
        "二叉树是每个节点最多有两个子树的树结构。前序遍历的顺序是根、左、右。",
        "深度学习（Deep Learning）在 2024 年很流行。它的准确率提升了 3.14 倍。",
        "GPT-SoVITS 与 CosyVoice 都是本地 TTS。e.g. 这种缩写不应被断开。",
        "第一段内容很长" + "，这里继续补充说明" * 30 + "。结尾句。",
        "行一\n\n行二\n\n行三",
        "无标点长句" * 100,
        "混合 English and 中文 mixed text 一句话。Second sentence here.",
        "",
    ]

    def test_concatenation_equals_original(self):
        for text in self.CASES:
            segments = split_sentences(text)
            self.assertEqual(
                text_key("".join(segments)), text_key(text),
                f"切分后内容与原文不一致（漏字/重复）：{text[:30]!r}")

    def test_segment_length_bounded(self):
        text = "这是一句比较长的话" * 40
        for seg in split_sentences(text, max_chars=120):
            self.assertLessEqual(len(seg), 120, seg)

    def test_no_segment_dropped_for_long_single_sentence(self):
        """旧的 slice(0, max) 会丢后半句：这里必须全部保留"""
        text = "甲" * 500
        segments = split_sentences(text)
        self.assertEqual("".join(segments), text)
        self.assertGreater(len(segments), 1)

    def test_numbers_units_and_abbreviations_intact(self):
        text = "显存 8GB，速度 1.5GB/s，温度 65.5 摄氏度，缩写 e.g. 与 etc. 保持完整。"
        segments = split_sentences(text)
        joined = "".join(segments)
        for token in ("8GB", "1.5GB/s", "65.5", "e.g.", "etc."):
            self.assertIn(token, joined, f"{token} 被切断")

    def test_list_number_not_read_alone(self):
        """列表序号（"3."）不能单独成段，否则会读出孤零零的"3." """
        segs = split_sentences("3. 递归结构：左子树和右子树本身也是二叉树。")
        self.assertEqual(len(segs), 1, segs)
        self.assertTrue(segs[0].startswith("3."))
        self.assertIn("递归结构", segs[0])
        # 多行列表同样不丢字
        multi = "1. 定义\n2. 性质\n3. 例子"
        self.assertEqual(text_key("".join(split_sentences(multi))), text_key(multi))

    def test_no_segment_is_bare_list_marker(self):
        """
        回归（2026-09-26 真机验收发现）：任何一段都不能只是"3."这样的序号。

        真机日志里出现过 items[0].text == "3." —— 当时是流式切分把序号切出来单独合成，
        会读出一个孤立的数字。后端合并规则对这种"已是独立输入"的文本无能为力，
        所以前端也修了切分（frontend/src/utils/ttsPlayer.ts::takeCompleteSentences）。
        这里固定后端行为：即使收到这种形状的输入，也不允许输出纯序号段。
        """
        texts = [
            "要点：\n1. 每个结点最多有两个子结点。",
            "步骤：\n1. 先访问根\n2. 再访问左子树\n3. 最后访问右子树",
            "要点：\n1. 定义\n2. 性质\n3. 例子",
            "1. 只有一个条目的列表",
        ]
        for text in texts:
            for seg in split_sentences(text):
                self.assertFalse(
                    re.fullmatch(r"\s*\d{1,3}\.\s*", seg),
                    f"出现了纯序号段 {seg!r}（会被单独朗读）：来自 {text!r}")

    def test_empty_and_whitespace(self):
        self.assertEqual(split_sentences(""), [])
        self.assertEqual(split_sentences("   \n  "), [])


class TestSpeechNormalization(unittest.TestCase):
    """
    数字 / 单位 / 缩写的朗读归一化

    背景（实测）：CosyVoice 的文本前端（wetext）在本机会静默失效
    （日志 "no frontend is available"），此时 "3.14 倍" 会被读成乱码。
    因此在业务侧做一层可控归一化，并用 ASR 回读验证效果。
    """

    def test_decimal_numbers(self):
        self.assertEqual(normalize_for_speech("3.14"), "三点一四")
        self.assertEqual(normalize_for_speech("1.5 秒"), "一点五 秒")

    def test_percentage_and_temperature(self):
        self.assertEqual(normalize_for_speech("95%"), "百分之九十五")
        self.assertEqual(normalize_for_speech("65.5℃"), "六十五点五摄氏度")

    def test_units(self):
        self.assertEqual(normalize_for_speech("8GB"), "八 G B")
        self.assertEqual(normalize_for_speech("1200 毫秒"), "一千二百 毫秒")
        self.assertEqual(normalize_for_speech("1.5GB/s"), "一点五 G B 每秒")

    def test_model_numbers_read_digit_by_digit(self):
        """RTX 4060 这类型号逐位读（四零六零），而不是"四千零六十" """
        self.assertEqual(normalize_for_speech("RTX 4060"), "R T X 四零六零")
        self.assertEqual(normalize_for_speech("GTX 1080"), "G T X 一零八零")

    def test_year(self):
        self.assertEqual(normalize_for_speech("2026年"), "二零二六年")

    def test_acronyms_spelled_out(self):
        self.assertEqual(normalize_for_speech("使用 GPU 加速"), "使用 G P U 加速")
        self.assertEqual(normalize_for_speech("调用 API"), "调用 A P I")

    def test_plain_chinese_unchanged(self):
        zh = "二叉树是每个节点最多有两个子树的树结构。"
        self.assertEqual(normalize_for_speech(zh), zh)

    def test_applied_in_readable_text(self):
        r = readable_text("准确率提升了 3.14 倍，显存占用约 8GB。")
        self.assertIn("三点一四", r["text"])
        self.assertIn("八 G B", r["text"])
        self.assertNotIn("3.14", r["text"])

    def test_formula_not_number_mangled(self):
        """公式应先被识别成提示，而不是被数字规则拆坏"""
        r = readable_text(r"由 $x_1^2 + 3.14$ 可知")
        self.assertIn(FORMULA_HINT, r["text"])
        self.assertIn("可知", r["text"])


class TestReadableText(unittest.TestCase):

    def test_markdown_emphasis_and_headings_removed(self):
        r = readable_text("## 标题\n二叉树是**每个节点**最多有两个子树的树结构。")
        self.assertNotIn("##", r["text"])
        self.assertNotIn("**", r["text"])
        self.assertIn("二叉树是每个节点最多有两个子树的树结构", r["text"])

    def test_code_block_becomes_hint(self):
        r = readable_text("前面的说明。\n\n```python\nprint(1)\n```\n\n后面的说明。")
        self.assertIn(CODE_HINT, r["text"])
        self.assertNotIn("print(1)", r["text"])
        # 正文不丢
        self.assertIn("前面的说明", r["text"])
        self.assertIn("后面的说明", r["text"])

    def test_link_keeps_text_drops_url(self):
        r = readable_text("参考[教材第一章](https://example.com/a/b)的内容。")
        self.assertIn("教材第一章", r["text"])
        self.assertNotIn("http", r["text"])

    def test_latex_becomes_screen_hint_not_garbage(self):
        r = readable_text(r"由公式 $\frac{a}{b} = c$ 可得结论。")
        self.assertIn(FORMULA_HINT, r["text"])
        self.assertNotIn("frac", r["text"])
        self.assertIn("可得结论", r["text"])

    def test_symbol_dense_sentence_gets_hint(self):
        r = readable_text("根据 a_1^2 + b_2^2 ≤ c^2 可知。")
        self.assertIn(FORMULA_HINT, r["text"])
        self.assertIn("可知", r["text"])

    def test_table_becomes_hint(self):
        md = "对比如下：\n\n| 名称 | 值 |\n|---|---|\n| A | 1 |\n\n以上就是对比。"
        r = readable_text(md)
        self.assertIn(TABLE_HINT, r["text"])
        self.assertNotIn("|---|---|", r["text"])
        self.assertIn("以上就是对比", r["text"])

    def test_ui_internal_info_not_read(self):
        r = readable_text("答案是二叉树。\n\n模型: deepseek-chat | 总耗时: 1.25s")
        self.assertNotIn("deepseek-chat", r["text"])
        self.assertNotIn("1.25s", r["text"])
        self.assertIn("答案是二叉树", r["text"])

    def test_inline_citation_markers_not_read(self):
        """
        行内引用标记 [1] / [1,2] 是屏幕上的来源序号，不应朗读。

        回归（2026-09-26 实测发现）：不处理时数字归一化会把 [1] 变成 [一]，
        听众会听到一个孤零零的"一"。
        """
        r = readable_text("二叉树的定义如下[1]。参考教材第二章[2]。")
        self.assertNotIn("[", r["text"])
        self.assertNotIn("一]", r["text"])
        self.assertIn("二叉树的定义如下。", r["text"].replace(" ", ""))
        self.assertIn("参考教材第二章。", r["text"].replace(" ", ""))
        for src in ("[1,2]", "[1-3]", "【1】", "[ 2 ]"):
            out = readable_text(f"结论如下{src}。")["text"]
            self.assertNotIn("]", out, f"{src} 未被处理")
            self.assertIn("结论如下", out)

    def test_explicit_truncation_reports_omitted(self):
        """截断必须显式告知，不能静默丢弃"""
        long_text = "这是一句测试用的话。" * 60
        r = readable_text(long_text, max_chars=100)
        self.assertTrue(r["truncated"])
        self.assertGreater(r["omitted_chars"], 0)
        self.assertLessEqual(len(r["text"]), 100)
        # 不设上限时不得截断
        r2 = readable_text(long_text)
        self.assertFalse(r2["truncated"])
        self.assertEqual(r2["omitted_chars"], 0)
        self.assertGreater(len(r2["text"]), 100)


if __name__ == "__main__":
    unittest.main()
