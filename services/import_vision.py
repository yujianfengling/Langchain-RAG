"""Durable image enhancement items in the import database.

A lease fences workers; a subject mutation lock covers source validation and
paired index publication. Item IDs in index metadata make crash replay idempotent.
"""
import hashlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from services.subject_mutation import subject_mutation
from utils.logger import logger

_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='import-vision')
_REMOTE = ThreadPoolExecutor(max_workers=1, thread_name_prefix='import-vision-call')
LEASE_SECONDS = 60
MAX_ATTEMPTS = 3


def connect():
    from services import import_service as imp
    imp._init_db()
    c = imp._connect()
    c.execute('''CREATE TABLE IF NOT EXISTS import_vision_items (
      id TEXT PRIMARY KEY, job_id TEXT, subject_id TEXT, source TEXT, sha TEXT,
      document TEXT, result TEXT, state TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
      owner TEXT, heartbeat REAL, error TEXT, remote_started INTEGER DEFAULT 0,
      remote_returned INTEGER DEFAULT 0, updated REAL)''')
    c.commit()
    return c


def source_sha(subject_id, source):
    from config.path_config import get_subjects_dir
    base = Path(get_subjects_dir()).resolve() / subject_id
    path = (base / source).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file(): return ''
    return hashlib.sha256(path.read_bytes()).hexdigest()


def enqueue(job_id, subject_id, docs):
    with subject_mutation(subject_id), connect() as c:
        for doc in docs:
            meta = dict(doc.metadata)
            source = meta.get('source') or ''
            sha = meta.get('source_content_sha') or ''
            # Missing version is not silently upgraded to the current file version.
            ident = hashlib.sha256((job_id + source + sha + str(meta.get('image_path'))).encode()).hexdigest()
            c.execute('''INSERT OR IGNORE INTO import_vision_items
              (id,job_id,subject_id,source,sha,document,updated) VALUES(?,?,?,?,?,?,?)''',
              (ident, job_id, subject_id, source, sha,
               json.dumps({'page_content':doc.page_content,'metadata':meta},ensure_ascii=False),time.time()))
    return _POOL.submit(run_pending, job_id)


def stopped(job_id):
    from services import import_service as imp
    if imp._is_cancelled(job_id): return 'cancelled'
    if imp._is_pause_requested(job_id): return 'paused'
    return None


def owned(c, ident, owner):
    return c.execute('SELECT 1 FROM import_vision_items WHERE id=? AND owner=? AND state=?',
                     (ident,owner,'running')).fetchone() is not None


def update(ident, owner, **fields):
    fields['updated'] = time.time()
    with connect() as c:
        c.execute('UPDATE import_vision_items SET '+','.join(k+'=?' for k in fields)+
                  ' WHERE id=? AND owner=? AND state=\'running\'',
                  (*fields.values(),ident,owner))


def publish(row, doc, owner):
    from rag.vector_store import load_vector_store, _safe_dir_name, read_index_meta
    from rag.embedding import get_embedding_for_index
    from services import knowledge_service as ks
    from config.path_config import get_vector_store_dir
    # Same boundary used by deletion/replacement and index rebuilds.
    with subject_mutation(row['subject_id']), connect() as c:
        if not owned(c,row['id'],owner): return
        state = stopped(row['job_id'])
        if state:
            update(row['id'],owner,state=state)
            return
        if not row['sha'] or source_sha(row['subject_id'],row['source']) != row['sha']:
            update(row['id'],owner,state='filtered',error='source version changed or deleted')
            return
        path = str(Path(get_vector_store_dir()) / _safe_dir_name(row['subject_id']))
        meta = read_index_meta(path)
        if not meta: raise RuntimeError('Text index is unavailable; enhancement cannot replace it')
        store = load_vector_store(embeddings=get_embedding_for_index(meta), persist_dir=path)
        if store is None: raise RuntimeError('Text index could not be loaded')
        if not any(d.metadata.get('enhancement_item_id') == row['id'] for d in store.documents):
            doc.metadata['enhancement_item_id'] = row['id']
            # Existing staging/paired publish + rollback, never mutate the live FAISS in place.
            ks._build_index_with_rollback(row['subject_id'], [*store.documents,doc])
        update(row['id'],owner,state='succeeded',error=None)


