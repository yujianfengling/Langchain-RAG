export function printedPageLabel(printed?: string) {
  const match = printed?.trim().match(/^(上册|下册)?\s*(\d+)$/);
  return match ? [match[1] && '七年级' + match[1], '第 ' + match[2] + ' 页'].filter(Boolean).join(' · ') : printed || '印刷页待定位';
}

export function openPageLabel(printed?: string) {
  const page = printed?.match(/(\d+)\s*$/)?.[1];
  return page ? '打开第 ' + page + ' 页' : '打开教材原页';
}
