from unittest.mock import patch
import pytest
from services import tutor_session as s
from api.schemas.tutor_schema import TutorStreamRequest


def test_node_omission_clear_and_subject_validation(tmp_path, monkeypatch):
    monkeypatch.setenv('EDU_DATA_DIR', str(tmp_path))
    s.save_context('u','a', {**s.default_context('u','a'), 'current_node_id':'a1'})
    with patch('services.knowledge_map.get_node',side_effect=lambda sid,n: {'node_id':n} if (sid,n) in [('a','a1'),('a','a2')] else None):
        assert s.resolve_node('u','a') == 'a1'
        assert s.resolve_node('u','a',None) is None
        assert s.resolve_node('u','a','a2') == 'a2'
        assert s.resolve_node('u','b') is None
        with pytest.raises(ValueError): s.resolve_node('u','b','a1')
    assert 'node_id' not in TutorStreamRequest(message='x',subject_id='a').model_fields_set
    assert 'node_id' in TutorStreamRequest(message='x',subject_id='a',node_id=None).model_fields_set


def test_session_api_keeps_omitted_and_clears_explicit(tmp_path,monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from api.routes import tutor
    from api.schemas.tutor_schema import TutorSessionUpdate
    monkeypatch.setenv('EDU_DATA_DIR',str(tmp_path))
    with patch.object(tutor,'require_subject_read',new=AsyncMock()),patch('services.knowledge_map.get_node',side_effect=lambda sid,n: {'node_id':n} if sid=='a' and n in ['a1','a2'] else None),patch.object(tutor.material_scope,'build_scope',return_value={'mode':'empty','selected':[],'invalid':[]}):
        for n in ['a1','a2']:
            result=asyncio.run(tutor.put_session(TutorSessionUpdate(subject_id='a',node_id=n),'u'))
            assert result['data']['context']['current_node_id']==n
        result=asyncio.run(tutor.put_session(TutorSessionUpdate(subject_id='a',ability='chat'),'u'))
        assert result['data']['context']['current_node_id']=='a2'
        result=asyncio.run(tutor.put_session(TutorSessionUpdate(subject_id='a',node_id=None),'u'))
        assert result['data']['context']['current_node_id'] is None
        with pytest.raises(tutor.HTTPException):
            asyncio.run(tutor.put_session(TutorSessionUpdate(subject_id='b',node_id='a2'),'u'))


def test_two_nodes_reach_prompt_and_tools(tmp_path,monkeypatch):
    from services import tutor_agent as a
    monkeypatch.setenv('EDU_DATA_DIR',str(tmp_path))
    prompts=[]
    class Provider:
        def stream_with_tools(self,messages,**kw):
            prompts.append(messages[0]['content'])
            yield {'type':'content','text':'mock answer'}
            yield {'type':'end','tool_calls':[]}
    def node(sid,n):return {'node_id':n,'label':'TOPIC_'+n} if sid=='s' and n in ['one','two'] else None
    scope={'mode':'empty','label':'only selected files','selected':[],'invalid':[],'signature':'test'}
    with patch('services.knowledge_map.get_node',side_effect=node),patch('services.material_scope.build_scope',return_value=scope),patch('services.model_service.get_model_provider',return_value=Provider()):
        for n in ['one','two',None]:
            events=list(a.stream_turn('u','s','explain',node_id=n))
            assert next(e for e in events if e['type']=='start')['node_id']==n
    assert 'TOPIC_one' in prompts[0] and 'TOPIC_two' in prompts[1]
    assert 'TOPIC_one' not in prompts[2] and 'TOPIC_two' not in prompts[2]