def run_pending(job_id=None):
    from langchain_core.documents import Document
    from rag.enrichment.multimodal_enricher import MultimodalEnricher
    with connect() as c:
        rows=c.execute('SELECT * FROM import_vision_items WHERE state IN (\'pending\',\'running\',\'retry\',\'paused\')'+
                       (' AND job_id=?' if job_id else ''), (job_id,) if job_id else ()).fetchall()
    for row in rows:
        state=stopped(row['job_id'])
        if state:
            with connect() as c:
                c.execute("UPDATE import_vision_items SET state=?,updated=? WHERE id=? AND (state!='running' OR heartbeat<?)",
                          (state,time.time(),row['id'],time.time()-LEASE_SECONDS))
            continue
        owner=uuid.uuid4().hex
        with subject_mutation(row['subject_id']), connect() as c:
            changed=c.execute('''UPDATE import_vision_items SET owner=?,heartbeat=?,state='running',
              attempts=attempts+1,updated=? WHERE id=? AND attempts<? AND
              (state IN ('pending','retry','paused') OR (state='running' AND heartbeat<?))''',
              (owner,time.time(),time.time(),row['id'],MAX_ATTEMPTS,time.time()-LEASE_SECONDS)).rowcount
            if not changed:
                c.execute("UPDATE import_vision_items SET state='failed',error='retry limit reached' WHERE id=? AND attempts>=? AND (state!='running' OR heartbeat<?)",
                          (row['id'],MAX_ATTEMPTS,time.time()-LEASE_SECONDS))
                continue
        try:
            # Reject obsolete source before spending money, then recheck at publication.
            if not row['sha'] or source_sha(row['subject_id'],row['source']) != row['sha']:
                update(row['id'],owner,state='filtered',error='source version changed or deleted')
                continue
            if row['result']:
                data=json.loads(row['result']);doc=Document(**data)
            else:
                doc=Document(**json.loads(row['document']))
                if not Path(doc.metadata.get('image_path','')).is_file():
                    raise RuntimeError('Persisted image is missing')
                with subject_mutation(row['subject_id']), connect() as c:
                    if not owned(c,row['id'],owner):
                        continue
                    if stopped(row['job_id']):
                        update(row['id'],owner,state=stopped(row['job_id']))
                        continue
                    update(row['id'],owner,remote_started=1)
                    future=_REMOTE.submit(MultimodalEnricher().enrich_images,[doc])
                while True:
                    try:
                        doc=future.result(timeout=10)[0]
                        break
                    except TimeoutError:
                        update(row['id'],owner,heartbeat=time.time())
                update(row['id'],owner,remote_returned=1, result=json.dumps(
                    {'page_content':doc.page_content,'metadata':doc.metadata},ensure_ascii=False))
            if stopped(row['job_id']):
                update(row['id'],owner,state=stopped(row['job_id']))
                continue
            if doc.metadata.get('vision_status') in ('failed','timeout'):
                raise RuntimeError('Vision returned failure: '+json.dumps({k: val for k,val in doc.metadata.items() if k in ('vision_status','vision_error_type','vision_http_status','http_status','provider_used','model_used','vision_error')},ensure_ascii=False))
            if not doc.page_content.strip() or doc.metadata.get('is_content') is False:
                update(row['id'],owner,state='skipped')
                continue
            publish(row,doc,owner)
        except Exception as e:
            logger.exception(f'Image enhancement item {row["id"]} failed')
            update(row['id'],owner,state='failed',error=str(e)[:1000])
        # Only terminal rows release their image. The database and result remain queryable.
        with connect() as c:
            final=c.execute('SELECT state FROM import_vision_items WHERE id=?',(row['id'],)).fetchone()
        if final and final['state'] in ('succeeded','filtered','skipped','cancelled'):
            p=Path(json.loads(row['document'])['metadata'].get('image_path',''))
            from config.path_config import get_data_dir
            if p.is_relative_to(Path(get_data_dir())/'deferred_vision') and p.is_file(): p.unlink()


def recover():
    def resume_after_lease():
        with connect() as c:
            latest=c.execute("SELECT MAX(heartbeat) FROM import_vision_items WHERE state='running'").fetchone()[0]
        if latest:
            time.sleep(max(0, latest + LEASE_SECONDS - time.time() + 0.1))
        run_pending()
    return _POOL.submit(resume_after_lease)


def status(job_id):
    with connect() as c:
        rows=c.execute('SELECT state,count(*) n FROM import_vision_items WHERE job_id=? GROUP BY state',(job_id,)).fetchall()
        errors=c.execute('SELECT id,state,error,attempts,remote_started,remote_returned FROM import_vision_items WHERE job_id=?',(job_id,)).fetchall()
    counts={r['state']:r['n'] for r in rows}
    return {'counts':counts,'total':sum(counts.values()),'filtered_by_source_check':counts.get('filtered',0),
            'items':[dict(r) for r in errors]}


def coordinated_control(fn):
    import functools
    @functools.wraps(fn)
    def wrapped(job_id, *args, **kwargs):
        from services import import_service as imp
        imp._init_db()
        with imp._connect() as c:
            row=c.execute('SELECT subject_id FROM import_jobs WHERE job_id=?',(job_id,)).fetchone()
        if row:
            with subject_mutation(row['subject_id']): return fn(job_id,*args,**kwargs)
        return fn(job_id,*args,**kwargs)
    return wrapped
