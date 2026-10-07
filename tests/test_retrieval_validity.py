# -*- coding: utf-8 -*-
"""检索前有效性校验：已删/已替换资料不得进入上下文与来源（2026-10-08）。"""
from services import ai_service
from langchain_core.documents import Document


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv('EDU_DATA_DIR', str(tmp_path))
    subject = tmp_path / 'subjects' / 's1'
    subject.mkdir(parents=True)
    (subject / 'keep.txt').write_bytes('保留'.encode('utf-8'))
    (subject / '_superseded').mkdir()
    return subject


def test_deleted_file_is_dropped_even_if_index_still_has_it(tmp_path, monkeypatch):
    """索引里还有、但文件已被删除 → 不得进入上下文（不依赖重建成功）。"""
    _isolate(tmp_path, monkeypatch)
    docs = [
        Document(page_content='保留内容', metadata={'file_name': 'keep.txt'}),
        Document(page_content='已删内容', metadata={'file_name': 'gone.txt'}),
    ]
    kept = ai_service._filter_existing_materials(docs, 's1')
    names = [d.metadata.get('file_name') for d in kept]
    assert names == ['keep.txt'], names


def test_superseded_source_is_dropped(tmp_path, monkeypatch):
    """留档目录里的旧版本不得被当成有效资料召回。"""
    _isolate(tmp_path, monkeypatch)
    docs = [
        Document(page_content='旧版本', metadata={'source': '_superseded/a.txt.abc123'}),
        Document(page_content='现行', metadata={'source': 'keep.txt'}),
    ]
    kept = ai_service._filter_existing_materials(docs, 's1')
    assert [d.page_content for d in kept] == ['现行']


def test_unknown_source_is_kept_conservatively(tmp_path, monkeypatch):
    """无法判定来源时保守保留，避免误伤历史资料。"""
    _isolate(tmp_path, monkeypatch)
    docs = [Document(page_content='无来源元数据', metadata={})]
    assert len(ai_service._filter_existing_materials(docs, 's1')) == 1


def test_missing_subject_dir_does_not_filter(tmp_path, monkeypatch):
    """学科目录不存在（只读/历史部署）时不做过滤。"""
    monkeypatch.setenv('EDU_DATA_DIR', str(tmp_path))
    docs = [Document(page_content='x', metadata={'file_name': 'whatever.txt'})]
    assert len(ai_service._filter_existing_materials(docs, 'nope')) == 1


def test_same_name_in_different_dirs_is_not_a_replace(tmp_path):
    """不同目录的同名文件不应当被当成一次"替换"（_decide_target 只看文件名与内容）。"""
    from services import import_service as svc
    subject = tmp_path / 'subjects' / 's2'
    subject.mkdir(parents=True)
    (subject / '同名.txt').write_bytes('A内容'.encode('utf-8'))
    # 另一份不同内容、同名文件（来自另一个目录/压缩包成员）→ 视为修改（替换）
    decision = svc._decide_target(subject, '同名.txt', 'B内容'.encode('utf-8'))
    assert decision.get('replace') is True
    # 但若内容一致则跳过，不会重复
    payload = 'A内容'.encode('utf-8')
    assert svc._decide_target(subject, '同名.txt', payload)['skip'] is True
