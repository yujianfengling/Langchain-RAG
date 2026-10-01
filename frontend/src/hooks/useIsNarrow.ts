import { useEffect, useState } from 'react';

/**
 * 窄屏判定（单一来源，避免各页面各写一套断点）。
 *
 * 断点取 900px：实测 800px 时侧边栏仍占 220~280px，会把内容挤到 981px 造成横向溢出
 * （按钮被挤出可视区），所以从 900px 起就切换成"顶栏 + 抽屉"的窄屏布局。
 */
export const NARROW_QUERY = '(max-width: 900px)';

export function useIsNarrow(query: string = NARROW_QUERY): boolean {
  const [narrow, setNarrow] = useState(
    () => (typeof window !== 'undefined' ? window.matchMedia(query).matches : false),
  );

  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = (e: MediaQueryListEvent) => setNarrow(e.matches);
    setNarrow(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [query]);

  return narrow;
}

export default useIsNarrow;
