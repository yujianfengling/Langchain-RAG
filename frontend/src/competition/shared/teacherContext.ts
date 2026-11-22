export function questionLabel(id: string): string {
  const match = /^Q0*(\d+)$/i.exec(id);
  return match ? `第 ${Number(match[1])} 题` : id;
}

export function rememberAnalysis(path: string): void {
  if (!/^\/class-analytics(?:\?|$)/.test(path)) return;
  sessionStorage.setItem('competition.analysis.returnTo', path);
}

export function analysisReturnPath(params: URLSearchParams): string {
  const explicit = params.get('returnTo');
  if (explicit && /^\/class-analytics(?:\?|$)/.test(explicit)) return explicit;
  const remembered = sessionStorage.getItem('competition.analysis.returnTo');
  if (remembered && /^\/class-analytics(?:\?|$)/.test(remembered)) return remembered;
  const next = new URLSearchParams();
  for (const key of ['source', 'quizId', 'classId', 'topic', 'scope', 'planTheme', 'reviewBudget', 'reviewStrategy']) {
    const value = params.get(key);
    if (value) next.set(key, value);
  }
  return `/class-analytics${next.size ? `?${next}` : ''}`;
}
