# -*- coding: utf-8 -*-
"""检索前有效性校验：已删/已替换资料不得进入上下文与来源（2026-10-08 加固）。"""
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
    assert [d.metadata.get('file_name') for d in kept] == ['keep.txt']
    assert kept[0].metadata.get('validity') == 'verified'


def test_superseded_source_is_dropped(tmp_path, monkeypatch):
    """留档目录里的旧版本不得被当成有效资料召回。"""
    _isolate(tmp_path, monkeypatch)
    docs = [
        Document(page_content='旧版本', metadata={'source': '_superseded/a.txt.abc123'}),
        Document(page_content='现行', metadata={'source': 'keep.txt'}),
    ]
    kept = ai_service._filter_existing_materials(docs, 's1')
    assert [d.page_content for d in kept] == ['现行']


def test_unidentifiable_chunk_dropped_when_roster_available(tmp_path, monkeypatch):
    """有权威资料清单时，无法核实身份的片段一律丢弃（不冒充有效依据）。"""
    _isolate(tmp_path, monkeypatch)
    docs = [Document(page_content='无来源元数据', metadata={})]
    assert ai_service._filter_existing_materials(docs, 's1') == []


def test_unverifiable_marked_not_silently_valid(tmp_path, monkeypatch):
    """清单与目录都不可用时：保留但明确标记原因，不能静默当成有效依据。"""
    _isolate(tmp_path, monkeypatch)
    from services import material_scope
    monkeypatch.setattr(material_scope, 'list_scope_materials',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('db down')))
    monkeypatch.setattr('config.path_config.get_data_dir',
                        lambda: str(tmp_path / 'missing_root'))
    docs = [Document(page_content='x', metadata={'file_name': 'keep.txt'})]
    kept = ai_service._filter_existing_materials(docs, 's1')
    assert len(kept) == 1
    assert str(kept[0].metadata.get('validity', '')).startswith('unverified_'), kept[0].metadata


def test_same_name_in_different_dirs_is_not_a_replace(tmp_path):
    """不同目录的同名文件不应当被当成一次"替换"（_decide_target 只看文件名与内容）。"""
    from services import import_service as svc
    subject = tmp_path / 'subjects' / 's2'
    subject.mkdir(parents=True)
    (subject / '同名.txt').write_bytes('A内容'.encode('utf-8'))
    decision = svc._decide_target(subject, '同名.txt', 'B内容'.encode('utf-8'))
    assert decision.get('replace') is True
    payload = 'A内容'.encode('utf-8')
    assert svc._decide_target(subject, '同名.txt', payload)['skip'] is True


def test_delete_then_rebuild_failure_still_hides_deleted_content(tmp_path, monkeypatch):
    """组合场景：删除成功 → 强制重建失败 → 旧索引仍在线 → 再提问。

    已删内容不得进入上下文；其余资料仍可用。
    """
    from services import knowledge_service as ks
    from services import material_scope

    monkeypatch.setenv('EDU_DATA_DIR', str(tmp_path))
    subject = tmp_path / 'subjects' / 's3'
    subject.mkdir(parents=True)
    (subject / 'keep.txt').write_bytes('保留'.encode('utf-8'))
    (subject / 'gone.txt').write_bytes('删除'.encode('utf-8'))

    # 删除后重建**失败**：文件已删，但索引（模拟"旧索引仍在线"）里还带着 gone.txt 的分块
    monkeypatch.setattr(ks, 'rebuild_subject_knowledge',
                        lambda sid: (_ for _ in ()).throw(RuntimeError('forced build failure')))
    monkeypatch.setattr(ks, '_invalidate_subject_index', lambda sid: True)
    # Failed index publication is now a failed deletion with a recoverable source,
    # not a false success. A successful retry must still hide all deleted chunks.
    import pytest
    with pytest.raises(ks.KnowledgeServiceError):
        ks.delete_subject_document('s3', 'gone.txt')
    assert (subject / 'gone.txt').exists()
    monkeypatch.setattr(ks, 'rebuild_subject_knowledge', lambda sid: {'success': True})
    ks.delete_subject_document('s3', 'gone.txt')
    assert not (subject / 'gone.txt').exists(), '文件应已删除'

    stale_docs = [
        Document(page_content='保留内容', metadata={'file_name': 'keep.txt'}),
        Document(page_content='已删内容', metadata={'file_name': 'gone.txt'}),
    ]
    kept = ai_service._filter_existing_materials(stale_docs, 's3')
    names = [d.metadata.get('file_name') for d in kept]
    assert 'gone.txt' not in names, f'已删内容仍进入上下文: {names}'
    assert 'keep.txt' in names, '其余资料不应被误删'


def test_missing_subject_dir_marks_unverified(tmp_path, monkeypatch):
    """学科目录不存在（只读/历史部署）→ 保留但标记 unverified，不静默放行。"""
    monkeypatch.setenv('EDU_DATA_DIR', str(tmp_path))
    from services import material_scope
    monkeypatch.setattr(material_scope, 'list_scope_materials', lambda *a, **k: [])
    docs = [Document(page_content='x', metadata={'file_name': 'whatever.txt'})]
    kept = ai_service._filter_existing_materials(docs, 'nope')
    assert len(kept) == 1
    assert str(kept[0].metadata.get('validity', '')).startswith('unverified_')
