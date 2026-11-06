"""展示名、头像，以及课件文件名的自然排序。"""

import io
import uuid

from PIL import Image

from services.knowledge_service import natural_filename_key


def test_chapter_ten_sorts_after_chapter_two():
    names = [
        "计算机图形学-第10章-真实感图形绘制.pptx",
        "计算机图形学-第2章-概述.pptx",
        "计算机图形学-第1章-绪论.pptx",
    ]
    ordered = sorted(names, key=natural_filename_key)
    assert ordered[0].startswith("计算机图形学-第1章")
    assert ordered[1].startswith("计算机图形学-第2章")
    assert ordered[-1].startswith("计算机图形学-第10章")


def test_update_display_name_and_avatar():
    from services import auth_service
    from tests.fixtures_isolated import start_isolated_env, stop_isolated_env

    tmpdir, _data_dir, patchers = start_isolated_env(("数据结构",))
    try:
        user = auth_service.register_user("stu_avatar_" + uuid.uuid4().hex[:6], "test-pass-123", "旧名字")
        updated = auth_service.update_display_name(user["id"], "新名字")
        assert updated["display_name"] == "新名字"
        assert updated["username"] == user["username"]
        assert updated["has_avatar"] is False

        try:
            auth_service.update_display_name(user["id"], "   ")
            raise AssertionError("空白姓名应该被拒绝")
        except auth_service.AuthError as exc:
            assert exc.error_type == "invalid_request"

        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (10, 20, 30)).save(buf, format="PNG")
        saved = auth_service.save_avatar(user["id"], buf.getvalue(), "face.png")
        assert saved["has_avatar"] is True
        assert auth_service.avatar_file(user["id"])

        try:
            auth_service.save_avatar(user["id"], b"not-an-image", "face.png")
            raise AssertionError("非图片应该被拒绝")
        except auth_service.AuthError as exc:
            assert exc.error_type == "invalid_request"
    finally:
        stop_isolated_env(tmpdir, patchers)


def test_page_index_progress_reports_stage():
    from services import page_index

    subject = "pgstage_" + uuid.uuid4().hex[:8]
    doc_id = "f:a.pdf"
    try:
        page_index._report_progress(subject, doc_id, "a.pdf", 0, 12, 3, stage="ocr")
        active = page_index.get_active_progress(subject)
        assert len(active) == 1
        assert active[0]["stage"] == "ocr"
        assert active[0]["page"] == 3
        assert active[0]["total"] == 12
    finally:
        page_index._clear_progress(subject, doc_id)
