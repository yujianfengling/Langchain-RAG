import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from langchain_core.documents import Document
from services import import_vision as v


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('EDU_DATA_DIR',str(tmp_path))
    source=tmp_path/'subjects'/'s'/'file.txt';source.parent.mkdir(parents=True);source.write_text('old')
    image=tmp_path/'deferred_vision'/'j'/'image.png';image.parent.mkdir(parents=True);image.write_bytes(b'image')
    doc=Document(page_content='',metadata={'source':'file.txt','source_content_sha':hashlib.sha256(b'old').hexdigest(),'image_path':str(image)})
    with patch.object(v._POOL,'submit'):
        v.enqueue('j','s',[doc])
    return source,image


def rows():
    with v.connect() as c:return [dict(r) for r in c.execute('select * from import_vision_items')]


@pytest.mark.parametrize('action',['replace','delete','cancel','pause'])
def test_recheck_after_last_remote_batch(setup,action):
    source,image=setup;flags=[None];published=[]
    def enhance(docs):
        if action=='replace':source.write_text('new')
        if action=='delete':source.unlink()
        if action in ('cancel','pause'):flags[0]='cancelled' if action=='cancel' else 'paused'
        return [Document(page_content='IMAGE ONLY SECRET',metadata=docs[0].metadata)]
    with patch.object(v,'stopped',side_effect=lambda j:flags[0]),patch('rag.enrichment.multimodal_enricher.MultimodalEnricher') as e,patch('services.knowledge_service._build_index_with_rollback',side_effect=lambda *a:published.append(a)):
        e.return_value.enrich_images.side_effect=enhance
        v.run_pending()
    assert not published
    expected={'replace':'filtered','delete':'filtered','cancel':'cancelled','pause':'paused'}[action]
    assert rows()[0]['state']==expected
    assert v.status('j')['total']==1
    assert rows()[0]['remote_started']==1 and rows()[0]['remote_returned']==1
    if action in ('replace','delete'):assert v.status('j')['filtered_by_source_check']==1


def test_paused_not_dispatched_on_recovery(setup):
    with patch.object(v,'stopped',return_value='paused'),patch('rag.enrichment.multimodal_enricher.MultimodalEnricher') as e:
        v.run_pending();v.run_pending()
        e.assert_not_called()
    assert rows()[0]['state']=='paused'


def test_missing_version_fail_closed(setup):
    with v.connect() as c:c.execute("update import_vision_items set sha=''")
    with patch.object(v,'stopped',return_value=None):v.run_pending()
    assert rows()[0]['state']=='filtered'


def test_failure_terminal_not_infinite_retry(setup):
    with patch.object(v,'stopped',return_value=None),patch('rag.enrichment.multimodal_enricher.MultimodalEnricher') as e:
        e.return_value.enrich_images.side_effect=RuntimeError('provider rejected image')
        v.run_pending();v.run_pending()
        assert e.return_value.enrich_images.call_count==1
    assert rows()[0]['state']=='failed'
    assert 'provider rejected' in rows()[0]['error']
    assert setup[1].exists()


def test_publish_replay_and_delete_share_boundary(setup):
    import threading
    from types import SimpleNamespace
    from services.subject_mutation import subject_mutation
    source,_=setup;stored=[];entered=threading.Event();deleted=threading.Event()
    with v.connect() as c:
        c.execute("update import_vision_items set owner='test',state='running'")
    row=rows()[0]
    doc=Document(page_content='image content '*20,metadata={'source':'file.txt'})
    def build(sid,docs):
        entered.set()
        assert not deleted.wait(.05), 'delete must wait until paired publication finishes'
        stored[:]=docs
    def delete():
        entered.wait(3)
        with subject_mutation('s'):
            source.unlink();deleted.set()
    worker=threading.Thread(target=delete);worker.start()
    with patch.object(v,'stopped',return_value=None),patch('rag.vector_store.read_index_meta',return_value={'embedding_provider':'local'}),patch('rag.embedding.get_embedding_for_index',return_value=object()),patch('rag.vector_store.load_vector_store',side_effect=lambda **kw:SimpleNamespace(documents=list(stored))),patch('services.knowledge_service._build_index_with_rollback',side_effect=build):
        v.publish(row,doc,'test')
    worker.join(3)
    assert deleted.is_set()
    assert rows()[0]['state']=='succeeded'
    assert v.status('j')['counts']=={'succeeded':1}


def test_crash_replay_does_not_append_twice(setup):
    from types import SimpleNamespace
    with v.connect() as c:c.execute("update import_vision_items set owner='test',state='running'")
    row=rows()[0];doc=Document(page_content='already published',metadata={'enhancement_item_id':row['id']})
    with patch.object(v,'stopped',return_value=None),patch('rag.vector_store.read_index_meta',return_value={'embedding_provider':'local'}),patch('rag.embedding.get_embedding_for_index',return_value=object()),patch('rag.vector_store.load_vector_store',return_value=SimpleNamespace(documents=[doc])),patch('services.knowledge_service._build_index_with_rollback') as publish:
        v.publish(row,doc,'test');publish.assert_not_called()
    assert rows()[0]['state']=='succeeded'
