const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const {Module}=require('node:module');const {buildSync}=require('esbuild');
const dir=process.env.COMPETITION_OFFICIAL_BUNDLE_DIR;
if(!dir)throw Error('Set COMPETITION_OFFICIAL_BUNDLE_DIR to the private official bundle');
const read=n=>JSON.parse(fs.readFileSync(path.join(dir,n+'.json'),'utf8'));
const bundle={...Object.fromEntries(['students','classes','quizzes','topics','questions','answers','events','kps','anomalies'].map(n=>[n,read(n)])),diagnoses:read('diagnoses'),curriculumMap:read('curriculum_map')};
const source=path.join(__dirname,'src/competition/studentAnswers/studentProfileData.ts');
const code=buildSync({entryPoints:[source],bundle:true,platform:'node',format:'cjs',write:false}).outputFiles[0].text;
const mod=new Module(source);mod._compile(code,source);const {buildStudentProfile}=mod.exports;
let passed=0;const check=(name,condition)=>{assert.ok(condition,name);passed++};const mismatches=[];
for(const student of bundle.students){for(const quiz of bundle.quizzes){
 const profile=buildStudentProfile(bundle,student.id,quiz.id);
 const sourceEvents=bundle.events.filter(e=>e.studentId===student.id&&e.quizId===quiz.id);
 const scored=sourceEvents.filter(e=>['有效得分','已评分'].includes(e.status)&&!e.quarantined&&!e.outOfScope&&quiz.availableItems.includes(e.questionId)&&e.score!==null&&e.fullScore>0&&e.score>=0&&e.score<=e.fullScore);
 const history=profile.history.find(q=>q.id===quiz.id);
 check(`history ${student.id}/${quiz.id}`,history.totals.score===scored.reduce((n,e)=>n+e.score,0)&&history.totals.fullScore===scored.reduce((n,e)=>n+e.fullScore,0)&&history.totals.scoredCount===scored.length&&history.totals.recordCount===sourceEvents.length);
 for(const d of bundle.diagnoses.diagnoses.filter(d=>d.student_id===student.id&&d.node_id===quiz.id)){
   const row=profile.indicators.find(x=>x.id===d.indicator_id);
   if(row && (row.totals.score!==d.score||row.totals.fullScore!==d.full_score||row.totals.scoredCount!==d.event_count))mismatches.push({student:student.id,quiz:quiz.id,indicator:d.indicator_id,profile:[row.totals.score,row.totals.fullScore,row.totals.scoredCount],official:[d.score,d.full_score,d.event_count]});
 }
}}
if(mismatches.length)console.error(JSON.stringify(mismatches.slice(0,15)));
check('profile indicator statistics match persisted official diagnoses',mismatches.length===0);
const d01=buildStudentProfile(bundle,'D01','N01');const algebra=d01.indicators.find(x=>x.id==='T01I2');
check('D01 Q015 partial with insufficient coverage',algebra.totals.score===2&&algebra.totals.fullScore===3&&algebra.status==='记录不足'&&algebra.totals.scoredCount===1);
const d17=buildStudentProfile(bundle,'D17','N01');const relations=d17.indicators.find(x=>x.id==='T01I1');
check('D17 full score does not imply sufficient evidence',relations.totals.score===4&&relations.totals.fullScore===4&&relations.status==='记录不足');
check('untested inequality not zero',d17.indicators.find(x=>x.id==='T03I2').status==='本次未测');
const d17later=buildStudentProfile(bundle,'D17','N02');
check('later inequality no student records is not untested',d17later.indicators.find(x=>x.id==='T03I2').status==='无记录');
check('D17 no comparable questions does not create progress',d17later.comparableQuestionIds.length===0);
console.log(`${passed}/${passed} profile assertions passed`);
