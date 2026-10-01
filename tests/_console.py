"""
控制台输出兜底（Windows GBK 终端）

背景（实测）：Windows 默认控制台编码是 cp936(GBK)，打印 ✅/❌/⚠️ 这类字符会直接抛
`UnicodeEncodeError` 并让脚本崩掉——**本该给用户看的提示还没打出来程序就先挂了**。
本项目已经踩过两次：
- `tests/_isolation_guard.py` 的"拒绝运行"提示自己崩掉（退出码不是预期的 2）；
- `tests/check_first_run.py`（部署文档里的第一条命令）在 [1/7] 就崩。

做法：把标准输出的 errors 设为 replace——**保留原有编码**（中文在 GBK 里能正常显示），
只把 GBK 表示不了的字符（emoji 等）替换成 '?'，保证任何终端下都能把话说完。

注意：不强制改成 UTF-8，否则在旧版 GBK 终端里中文会变乱码。
"""

import sys


def safe_console() -> None:
    """让标准输出/错误在任何终端下都不会因编码问题抛异常"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
