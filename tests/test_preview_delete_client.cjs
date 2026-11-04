// Execute the actual production effects/handler with deferred HTTP, without models.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const req = require('node:module').createRequire(path.resolve('frontend/package.json'));
const compile = source => {
  const code = req('esbuild').transformSync(source, {loader:'ts', format:'cjs'}).code;
  const mod = {exports:{}};
  new Function('module','exports',code)(mod, mod.exports);
  return mod.exports.default;
};
const tick = () => new Promise(setImmediate);
const defer = () => {let resolve; const promise = new Promise(r=>resolve=r); return {resolve,promise};};
const read = file => process.env.PROBE_REV
  ? require('node:child_process').execFileSync('git',['show',`${process.env.PROBE_REV}:${file}`],{encoding:'utf8'})
  : fs.readFileSync(file,'utf8');
(async()=>{
  const white = read('frontend/src/components/TutorWorkspace/WhiteboardModal.tsx').replace(/\r\n/g,'\n');
  const start = white.indexOf('  useEffect(() => {', white.indexOf('  const activePdfName'));
  const end = white.indexOf('\n  }, [activePdfName', start);
  assert(start > 0 && end > start);
  const effect = compile(`export default (env:any)=>{const {activePdfName,activePdf,subjectId,owner,open,sourceKey,setActivePdf,setError,apiClient}=env;${white.slice(start+'  useEffect(() => {'.length,end)}\n}`);
  let current={fileName:'lesson.pdf',owner:'A',data:new ArrayBuffer(8)}, errors=[], requests=[];
  const env={activePdfName:'lesson.pdf',subjectId:'B',owner:'user:B',open:true,sourceKey:'1',setActivePdf:v=>current=v,setError:e=>errors.push(e)};
  const first=defer();
  const cleanup=effect({...env,activePdf:current,apiClient:{get:async(url)=>{requests.push(url);return first.promise;}}});
  assert.equal(current,null,'old PDF cleared before new request');
  assert.equal(requests.length,1,'same filename in another subject must fetch');
  cleanup();
  effect({...env,subjectId:'C',owner:'user:C',apiClient:{get:async()=>new Blob(['NEW_C'])}});
  await tick();await tick();
  first.resolve(new Blob(['LATE_B']));
  for(let i=0;i<8;i++)await tick();
  assert.equal(current.owner,'user:C','late B cannot overwrite C');
  assert.equal(new TextDecoder().decode(current.data),'NEW_C');
  const version=current.version;
  effect({...env,subjectId:'C',owner:'user:C',apiClient:{get:async()=>new Blob(['REPLACED_C'])}});
  for(let i=0;i<8;i++)await tick();
  assert.notEqual(current.version,version,'same-name replacement gets a new content identity');

  const upload=read('frontend/src/components/UploadDropzone/index.tsx').replace(/\r\n/g,'\n');
  const a=upload.indexOf('  const handleDiscard = async () => {');
  const b=upload.indexOf('  const handleOpenSubject = () => {',a);
  const discard=compile(`export default (env:any)=>{const {job,setBusy,discardImport,message,setJob,onFinishedRef,describeError}=env;${upload.slice(a,b)};return handleDiscard;}`);
  let hidden=false, success=0, failed=0;
  const base={job:{job_id:'test'},setBusy(){},message:{success(){success++;},error(){failed++;}},setJob:v=>hidden=v===null,onFinishedRef:{current(){}},describeError:e=>e.message};
  await discard({...base,discardImport:async()=>{throw Error('cancel rejected');}})();
  assert.equal(hidden,false);assert.equal(success,0);assert.equal(failed,1);
  await discard({...base,discardImport:async()=>({deleted:['test.pdf'],retained:[]})})();
  assert(hidden);assert.equal(success,1);
  console.log('PASS: cross-subject, stale response, same-name replacement, delete failure, delete success (5)');
})().catch(e=>{console.error(e);process.exitCode=1;});
