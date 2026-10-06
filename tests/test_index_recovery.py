# -*- coding: utf-8 -*-
"""索引中断恢复的回归测试（2026-10-07 真实事故）。"""
import os

from services import knowledge_service as ks


def _write_index(dir_path, n_files=3):
    os.makedirs(dir_path, exist_ok=True)
    for i in range(n_files):
        with open(os.path.join(dir_path, f'f{i}.bin'), 'wb') as fh:
            fh.write(b'x' * 16)


def test_restore_orphaned_index_backup_when_live_empty(monkeypatch, tmp_path):
    """线上索引为空 + 存在 .bak → 必须从 .bak 恢复（真实事故：sub-kg5yy0t4）。"""
    monkeypatch.setenv('EDU_DATA_DIR', str(tmp_path))
    # 注意：_resolve_path('data/vector_store') 会把 data/ 前缀换成数据根目录，
    # 所以索引实际位于 <EDU_DATA_DIR>/vector_store。
    vs = tmp_path / 'vector_store'
    bm = tmp_path / 'bm25_index'
    live = vs / 'sub-demo'
    live.mkdir(parents=True)                 # 线上目录存在但为空（delete 之后、build 之前被杀）
    _write_index(vs / 'sub-demo.bak', 3)
    _write_index(bm / 'sub-demo.bak', 1)     # bm25 线上目录干脆不存在

    fixed = ks.restore_orphaned_index_backups()

    assert fixed == 2, fixed
    assert (live / 'f0.bin').exists(), 'vector store 未恢复'
    assert not (vs / 'sub-demo.bak').exists(), '.bak 应被消费掉'
    assert (bm / 'sub-demo' / 'f0.bin').exists(), 'bm25 未恢复'


def test_stale_backup_removed_when_live_index_complete(monkeypatch, tmp_path):
    """线上索引完整 + 残留 .bak → 保留线上索引，删掉过时备份。"""
    monkeypatch.setenv('EDU_DATA_DIR', str(tmp_path))
    vs = tmp_path / 'vector_store'
    _write_index(vs / 'sub-ok', 3)
    _write_index(vs / 'sub-ok.bak', 3)

    fixed = ks.restore_orphaned_index_backups()

    assert fixed == 0
    assert (vs / 'sub-ok' / 'f0.bin').exists()
    assert not (vs / 'sub-ok.bak').exists()


def test_no_backup_is_noop(monkeypatch, tmp_path):
    monkeypatch.setenv('EDU_DATA_DIR', str(tmp_path))
    (tmp_path / 'vector_store').mkdir(parents=True)
    assert ks.restore_orphaned_index_backups() == 0


def test_build_backup_keeps_last_good_copy_when_live_empty(monkeypatch, tmp_path):
    """线上为空时建库，不得把上一次的有效 .bak 覆盖成空目录（2026-10-07 事故）。"""
    from services import knowledge_service as ks

    monkeypatch.setenv('EDU_DATA_DIR', str(tmp_path))
    vs = tmp_path / 'vector_store'
    (vs / 'sub-x.bak').mkdir(parents=True)
    (vs / 'sub-x.bak' / 'f0.bin').write_bytes(b'x')
    (vs / 'sub-x').mkdir()          # 线上为空（上次建库被中断）

    monkeypatch.setattr(ks, '_attach_material_ids', lambda *a, **k: None)
    monkeypatch.setattr(ks, 'delete_knowledge_base', lambda **k: True)
    monkeypatch.setattr(ks, 'build_knowledge_base',
                        lambda **k: (_ for _ in ()).throw(RuntimeError('boom')))

    try:
        ks._build_index_with_rollback('sub-x', [object()])
    except Exception:
        pass

    # 关键断言：有效副本没有被空目录覆盖掉——要么 .bak 仍在，要么已回滚到线上
    survived = (vs / 'sub-x.bak' / 'f0.bin').exists() or (vs / 'sub-x' / 'f0.bin').exists()
    assert survived, '上一次的有效索引副本被空目录覆盖（回归）'
