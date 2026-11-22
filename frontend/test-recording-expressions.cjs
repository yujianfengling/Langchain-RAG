const assert = require('node:assert/strict');
const { buildSync } = require('esbuild');
const { Module } = require('node:module');
const path = require('node:path');
const source = path.join(__dirname, 'src/competition/livePretest/expr.ts');
const compiled = buildSync({ entryPoints: [source], bundle: true, platform: 'node', format: 'cjs', write: false });
const moduleUnderTest = new Module(source);
moduleUnderTest._compile(compiled.outputFiles[0].text, source);
const { parseLinear, sameLinear } = moduleUnderTest.exports;
const cases = [
  ['展开式与合并式等价', '6x-2-x-4', '5x-6', true],
  ['项序交换等价', '-6+5x', '5x-6', true],
  ['显式乘号等价', '6*x-2-x-4', '5x-6', true],
  ['括号分配等价', '3(2a-b)-2(a+2b)', '4a-7b', true],
  ['中间负号差异', '6x-2-x+4', '5x-6', false],
  ['最终符号差异', '5x+6', '5x-6', false],
];
for (const [name, a, b, expected] of cases) {
  assert.equal(sameLinear(parseLinear(a), parseLinear(b)), expected, name);
  console.log(`PASS | ${name}`);
}
for (const input of ['', '???', 'x*x', '1/x', '1/0', 'sin(x)', '('.repeat(513), '9'.repeat(400), `${'9'.repeat(200)}*${'9'.repeat(200)}x`]) {
  assert.equal(parseLinear(input), null, `应拒绝 ${input.slice(0, 40)}`);
  console.log(`PASS | 拒绝不支持或超界输入（长度${input.length}）`);
}
console.log('15/15 expression assertions passed');
