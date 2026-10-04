"""风险 A 回归：未登录请求回退到 legacy 共享账号时，不得注入个人学习状态

背景（2026-10-03）：
`/api/chat`、`/api/chat/stream` 使用 `get_current_user`，**没有令牌**时会回退到
共享的 legacy 账号（`api/deps.LEGACY_USER` ← `auth_service.LEGACY_USERNAME`）。
修复前该账号的掌握度/记忆/错题/任务会被拼进 system prompt —— 未登录访问者
即可读到"某个账号"的学情（跨账号信息泄露）。
修复方式：对 legacy 账号**不做个人化**（问答照常，只是不带个人学习状态）。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures_isolated import IsolatedDataTestCase  # noqa: E402


class LegacyAccountNotPersonalizedTests(IsolatedDataTestCase):
    """legacy 账号不注入个人学习状态；正常用户不受影响"""

    def test_legacy_account_gets_no_personal_context(self):
        from services.auth_service import LEGACY_USERNAME
        from services import learning_memory, teaching_context

        # 给 legacy 账号种一条"个人学习记忆"——修复前它会被拼进提示词（真实泄露路径）
        learning_memory.set_memory(LEGACY_USERNAME, "解释风格", "多用图示",
                                   memory_type=learning_memory.MEMORY_PREFERENCE,
                                   scope=learning_memory.SCOPE_GLOBAL)
        seeded = learning_memory.get_effective_memory(LEGACY_USERNAME, "数据结构")
        self.assertTrue(seeded["items"], "前置条件：记忆已写入")

        ctx = teaching_context.build_teaching_context(
            LEGACY_USERNAME, "数据结构", question="二叉树是什么")
        self.assertEqual(ctx["prompt_block"], "",
                         "未登录（legacy 共享账号）不得注入个人学习状态/记忆")
        self.assertNotIn("多用图示", json.dumps(ctx, ensure_ascii=False),
                         "记忆内容不得出现在教学上下文里")
        self.assertEqual(ctx["teaching_mode"], teaching_context.MODE_STANDARD)
        self.assertTrue(any("未登录" in r for r in ctx["reasons"]), ctx["reasons"])

    def test_guard_is_scoped_to_legacy_account_only(self):
        """正常用户仍走原有逻辑（不因这条规则被误伤）"""
        from services import teaching_context

        ctx = teaching_context.build_teaching_context(
            "some_normal_student", "数据结构", question="二叉树是什么")
        self.assertFalse(any("未登录" in r for r in ctx["reasons"]), ctx["reasons"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
