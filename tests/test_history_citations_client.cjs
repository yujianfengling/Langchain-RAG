const fs = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const req = require('node:module').createRequire(path.resolve('frontend/package.json'));
const source = fs.readFileSync('frontend/src/components/TutorWorkspace/index.tsx','utf8').replace(/\r\n/g,'\n');
const compile = (name, ending) => {
  const start = source.indexOf(`  const ${name} = `);
  const end = source.indexOf(ending,start) + ending.length;
  assert(start>0 && end>start);
  const code = req('esbuild').transformSync(`export default (env:any)=>{
    const {getTurn,setPanelSources,setPanelCitedIds,lastSourcesRef,lastCitedRef,setFocusSource,toggleRightPanel,antdMessage,window,
      messages,historyFocusId,setBoardSources,setBoardCitedIds,setBoardOpen}=env;
    const useCallback=(fn:any)=>fn;
    ${source.slice(start,end)}
    return ${name};}`,{loader:'ts',format:'cjs'}).code;
  const mod={exports:{}};new Function('module','exports',code)(mod,mod.exports);return mod.exports.default;
};
const citation = compile('openCitation','  }, [toggleRightPanel]);');
const board = compile('openWhiteboard','\n  };');
(async()=>{
  for(const final of [true,false]){
    let sources;
    const complete=[{id:1,file_name:'slide.pptx',page:2},{id:3,file_name:'slide.pptx',page:2}];
    const delta=[complete[1]];
    const events=[{type:'sources',payload:{sources:complete.slice(0,1)}},{type:'sources',payload:{sources:delta}},
      ...(final?[{type:'done',payload:{sources:complete,cited_source_ids:[1,3]}}]:[])];
    const env={getTurn:async()=>({events}),setPanelSources:v=>sources=v,setPanelCitedIds(){},lastSourcesRef:{current:[]},lastCitedRef:{current:[]},
      setFocusSource(){},toggleRightPanel(){},antdMessage:{error:()=>assert.fail('unexpected error')},window:{requestAnimationFrame:f=>f()},
      messages:[],historyFocusId:'',setBoardSources:v=>sources=v,setBoardCitedIds(){},setBoardOpen(){}};
    await citation(env)({id:'hist-a-offline',sources:[]},1,{});
    assert.deepEqual(sources,final?complete:delta,'history citation uses complete final registry when available');
    await board(env)('offline');
    assert.deepEqual(sources,final?complete:delta,'history whiteboard uses the same registry');
  }
  console.log('PASS: restored citations and whiteboard prefer final source registry, unfinished history keeps fallback');
})().catch(e=>{console.error(e);process.exitCode=1;});
