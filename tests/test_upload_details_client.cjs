// Render the production component with mocked HTTP and click its actual button.
const assert=require('node:assert/strict');
const path=require('node:path');
const req=require('node:module').createRequire(path.resolve('frontend/package.json'));
let slots=[],cursor=0,effects=[],dirty=true,tree;
const equal=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
const hooks={
 createElement:(type,p,...children)=>({type,props:{...p,children}}),
 useState:init=>{const i=cursor++;if(!(i in slots))slots[i]=typeof init==='function'?init():init;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;dirty=true;}];},
 useRef:v=>{const i=cursor++;return slots[i]||=( {current:v} );},
 useMemo:(fn,deps)=>{const i=cursor++;if(!slots[i]||!equal(slots[i].deps,deps))slots[i]={deps,value:fn()};return slots[i].value;},
 useCallback:(fn,deps)=>hooks.useMemo(()=>fn,deps),
 useEffect:(fn,deps)=>{const i=cursor++;if(!slots[i]||!equal(slots[i].deps,deps)){const old=slots[i];slots[i]={deps};effects.push(()=>{old?.cleanup?.();slots[i].cleanup=fn();});}},
};
globalThis.__uploadHooks=hooks;
const job={job_id:'fixture',subject_id:'s',status:'partial',paused:true,items:[{item_id:'1',display_name:'fixture.pptx',status:'ready'}],
 totals:{total:1,ready:1,materials:1,files:1,containers:1},archive_summaries:[{archive:'fixture.zip',extracted:1,skipped:0}],warnings:['fixture warning']};
globalThis.__uploadHttp={get:async url=>({success:true,data:url.endsWith('/imports')?[job]:url.includes('capabilities')?{formats:[],archives:[],limits:{}}:job})};
globalThis.window={addEventListener(){},removeEventListener(){},setInterval:()=>0,clearInterval(){}};
const ui=names=>names.map(n=>`export const ${n}=${JSON.stringify(n)};`).join('\n');
(async()=>{
 const bundle=await req('esbuild').build({entryPoints:['frontend/src/components/UploadDropzone/index.tsx'],bundle:true,write:false,platform:'node',format:'cjs',jsx:'transform',plugins:[{name:'isolated-ui',setup(b){
  b.onResolve({filter:/^(react|react\/jsx-runtime|react-router-dom|antd|@ant-design\/icons)$|\.css$|^\.\/axios$/},a=>({path:a.path,namespace:'stub'}));
  b.onLoad({filter:/.*/,namespace:'stub'},a=>{
   let code='';
   if(a.path==='react/jsx-runtime')code='export const jsx=(type,props)=>({type,props:{...props,children:Array.isArray(props.children)?props.children:[props.children]}});export const jsxs=jsx;export const Fragment="Fragment";';
   else if(a.path==='react')code='export default globalThis.__uploadHooks;'+['useState','useRef','useEffect','useMemo','useCallback'].map(n=>`export const ${n}=globalThis.__uploadHooks.${n};`).join('');
   else if(a.path==='react-router-dom')code='export const useNavigate=()=>()=>{};';
   else if(a.path==='antd')code=ui(['Alert','Button','Popconfirm','Progress','Tag'])+';export const Typography={Text:"Text"};export const message={error(){},success(){}};';
   else if(a.path==='@ant-design/icons')code=ui(['DeleteOutlined','DownOutlined','FileTextOutlined','InboxOutlined','PauseCircleOutlined','PlayCircleOutlined','ReloadOutlined','RightOutlined']);
   else if(a.path==='./axios')code='export default globalThis.__uploadHttp;export const LONG_TASK_TIMEOUT=1800000;';
   return{contents:code,loader:'js'};
  });
 }}]});
 const mod={exports:{}};new Function('module','exports','require',bundle.outputFiles[0].text)(mod,mod.exports,require);
 const Component=mod.exports.default;
 const render=()=>{cursor=0;effects=[];dirty=false;tree=Component({subjectId:'s',writable:true});effects.forEach(e=>e());};
 const flush=async()=>{for(let i=0;i<8;i++){if(dirty)render();await new Promise(setImmediate);}};
 const walk=n=>!n||typeof n!=='object'?[]:[n,...(n.props?.children||[]).flat(Infinity).flatMap(walk)];
 const nodes=()=>walk(tree);
 const hasClass=c=>nodes().some(n=>(n.props?.className||'').split(' ').includes(c));
 const toggle=()=>nodes().find(n=>n.props?.className==='udz-details-toggle').props.onClick();
 await flush();
 assert(!hasClass('udz-counts'),'collapsed panel must hide detail counters');
 assert(!hasClass('udz-rows'),'collapsed panel must hide file rows');
 assert(hasClass('udz-progress'),'overall progress remains visible');
 assert(JSON.stringify(tree).includes('继续'),'resume remains available');
 toggle();await flush();
 assert(hasClass('udz-counts')&&hasClass('udz-rows')&&hasClass('udz-archive'));
 toggle();await flush();
 assert(!hasClass('udz-counts')&&!hasClass('udz-rows')&&!hasClass('udz-archive'));
 assert(hasClass('udz-progress'));
 job.status='running';job.paused=false;dirty=true;await flush();
 assert(nodes().some(n=>n.props?.className==='udz-details-toggle'),'running import can also collapse details');
 console.log('PASS: paused collapse/expand/collapse, progress and resume retained, running toggle available; no HTTP/model calls.');
})().catch(e=>{console.error(e);process.exitCode=1;});
