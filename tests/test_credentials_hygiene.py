"""
凭据卫生回归测试（2026-09-26）

背景：`config/models.py` 曾内置团队共享密钥且该文件被 Git 跟踪 → 密钥进入了仓库与历史。
本组用例保证：仓库**跟踪的文件**里不会再出现真实密钥形态的字符串，
且密钥解析只走"环境变量 / 本地未跟踪配置"，没有内置兜底。

注意：用例只做"形态扫描"，**不会**打印任何疑似密钥的内容。
"""

import os
import re
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 常见密钥形态（只用于判定"像不像密钥"，不用于提取内容）
_KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"tp-[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9\-_\.]{24,}"),
]

# 允许出现"密钥形态"的文件：示例/文档中的占位符、测试自身
_ALLOWLIST = {
    os.path.join("tests", "test_credentials_hygiene.py"),
    os.path.join("tests", "test_default_model_switch.py"),
}


def _tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def _looks_like_key(text: str) -> bool:
    return any(p.search(text) for p in _KEY_PATTERNS)


class TestNoSecretsInTrackedFiles(unittest.TestCase):

    def test_git_tracked_files_contain_no_keylike_string(self):
        """
        被 Git 跟踪的文件中不得出现真实密钥形态的字符串。

        只报告**文件路径与命中行号**，不输出内容。
        """
        offenders = []
        for rel in _tracked_files():
            if rel in _ALLOWLIST:
                continue
            path = os.path.join(REPO_ROOT, rel)
            if not os.path.isfile(path):
                continue
            if os.path.getsize(path) > 2 * 1024 * 1024:
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if _looks_like_key(line):
                            offenders.append(f"{rel}:{i}")
            except OSError:
                continue
        self.assertEqual(offenders, [], f"跟踪文件中疑似存在真实密钥：{offenders}")

    def test_example_config_has_no_real_key(self):
        """示例配置里只能是占位符"""
        with open(os.path.join(REPO_ROOT, "config", "local_secrets.example.json"),
                  encoding="utf-8") as f:
            content = f.read()
        self.assertFalse(_looks_like_key(content), "local_secrets.example.json 疑似含真实密钥")
        self.assertIn("<your-deepseek-api-key>", content)

    def test_env_example_has_no_real_key(self):
        with open(os.path.join(REPO_ROOT, "config", "env.example"),
                  encoding="utf-8") as f:
            content = f.read()
        self.assertFalse(_looks_like_key(content), "env.example 疑似含真实密钥")


class TestKeyResolutionHasNoBuiltinFallback(unittest.TestCase):

    def setUp(self):
        # 清掉本地密钥文件的影响，保证结果只取决于当前 mock 的环境
        from config import models as model_config
        model_config.reload_local_secrets()
        self.addCleanup(model_config.reload_local_secrets)

    def test_builtin_table_is_empty(self):
        from config import models as model_config
        self.assertEqual(model_config._BUILTIN_API_KEYS, {},
                         "不应再保留内置密钥表")

    def test_no_key_when_env_and_local_missing(self):
        """环境变量与本地配置都没有时 → 空串（提供方标记为未配置），不回落任何内置值"""
        from config import models as model_config
        env = {"DEEPSEEK_API_KEY": "", "MIMO_API_KEY": "", "DASHSCOPE_API_KEY": ""}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(model_config, "_load_local_secrets", return_value={}):
            model_config.reload_local_secrets()
            for name in ("deepseek", "mimo", "qwen", "mimo-asr", "mimo-tts"):
                self.assertEqual(model_config.get_model_api_key(name), "",
                                 f"{name} 在没有配置时不应拿到任何密钥")

    def test_local_untracked_config_is_used_when_env_missing(self):
        """本地未跟踪配置可作为来源（且支持 env: 间接引用）"""
        from config import models as model_config
        fake = "sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2"
        local = {"api_keys": {"deepseek": fake}}
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}), \
             mock.patch.object(model_config, "_load_local_secrets", return_value=local["api_keys"]):
            model_config.reload_local_secrets()
            self.assertEqual(model_config.get_model_api_key("deepseek"), fake)

    def test_local_secrets_file_is_gitignored(self):
        """本地密钥文件必须被 .gitignore 忽略"""
        result = subprocess.run(
            ["git", "check-ignore", "-q", "config/local_secrets.json"], cwd=REPO_ROOT)
        self.assertEqual(result.returncode, 0,
                         "config/local_secrets.json 未被 .gitignore 忽略")


if __name__ == "__main__":
    unittest.main()
