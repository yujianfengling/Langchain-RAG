interface TeachingConnection {
  from: string;
  to: string;
  reason: string;
}

/** Suggested links among official indicators. These are teaching choices, not textbook order. */
export const teachingConnections: readonly TeachingConnection[] = [
  { from: 'T01I1', to: 'T01I2', reason: '先会读式子、代入检验，再学习去括号和合并同类项时怎样保持式子等价。' },
  { from: 'T01I1', to: 'T01I3', reason: '先说清字母和各项表示什么，再用整式解释题目里的数量关系。' },
  { from: 'T01I2', to: 'T01I3', reason: '题目需要化简时，先核对变形是否等价，再解释化简结果在原题中的意义。' },
  { from: 'T02I1', to: 'T02I2', reason: '消元后会得到一元一次方程，可用等式性质求出剩下的未知数。' },
  { from: 'T02I1', to: 'T02I3', reason: '若用一个未知数即可描述等量关系，可先练列一元一次方程并检验结果。' },
  { from: 'T02I2', to: 'T02I3', reason: '需要用两个未知数表达两条等量关系时，可列方程组，求解后回到题意检验。' },
  { from: 'T03I1', to: 'T03I2', reason: '先正确求出每个不等式的解集，再找出两者都满足的范围。' },
  { from: 'T03I1', to: 'T03I3', reason: '遇到单一限制时，先列并求解一个不等式，再解释实际能取哪些值。' },
  { from: 'T03I2', to: 'T03I3', reason: '遇到多个限制时，先求共同范围，再按题意核对端点能否取到。' },
];
