"""
个人学科（学生自己创建的学科）的可见性与资料列表回归测试（2026-10-07）

背景：上轮为"学生可创建自己的学科"引入了 personal 归属。本轮端到端验收发现
`student_visible_document_names` 没有 personal 分支，会落到课程分支用 course_id=None
去查课程资料，返回空集合 → `GET /knowledge/{sid}/documents` 把学生自己上传的资料
全部隐藏（界面显示"资料 0 份"、资料范围选择为空）。这里锁定正确行为。
"""

import pytest

from services import subject_service, teaching_access


@pytest.fixture()
def personal_subject():
    sid = "personal_subject_test"
    owner = "owner_user_test"
    try:
        subject_service.delete_subject(sid)
    except Exception:
        pass
    subject_service.create_subject(sid, "个人学科回归", owner_user_id=owner)
    yield sid, owner
    try:
        subject_service.delete_subject(sid)
    except Exception:
        pass


def test_personal_subject_owner_can_read_and_write(personal_subject):
    sid, owner = personal_subject
    assert teaching_access.subject_scope(sid)["scope"] == "personal"
    assert teaching_access.can_read_subject(owner, sid) is True
    assert teaching_access.can_write_subject(owner, sid) is True
    assert teaching_access.require_subject_write(owner, sid) is True


def test_personal_subject_others_denied(personal_subject):
    sid, _owner = personal_subject
    assert teaching_access.can_read_subject("someone_else", sid) is False
    assert teaching_access.can_write_subject("someone_else", sid) is False
    with pytest.raises(teaching_access.TeachingError):
        teaching_access.require_subject_read("someone_else", sid)


def test_personal_subject_does_not_filter_document_names(personal_subject):
    """
    关键回归：personal 学科必须返回 None（不按文件名过滤）。
    返回 set() 会让资料列表变空 —— 这正是本轮修复的缺陷。
    """
    sid, owner = personal_subject
    assert teaching_access.student_visible_document_names(owner, sid) is None
    assert teaching_access.student_visible_document_names("someone_else", sid) is None


def test_personal_subject_owner_recorded(personal_subject):
    sid, owner = personal_subject
    assert subject_service.get_subject_owner(sid) == owner
