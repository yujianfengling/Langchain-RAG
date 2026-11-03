// Execute the actual workspace with a small hook renderer and deferred API results.
// No browser/server/model requests; children are inert so only workspace behavior runs.
const assert = require('node:assert/strict');
const path = require('node:path');
const {createRequire} = require('node:module');
const req = createRequire(path.resolve('frontend/package.json'));
const tick = () => new Promise(setImmediate);
const deferred = () => { let resolve; const promise = new Promise(r => {resolve=r;}); return {promise,resolve}; };
let slots, cursor, effects, dirty, tree, props, api, writes, courses;
const equal = (a,b) => a && b && a.length===b.length && a.every((v,i)=>Object.is(v,b[i]));
const hooks = {
  createElement: (type, p, ...children) => ({type,props:{...p,children}}),
  useState: init => { const i=cursor++; if (!(i in slots)) slots[i]=typeof init==='function'?init():init; return [slots[i],v=>{const next=typeof v==='function'?v(slots[i]):v;if(!Object.is(next,slots[i])){slots[i]=next;dirty=true;}}]; },
  useRef: value => { const i=cursor++; return slots[i] ||= {current:value}; },
  useMemo: (fn,deps) => { const i=cursor++; if(!slots[i] || !equal(slots[i].deps,deps)) slots[i]={deps,value:fn()};return slots[i].value; },
  useCallback: (fn,deps) => hooks.useMemo(()=>fn,deps),
  useEffect: (fn,deps) => {const i=cursor++;if(!slots[i] || !equal(slots[i].deps,deps)){const old=slots[i];slots[i]={deps};effects.push(()=>{old?.cleanup?.();slots[i].cleanup=fn();});}},
};
globalThis.__resumeHooks=hooks;
globalThis.__resumeApi=new Proxy({}, {get:(_,k)=>(...args)=>api[k](...args)});
globalThis.__resumeCourse=()=>courses;
const user={id:'alice'};
globalThis.__resumeAuth=()=>({user});
globalThis.window={sessionStorage:{getItem:()=>null},addEventListener(){},removeEventListener(){}};
globalThis.localStorage={getItem:()=>null,removeItem(){}};
const ui = names => names.map(n=>`export const ${n}=${JSON.stringify(n)};`).join('\n');
(async()=>{
 const bundle=await req('esbuild').build({entryPoints:[path.resolve('frontend/src/components/TutorWorkspace/index.tsx')],bundle:true,platform:'node',format:'cjs',write:false,jsx:'transform',plugins:[{name:'workspace-isolation',setup(b){
   b.onResolve({filter:/.*/},a=>a.kind==='entry-point'?undefined:{path:a.path,namespace:'mock'});
   b.onLoad({filter:/.*/,namespace:'mock'},a=>{
     let code='';
     if(a.path==='react/jsx-runtime')code='export const jsx=(type,props)=>({type,props:{...props,children:Array.isArray(props.children)?props.children:[props.children]}});export const jsxs=jsx;export const Fragment="Fragment";';
     else if(a.path==='react')code='const r=globalThis.__resumeHooks;export default r;export const {useState,useRef,useEffect,useCallback,useMemo}=r;';
     else if(a.path==='antd')code=ui(['Alert','Button','Checkbox','Collapse','Drawer','Dropdown','Input','Popover','Segmented','Select','Space','Spin','Tag','Tooltip'])+'export const Typography={Text:"Text",Title:"Title"}; export const message={error(){},warning(){},success(){}};';
     else if(a.path==='@ant-design/icons')code=ui(['AudioOutlined','BookOutlined','BulbOutlined','CheckCircleOutlined','CloseOutlined','DatabaseOutlined','DownOutlined','FileTextOutlined','FormOutlined','HighlightOutlined','HistoryOutlined','MenuOutlined','MoreOutlined','PartitionOutlined','PlusOutlined','ReloadOutlined','RightOutlined','SendOutlined','StopOutlined','ReadOutlined','AimOutlined']);
     else if(a.path==='framer-motion')code='export const AnimatePresence="AnimatePresence",motion={div:"motion.div"};export const useReducedMotion=()=>true;';
     else if(a.path==='react-router-dom')code='export const useNavigate=()=>()=>{};';
     else if(a.path.includes('CourseContext'))code='export const useCourse=globalThis.__resumeCourse;';
     else if(a.path.includes('AuthContext'))code='export const useAuth=globalThis.__resumeAuth;';
     else if(a.path.includes('useIsNarrow'))code='export const useIsNarrow=()=>false;';
     else if(a.path==='../../api/tutor')code=['deleteArtifact','generateKnowledgeMap','getKnowledgeMap','getMapNode','setNodeStatus','cancelTurn','getTurn','getTutorSession','getTutorResume','listArtifacts','listTutorConversations','listTurns','streamTutorTurn','updateArtifact','updateTutorSession'].map(n=>`export const ${n}=(...a)=>globalThis.__resumeApi.${n}(...a);`).join('\n');
     else if(a.path==='./markdown')code='export const MarkdownText="MarkdownText";';
     else code='export default "'+a.path+'";';
     return {contents:code,loader:'js'};
   });
 }}]});
 const mod={exports:{}};new Function('module','exports','require',bundle.outputFiles[0].text)(mod,mod.exports,require);const Workspace=mod.exports.default;
 const render=()=>{cursor=0;effects=[];dirty=false;tree=Workspace(props);for(const e of effects)e();};
 const flush=async()=>{for(let n=0;n<18;n++){if(dirty)render();await tick();}};
 const walk=n=>!n||typeof n!=='object'?[]:[n,...(n.props?.children||[]).flat(Infinity).flatMap(walk)];
 const nodes=()=>walk(tree);
 const hasText=value=>JSON.stringify(tree).includes(value);
 const clickNew=()=>nodes().find(n=>n.type==='Button' && JSON.stringify(n.props.children).includes('新对话')).props.onClick();
 const setup=(extra={})=>{
   slots=[];effects=[];cursor=0;dirty=true;props={role:'student',...extra};writes=[];
   courses={status:'ready',courses:[],personalSubjects:[{subject_id:'one',name:'One'},{subject_id:'two',name:'Two'}],subjects:[],currentSubjectId:'',select(){},selectSubject(){}};
   api={getTutorResume:async()=>({subject_id:'one'}),getTutorSession:async sid=>({context:{conversation_id:sid+'-conv',current_node_id:'point'},scope:{},materials:[{doc_id:'x',status:'ready'}]}),getKnowledgeMap:async()=>({map:{nodes:[]}}),getMapNode:async()=>({node:{label:'Saved point'}}),listArtifacts:async()=>[],listTurns:async(s,l,n,c)=>[{id:'t',user_message:'saved question',answer:c?'answer '+c:'node history',questions:[{id:'q',question:'saved quiz',last_attempt:{answer:'B'}}]}],updateTutorSession:async p=>{writes.push(p);return {scope:{}};},listTutorConversations:async()=>({conversations:[],loose_turns:[]}),streamTutorTurn:()=>{throw Error('Restore must never generate');}};
 };
 setup();await flush();assert(hasText('answer one-conv'));assert(hasText('saved quiz'));assert.equal(writes.length,0,'automatic resume is read-only');
 // Session arrival after first paint must still restore, and node metadata cannot erase it.
 setup({initialSubjectId:'one'});let session=deferred();api.getTutorSession=()=>session.promise;await flush();assert(!hasText('answer one-conv'));session.resolve({context:{conversation_id:'one-conv',current_node_id:'point'},scope:{},materials:[{doc_id:'x',status:'ready'}]});await flush();assert(hasText('answer one-conv'));
 // New conversation is explicit and fences an already-running history fetch.
 setup({initialSubjectId:'one'});let history=deferred();api.listTurns=()=>history.promise;await flush();clickNew();await flush();history.resolve([{id:'late',answer:'late automatic answer'}]);await flush();assert(!hasText('late automatic answer'));assert(writes.some(p=>p.conversation_id===null));
 // Task deep link wins over the persisted subject and conversation.
 setup({initialSubjectId:'two',initialNodeId:'task-point'});let resumes=0;api.getTutorResume=async()=>{resumes++;return {subject_id:'one'};};await flush();assert.equal(resumes,0);assert(!hasText('answer two-conv'));assert(hasText('node history'));
 // After entering a task link, an explicit subject change must leave that link's intent behind.
 nodes().find(n=>n.type==='Select').props.onChange('one');await flush();assert(hasText('answer one-conv'));
 // A deep-link scope save can finish after a manual switch; it must not install the old node.
 setup({initialSubjectId:'two',initialDocIds:['f:task']});const scopeSave=deferred();api.updateTutorSession=p=>p.doc_ids?scopeSave.promise:Promise.resolve({scope:{}});api.getTutorSession=async sid=>({context:{conversation_id:sid+'-conv',current_node_id:sid+'-point'},scope:{},materials:[{doc_id:'x',status:'ready'}]});await flush();nodes().find(n=>n.type==='Select').props.onChange('one');await flush();scopeSave.resolve({scope:{}});await flush();assert.equal(nodes().find(n=>n.type==='./MapTree').props.currentNodeId,'one-point');assert(hasText('answer one-conv'));
 // Fast manual subject switch fences the old session, and restores the chosen subject.
 setup({initialSubjectId:'one'});session=deferred();api.getTutorSession=sid=>sid==='one'?session.promise:Promise.resolve({context:{conversation_id:'two-conv'},scope:{},materials:[{doc_id:'x',status:'ready'}]});await flush();nodes().find(n=>n.type==='Select').props.onChange('two');await flush();session.resolve({context:{conversation_id:'one-conv'},scope:{},materials:[]});await flush();assert(hasText('answer two-conv'));assert(!hasText('answer one-conv'));
 // Manual history selection wins over a late automatic conversation response.
 setup({initialSubjectId:'one'});history=deferred();api.listTurns=(s,l,n,c)=>c==='one-conv'?history.promise:Promise.resolve([{id:'picked',answer:'manual choice'}]);api.listTutorConversations=async()=>({conversations:[{conversation_id:'manual',title:'Choose manual'}],loose_turns:[]});await flush();nodes().find(n=>n.type==='Button' && JSON.stringify(n.props.children).includes('历史')).props.onClick();await flush();nodes().find(n=>n.type==='button' && JSON.stringify(n.props.children).includes('Choose manual')).props.onClick();await flush();history.resolve([{id:'late',answer:'late auto'}]);await flush();assert(hasText('manual choice'));assert(!hasText('late auto'));assert(writes.some(p=>p.conversation_id==='manual'));
 console.log('PASS 8 workspace cases: root, delayed session, new-dialog race, task deep link, leaving task link, delayed scope save, subject race, history race; no model requests');
})().catch(e=>{console.error(e);process.exitCode=1;});
