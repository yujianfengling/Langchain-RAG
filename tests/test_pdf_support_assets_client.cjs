// Exercise the real openPdf request assembly without running a browser worker.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const req = require('node:module').createRequire(path.resolve('frontend/package.json'));
const source = fs.readFileSync('frontend/src/components/PdfViewer/pdfjs.ts', 'utf8');
const body = source.slice(source.indexOf('export const openPdf'))
  .replaceAll('import.meta.env.DEV', 'false')
  .replaceAll('import.meta.env.BASE_URL', "'/'");
const code = req('esbuild').transformSync(body, {loader:'ts',format:'cjs'}).code;
const moduleStub = {exports:{}};
let options;
new Function('module','exports','getPdfJs', code)(moduleStub,moduleStub.exports,
  async()=>({getDocument(o){options=o;return {promise:Promise.resolve('opened')};}}));
(async()=>{
  const original = new Uint8Array([1,2,3]).buffer;
  assert.equal(await moduleStub.exports.openPdf(original),'opened');
  assert.equal(options.cMapUrl,'/assets/pdfjs/cmaps/');
  assert.equal(options.standardFontDataUrl,'/assets/pdfjs/standard_fonts/');
  assert.equal(options.cMapPacked,true);
  assert.notEqual(options.data.buffer,original,'retain caller buffer');
  assert.deepEqual([...options.data],[1,2,3]);
  console.log('PASS: production PDF request includes local CJK maps and fonts, caller buffer retained');
})().catch(e=>{console.error(e);process.exitCode=1;});
