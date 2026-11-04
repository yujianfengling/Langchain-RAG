# -*- coding: utf-8 -*-
"""删除资料必须让已删内容从索引中消失（2026-10-08 真实修复的回归）。"""
from pathlib import Path


def test_delete_document_rebuilds_index_when_files_remain(tmp_path, monkeypatch):
    """删文件后必须重建索引；只失效缓存不够（磁盘索引仍带已删 chunk）。"""
    from services import knowledge_service as ks

    monkeypatch.setenv('EDU_DATA_DIR', str(tmp_path))
    subject = tmp_path / 'subjects' / 'sub1'
    subject.mkdir(parents=True)
    (subject / 'keep.txt').write_bytes('保留'.encode('utf-8'))
    (subject / 'gone.txt').write_bytes('删除'.encode('utf-8'))

    calls = {'rebuild': 0, 'delete_kb': 0}

    def fake_rebuild(subject_id):
        calls['rebuild'] += 1
        return {'success': True, 'subject_id': subject_id}

    def fake_delete_kb(subject_id):
        calls['delete_kb'] += 1
        return {'success': True}

    monkeypatch.setattr(ks, 'rebuild_subject_knowledge', fake_rebuild)
    monkeypatch.setattr(ks, 'delete_subject_knowledge', fake_delete_kb)
    monkeypatch.setattr(ks, '_invalidate_subject_index', lambda sid: True)

    ks.delete_subject_document('sub1', 'gone.txt')
    assert calls['rebuild'] == 1, '删除后必须重建索引，否则已删内容仍会被召回'
    assert calls['delete_kb'] == 0
    assert not (subject / 'gone.txt').exists()


def test_delete_last_document_clears_knowledge_base(tmp_path, monkeypatch):
    """删掉最后一份资料时应清理知识库，而不是留下空索引。"""
    from services import knowledge_service as ks

    monkeypatch.setenv('EDU_DATA_DIR', str(tmp_path))
    subject = tmp_path / 'subjects' / 'sub2'
    subject.mkdir(parents=True)
    (subject / 'only.txt').write_bytes('唯一'.encode('utf-8'))

    calls = {'rebuild': 0, 'delete_kb': 0}
    monkeypatch.setattr(ks, 'rebuild_subject_knowledge',
                        lambda sid: calls.__setitem__('rebuild', calls['rebuild'] + 1))
    monkeypatch.setattr(ks, 'delete_subject_knowledge',
                        lambda sid: calls.__setitem__('delete_kb', calls['delete_kb'] + 1))
    index = Path(ks.get_vector_store_dir('sub2'))
    index.mkdir(parents=True)
    # Single-file deletion now uses index-only removal; whole-subject deletion
    # would incorrectly remove unrelated assets and the rollback source.
    monkeypatch.setattr(ks, 'delete_knowledge_base',
                        lambda subject_id: calls.__setitem__('delete_kb', calls['delete_kb'] + 1))
    monkeypatch.setattr(ks, '_invalidate_subject_index', lambda sid: True)

    ks.delete_subject_document('sub2', 'only.txt')
    assert calls['delete_kb'] == 1 and calls['rebuild'] == 0, calls
