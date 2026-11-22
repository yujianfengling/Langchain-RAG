/**
 * 受限线性表达式解析与等价比较（现场补测用）
 * ============================================================
 * 只支持「整式化简」范围内的线性表达式：整数/小数系数、单字母变量、
 * + - * /（除数必须为常数）、括号（分配律）、隐式乘法（2a、3(x+1)）、一元负号。
 * 变量×变量、除数为变量、函数等超出支持范围 → 返回 null（待人工核对），不猜测。
 * 等价口径：合并同类项后的规范形（系数映射+常数项）相同即等价，
 * 因此 4a-7b 与 -7b+4a、2*2a-7*b 等等价写法都判对。
 */

export interface Linear {
  terms: Record<string, number>;
  constant: number;
}

const EPS = 1e-9;

export function zero(): Linear {
  return { terms: {}, constant: 0 };
}

function isConst(l: Linear): boolean {
  return Object.values(l.terms).every((v) => Math.abs(v) < EPS);
}

function scale(l: Linear, k: number): Linear {
  const terms: Record<string, number> = {};
  Object.entries(l.terms).forEach(([v, c]) => { terms[v] = c * k; });
  return { terms, constant: l.constant * k };
}

function add(a: Linear, b: Linear, sign = 1): Linear {
  const terms: Record<string, number> = { ...a.terms };
  Object.entries(b.terms).forEach(([v, c]) => { terms[v] = (terms[v] || 0) + sign * c; });
  return { terms, constant: a.constant + sign * b.constant };
}

function mul(a: Linear, b: Linear): Linear | null {
  if (isConst(a)) return scale(b, a.constant);
  if (isConst(b)) return scale(a, b.constant);
  return null; // 变量×变量：超出线性支持范围
}

function div(a: Linear, b: Linear): Linear | null {
  if (!isConst(b) || Math.abs(b.constant) < EPS) return null;
  return scale(a, 1 / b.constant);
}

export function sameLinear(a: Linear, b: Linear): boolean {
  const keys = new Set([...Object.keys(a.terms), ...Object.keys(b.terms)]);
  for (const k of keys) {
    if (Math.abs((a.terms[k] || 0) - (b.terms[k] || 0)) > EPS) return false;
  }
  return Math.abs(a.constant - b.constant) < EPS;
}

/** 逐项系数差异（用于给「哪一步符号/系数错」的提示） */
export function diffTerms(a: Linear, b: Linear): string[] {
  const out: string[] = [];
  const keys = [...new Set([...Object.keys(a.terms), ...Object.keys(b.terms)])].sort();
  for (const k of keys) {
    const x = a.terms[k] || 0;
    const y = b.terms[k] || 0;
    if (Math.abs(x - y) > EPS) out.push(`${k} 项系数：输入 ${fmtNum(x)}，应为 ${fmtNum(y)}`);
  }
  if (Math.abs(a.constant - b.constant) > EPS) {
    out.push(`常数项：输入 ${fmtNum(a.constant)}，应为 ${fmtNum(b.constant)}`);
  }
  return out;
}

export function fmtNum(n: number): string {
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 1000) / 1000);
}

export function formatLinear(l: Linear): string {
  const parts: string[] = [];
  Object.keys(l.terms).sort().forEach((v) => {
    const c = l.terms[v];
    if (Math.abs(c) < EPS) return;
    parts.push(`${fmtNum(c)}${v}`);
  });
  if (Math.abs(l.constant) > EPS || parts.length === 0) parts.push(fmtNum(l.constant));
  return parts.join(' + ').replace(/\+ -/g, '- ');
}

/** 去掉参考解析里的中文前缀与尾部标点，只留表达式 */
export function stripPrefix(s: string): string {
  return s
    .replace(/^(去括号得|去括号后得|去括号后|合并同类项得|合并得|化简得|结果为|原式=|得)/, '')
    .replace(/[。．.;；\s]+$/, '')
    .trim();
}

type Tok = { t: 'num'; v: number } | { t: 'var'; v: string } | { t: 'op'; v: string };

function tokenize(src: string): Tok[] | null {
  const s = src
    .replace(/[−–—]/g, '-')
    .replace(/[×·]/g, '*')
    .replace(/÷/g, '/')
    .replace(/[（]/g, '(')
    .replace(/[）]/g, ')')
    .replace(/\s+/g, '');
  const toks: Tok[] = [];
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (/[0-9.]/.test(c)) {
      const m = /^\d+(\.\d+)?/.exec(s.slice(i));
      if (!m) return null;
      toks.push({ t: 'num', v: parseFloat(m[0]) });
      i += m[0].length;
    } else if (/[a-zA-Z]/.test(c)) {
      toks.push({ t: 'var', v: c });
      i += 1;
    } else if ('+-*/()'.includes(c)) {
      toks.push({ t: 'op', v: c });
      i += 1;
    } else {
      return null; // 不支持的字符
    }
  }
  return toks;
}

/** 解析为规范线性形；不支持/语法错误返回 null */
export function parseLinear(input: string): Linear | null {
  if (input.length > 512) return null;
  const toks = tokenize(input || '');
  if (!toks || toks.length === 0 || toks.length > 256) return null;
  let pos = 0;
  const peek = () => toks[pos];
  const eat = () => toks[pos++];

  function factor(): Linear | null {
    const tk = peek();
    if (!tk) return null;
    if (tk.t === 'op' && (tk.v === '-' || tk.v === '+')) {
      eat();
      const f = factor();
      if (!f) return null;
      return tk.v === '-' ? scale(f, -1) : f;
    }
    if (tk.t === 'op' && tk.v === '(') {
      eat();
      const e = expr();
      if (!e) return null;
      const close = eat();
      if (!close || close.t !== 'op' || close.v !== ')') return null;
      return e;
    }
    if (tk.t === 'num') {
      eat();
      return { terms: {}, constant: tk.v };
    }
    if (tk.t === 'var') {
      eat();
      return { terms: { [tk.v]: 1 }, constant: 0 };
    }
    return null;
  }

  function term(): Linear | null {
    const first = factor();
    if (!first) return null;
    let acc: Linear = first;
    for (;;) {
      const tk = peek();
      if (!tk) break;
      if (tk.t === 'op' && (tk.v === '*' || tk.v === '/')) {
        eat();
        const right = factor();
        if (!right) return null;
        const r: Linear | null = tk.v === '*' ? mul(acc, right) : div(acc, right);
        if (!r) return null;
        acc = r;
      } else if (tk.t === 'num' || tk.t === 'var' || (tk.t === 'op' && tk.v === '(')) {
        // 隐式乘法：2a、3(x+1)
        const right = factor();
        if (!right) return null;
        const r: Linear | null = mul(acc, right);
        if (!r) return null;
        acc = r;
      } else break;
    }
    return acc;
  }

  function expr(): Linear | null {
    let left = term();
    if (!left) return null;
    for (;;) {
      const tk = peek();
      if (!tk || tk.t !== 'op' || (tk.v !== '+' && tk.v !== '-')) break;
      eat();
      const right = term();
      if (!right) return null;
      left = add(left, right, tk.v === '+' ? 1 : -1);
    }
    return left;
  }

  const result = expr();
  if (!result || pos !== toks.length || !Number.isFinite(result.constant)
    || Object.values(result.terms).some((c) => !Number.isFinite(c))) return null;
  // 清理近零系数
  const terms: Record<string, number> = {};
  Object.entries(result.terms).forEach(([v, c]) => { if (Math.abs(c) > EPS) terms[v] = c; });
  return { terms, constant: Math.abs(result.constant) > EPS ? result.constant : 0 };
}
