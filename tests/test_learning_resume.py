import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from api.routes import tutor
from api.schemas.tutor_schema import TutorSessionUpdate
from services import tutor_session as sessions


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, '_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(sessions, 'DB_PATH', str(tmp_path / 'tutor.db'))


def test_recent_position_account_isolated_and_reads_do_not_touch(isolated):
    sessions.update_context('alice', 'one', conversation_id='first')
    sessions.update_context('alice', 'two', conversation_id='second', current_node_id='node2')
    sessions.update_context('bob', 'other', conversation_id='private')
    before = sessions.load_context('alice', 'two')
    assert sessions.recent_subjects('alice') == ['two', 'one']
    assert sessions.recent_subjects('bob') == ['other']
    assert sessions.recent_subjects('new-user') == []
    assert sessions.load_context('alice', 'two') == before
    sessions.update_context('alice', 'one')
    assert sessions.recent_subjects('alice')[0] == 'one'


def test_resume_skips_forbidden_and_deleted_subjects(isolated):
    from services.subject_service import SubjectServiceError

    sessions.update_context('alice', 'allowed')
    sessions.update_context('alice', 'forbidden')
    sessions.update_context('alice', 'deleted')

    def subject(sid):
        if sid == 'deleted':
            raise SubjectServiceError('not found')
        return {'id': sid}

    async def read(sid, uid):
        assert uid == 'alice'
        if sid == 'forbidden':
            raise tutor.HTTPException(403, 'not enrolled')

    with patch('services.subject_service.get_subject', side_effect=subject), patch.object(tutor, 'require_subject_read', side_effect=read):
        result = asyncio.run(tutor.get_resume('alice'))['data']
    assert result['subject_id'] == 'allowed'
    assert result['notice']


def test_resume_does_not_swallow_server_failures(isolated):
    sessions.update_context('alice', 'one')
    with patch('services.subject_service.get_subject', return_value={}), patch.object(tutor, 'require_subject_read', new=AsyncMock(side_effect=tutor.HTTPException(500, 'server error'))):
        with pytest.raises(tutor.HTTPException) as error:
            asyncio.run(tutor.get_resume('alice'))
    assert error.value.status_code == 500


def test_new_conversation_null_is_persisted_omission_retains(isolated):
    sessions.update_context('alice', 'one', conversation_id='old', current_node_id='node')
    with patch.object(tutor, 'require_subject_read', new=AsyncMock()), patch.object(tutor.material_scope, 'build_scope', return_value={'mode': 'empty', 'selected': [], 'invalid': []}):
        asyncio.run(tutor.put_session(TutorSessionUpdate(subject_id='one'), 'alice'))
        assert sessions.load_context('alice', 'one')['conversation_id'] == 'old'
        asyncio.run(tutor.put_session(TutorSessionUpdate(subject_id='one', conversation_id=None), 'alice'))
        assert sessions.load_context('alice', 'one')['conversation_id'] is None
        assert sessions.load_context('alice', 'one')['current_node_id'] == 'node'


def test_stale_node_falls_back_without_mutating_history(isolated):
    sessions.update_context('alice', 'one', conversation_id='saved', current_node_id='deleted')
    before = sessions.load_context('alice', 'one')
    with patch.object(tutor, 'require_subject_read', new=AsyncMock()), patch('services.knowledge_map.get_node', return_value=None), patch.object(tutor.material_scope, 'build_scope', return_value={'mode': 'empty', 'selected': [], 'invalid': []}), patch.object(tutor.material_scope, 'list_scope_materials', return_value=[]):
        result = asyncio.run(tutor.get_session('one', 'alice'))['data']
    assert result['context']['current_node_id'] is None
    assert result['context']['conversation_id'] == 'saved'
    assert result['notice']
    assert sessions.load_context('alice', 'one') == before


def test_history_keeps_answer_and_account_conversation_filter(isolated):
    turn = sessions.create_turn('alice', 'one', 'chat', 'question', conversation_id='chosen')
    with sessions._get_db() as conn:
        conn.execute("UPDATE tutor_turns SET status='SUCCEEDED',answer=? WHERE id=?", ('saved answer', turn))
        conn.commit()
    sessions.update_context('alice', 'one', conversation_id='chosen')
    assert sessions.list_turns('alice', 'one', conversation_id='chosen')[0]['answer'] == 'saved answer'
    assert sessions.list_turns('bob', 'one', conversation_id='chosen') == []
    with sessions._get_db() as conn:
        before = json.dumps([dict(r) for r in conn.execute('SELECT * FROM tutor_turns')], sort_keys=True)
    sessions.recent_subjects('alice')
    with sessions._get_db() as conn:
        after = json.dumps([dict(r) for r in conn.execute('SELECT * FROM tutor_turns')], sort_keys=True)
    assert before == after
