"""
测试用真实样本生成器（2026-10-07）

用于真实格式解析 / 压缩包导入 / 批量导入任务测试：
生成的是**真正的** pptx / xlsx / csv / txt / zip / 7z，而不是 mock。
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path


def make_png_bytes(color=(255, 0, 0), size=(80, 80)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def make_pptx(path: str, with_image: bool = True) -> str:
    """
    生成含标题/正文/表格/备注/组合形状/空白页的多页 pptx。
    返回路径。
    """
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()

    # 第 1 页：标题 + 正文 + 组合形状内文本 +（可选）图片
    s1 = prs.slides.add_slide(prs.slide_layouts[5])
    s1.shapes.title.text = "第一页标题"
    tb = s1.shapes.add_textbox(Inches(1), Inches(2), Inches(4), Inches(1))
    # 正文刻意写长一些：真实建库有 chunk_min_length=120 的碎片过滤，
    # 太短的内容会被丢弃，无法验证"进入检索"。
    tb.text_frame.text = "第一页正文内容。" * 30
    group = s1.shapes.add_group_shape()
    inner = group.shapes.add_textbox(Inches(0), Inches(0), Inches(2), Inches(1))
    inner.text_frame.text = "组合形状内文本"
    if with_image:
        img_path = str(Path(path).parent / "_sample_pic.png")
        Path(img_path).write_bytes(make_png_bytes())
        s1.shapes.add_picture(img_path, Inches(5), Inches(4), Inches(1), Inches(1))

    # 第 2 页：标题 + 表格 + 备注
    s2 = prs.slides.add_slide(prs.slide_layouts[5])
    s2.shapes.title.text = "第二页标题"
    table = s2.shapes.add_table(2, 2, Inches(0.5), Inches(2), Inches(6), Inches(1)).table
    table.cell(0, 0).text = "名称"
    table.cell(0, 1).text = "数值"
    table.cell(1, 0).text = "alpha"
    table.cell(1, 1).text = "42"
    s2.notes_slide.notes_text_frame.text = "重要备注：alpha 等于四十二"

    # 第 3 页：空白页（无文本、无图）
    prs.slides.add_slide(prs.slide_layouts[5])

    prs.save(path)
    return Path(path)


def make_xlsx(path: str) -> str:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "成绩表"
    ws.append(["姓名", "分数"])
    ws.append(["张三", 95])
    ws.append(["李四", 88])
    ws2 = wb.create_sheet("说明")
    ws2.append(["备注"])
    ws2.append(["这是第二个工作表"])
    wb.save(path)
    return Path(path)


def make_csv(path: str) -> str:
    Path(path).write_text("姓名,分数\n张三,95\n李四,88\n", encoding="utf-8")
    return Path(path)


def make_txt(path: str, text: str = "这是一份测试说明文档内容。" * 6) -> str:
    Path(path).write_text(text, encoding="utf-8")
    return Path(path)


def make_zip(path: str, members) -> str:
    """
    members: [(arcname, bytes), ...]
    """
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            z.writestr(name, data)
    return Path(path)


def make_zip_with_gbk_name(path: str) -> str:
    """写入一个未置 UTF-8 标志、文件名按 GBK 编码的条目"""
    with zipfile.ZipFile(path, "w") as z:
        fake = "中文名.txt".encode("gbk").decode("cp437")
        info = zipfile.ZipInfo(fake)
        info.flag_bits = 0
        z.writestr(info, "中文内容".encode("gbk"))
    return Path(path)


def force_zip_encrypted_flag(path: str) -> str:
    """
    把 zip 中央目录的通用标志位 bit0（加密）置 1。

    仅用于测试"加密压缩包必须明确失败"的判定路径（Python zipfile 不能写加密包）。
    """
    data = bytearray(Path(path).read_bytes())
    idx = data.find(b"PK\x01\x02")
    if idx < 0:
        raise RuntimeError("not a zip")
    # 中央目录：签名4 + version made by 2 + version needed 2 + flags 2
    flags_off = idx + 8
    data[flags_off] = data[flags_off] | 0x01
    Path(path).write_bytes(bytes(data))
    return Path(path)


def make_7z(path: str, members) -> str:
    import py7zr
    with py7zr.SevenZipFile(path, "w") as z:
        for name, data in members:
            if isinstance(data, str):
                z.writestr(data, name)
            else:
                z.writestr(data.decode("utf-8", errors="ignore"), name)
    return Path(path)


def make_encrypted_7z(path: str, password: str = "secret") -> str:
    import py7zr
    with py7zr.SevenZipFile(path, "w", password=password) as z:
        z.writestr("加密内容", "secret.txt")
    return Path(path)


__all__ = [
    "force_zip_encrypted_flag",
    "make_7z",
    "make_csv",
    "make_encrypted_7z",
    "make_png_bytes",
    "make_pptx",
    "make_txt",
    "make_xlsx",
    "make_zip",
    "make_zip_with_gbk_name",
]
