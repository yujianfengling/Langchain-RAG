import threading
import time
from unittest.mock import patch
import pytest
from services import tutor_session as s, turn_execution as tx, tutor_agent as a


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(s,'DB_PATH',str(tmp_path/'tutor.db'))
    monkeypatch.setattr(s,'_DATA_DIR',str(tmp_path))


def test_terminal_compare_and_set_and_partial_cancel():
    t=s.create_turn('u','s','chat','question')
    s.append_event(t,'delta',{'content':'partial'})
    assert s.request_cancel(t,'u')
    assert not s.finish_turn(t,s.STATE_SUCCEEDED,answer='late')
    row=s.get_turn(t);assert row['status']=='CANCELLED' and row['answer']=='partial'
    assert not s.finish_turn(t,s.STATE_FAILED,error='late error')
    t=s.create_turn('u','s','chat','normal')
    assert s.finish_turn(t,s.STATE_SUCCEEDED,answer='complete')
    assert not s.finish_turn(t,s.STATE_CANCELLED)
    assert s.get_turn(t)['answer']=='complete'


@pytest.mark.parametrize('phase',['before_first','during_stream','tool'])
def test_cancel_blocked_or_late_work(phase,tmp_path):
    ex=tx.Execution('test',10);started=threading.Event();release=threading.Event();written=[]
    def blocked():
        started.set();release.wait(2)
        tx.check();written.append('late')
        yield 'late'
    stream=tx.iterate(blocked(),ex)
    result=[]
    def consume():
        try:result.extend(stream)
        except tx.TurnCancelled:result.append('cancelled')
    worker=threading.Thread(target=consume);worker.start();assert started.wait(1)
    ex.cancel();worker.join(1);release.set()
    assert result==['cancelled'] and not written


def test_timeout_before_first_event():
    ex=tx.Execution('timeout',.02)
    def stream():time.sleep(.2);yield 'late'
    with pytest.raises(tx.TurnTimeout):list(tx.iterate(stream(),ex))


def test_cancelled_tool_cannot_commit(tmp_path):
    ex=tx.Execution('tool',10)
    with tx.bind(ex):
        conn=tx.connect(tmp_path/'tool.db');conn.execute('create table items(x)');conn.commit()
        conn.execute("insert into items values('late')");ex.cancel()
        with pytest.raises(tx.TurnCancelled):conn.commit()
        conn.rollback();assert conn.execute('select count(*) from items').fetchone()[0]==0


def test_recover_only_proven_dead_owner():
    live=s.create_turn('u','s','chat','live');dead=s.create_turn('u','s','chat','dead');legacy=s.create_turn('u','s','chat','unknown')
    c=s._get_db();c.execute("update tutor_turns set owner_pid=99999 where id=?",(dead,));c.execute('update tutor_turns set owner_pid=NULL where id=?',(legacy,));c.commit();c.close()
    from services.process_identity import identity
    def probe(pid):return ('dead',None) if pid==99999 else identity(pid)
    with patch('services.process_identity.identity',side_effect=probe):assert s.recover_interrupted_turns()==[dead]
    assert s.get_turn(live)['status']=='RUNNING' and s.get_turn(legacy)['status']=='RUNNING'
    assert s.get_turn(dead)['status']=='FAILED'


def test_agent_incomplete_stream_fails_with_partial():
    class Provider:
        def stream_with_tools(self,*args,**kw):yield {'type':'content','text':'partial'}
    scope={'mode':'empty','label':'none','selected':[],'invalid':[]}
    with patch('services.material_scope.build_scope',return_value=scope),patch('services.model_service.get_model_provider',return_value=Provider()):
        events=list(a.stream_turn('u','s','question',node_id=None))
    t=s.get_turn(events[0]['turn_id']);assert t['status']=='FAILED' and t['answer']=='partial'
    assert not any(e['type']=='done' for e in events)


def test_sse_disconnect_closes_turn_without_waiting_for_upstream():
    import asyncio
    from api.routes import tutor
    from api.schemas.tutor_schema import TutorStreamRequest
    started=threading.Event();release=threading.Event();ids=[]
    def blocked(*args,**kw):
        t=s.create_turn('u','s','chat','question');ids.append(t)
        yield {'type':'start','turn_id':t}
        started.set();release.wait(2)
        s.finish_turn(t,s.STATE_SUCCEEDED,answer='late')
        yield {'type':'done','turn_id':t,'answer':'late'}
    async def run():
        gen=tutor._tutor_stream_async(TutorStreamRequest(subject_id='s',message='question'),'u','student')
        await anext(gen)
        await gen.aclose()
    with patch.object(tutor.tutor_agent,'stream_turn',side_effect=blocked):
        asyncio.run(run())
        assert s.get_turn(ids[0])['status']=='CANCELLED'
        release.set()
    time.sleep(.05)
    assert s.get_turn(ids[0])['status']=='CANCELLED'


def test_process_exit_is_recovered_without_signalling_any_process(tmp_path,monkeypatch):
    import os,subprocess,sys
    data=tmp_path/'child';data.mkdir()
    env=dict(os.environ,EDU_DATA_DIR=str(data))
    code="from services.tutor_session import create_turn; print(create_turn('u','s','chat','test'))"
    r=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stderr
    turn=r.stdout.strip().splitlines()[-1]
    monkeypatch.setattr(s,'DB_PATH',str(data/'tutor.db'))
    monkeypatch.setattr(s,'_DATA_DIR',str(data))
    assert s.recover_interrupted_turns()==[turn]
    assert s.get_turn(turn)['status']=='FAILED'
    assert not s.recover_interrupted_turns()


def test_model_eof_without_end_is_not_success():
    from services.model_service import OpenAICompatibleProvider, ModelAPIError
    class Resp:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def iter_lines(self,**kw):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}'
    provider=OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider.api_key='test-only';provider.config={'model_name':'test','base_url':'http://test.invalid'};provider.model_name='deepseek'
    with patch('services.model_service.requests.post',return_value=Resp()):
        stream=provider.stream_with_tools([{'role':'user','content':'test'}])
        assert next(stream)['text']=='partial'
        with pytest.raises(ModelAPIError,match='结束标记'):list(stream)


def test_http_evidence_never_copies_message_or_credentials():
    import requests,json
    from services.model_service import http_error_evidence
    r=requests.Response();r.status_code=404;r._content=json.dumps({'error':{'code':'DeploymentNotFound','message':'secret user text sk-private'}}).encode()
    evidence=http_error_evidence(r)
    assert evidence['code']=='DeploymentNotFound'
    assert 'secret' not in str(evidence) and 'message' not in evidence


def test_agent_cancel_during_generation_keeps_partial_and_blocks_late_end():
    ready=threading.Event();release=threading.Event();events=[]
    class Provider:
        def stream_with_tools(self,*args,**kw):
            yield {'type':'content','text':'partial'}
            ready.set();release.wait(2)
            yield {'type':'end','content':'late answer','tool_calls':[]}
    scope={'mode':'empty','label':'none','selected':[],'invalid':[]}
    def run():events.extend(a.stream_turn('u','s','question',node_id=None))
    with patch('services.material_scope.build_scope',return_value=scope),patch('services.model_service.get_model_provider',return_value=Provider()):
        worker=threading.Thread(target=run);worker.start();assert ready.wait(2)
        c=s._get_db();row=c.execute('select id from tutor_turns').fetchone();c.close()
        # Wait for the delivered delta to reach persistence before cancellation.
        for _ in range(100):
            if any(e['type']=='delta' for e in s.list_events(row['id'])):break
            time.sleep(.01)
        assert s.request_cancel(row['id'],'u')
        worker.join(1);release.set()
    assert not worker.is_alive()
    assert s.get_turn(row['id'])['status']=='CANCELLED'
    assert s.get_turn(row['id'])['answer']=='partial'
    assert not any(e['type']=='done' for e in events)


def test_sse_eof_without_terminal_is_failed():
    import asyncio
    from api.routes import tutor
    from api.schemas.tutor_schema import TutorStreamRequest
    ids=[]
    def truncated(*args,**kw):
        t=s.create_turn('u','s','chat','question');ids.append(t)
        yield {'type':'start','turn_id':t}
    async def run():
        return [e async for e in tutor._tutor_stream_async(TutorStreamRequest(subject_id='s',message='question'),'u','student')]
    with patch.object(tutor.tutor_agent,'stream_turn',side_effect=truncated):
        events=asyncio.run(run())
    assert s.get_turn(ids[0])['status']=='FAILED'
    assert 'error' in events[-1]
