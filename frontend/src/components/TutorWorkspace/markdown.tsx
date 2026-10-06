import React, { useMemo } from 'react';
import MarkdownIt from 'markdown-it';

/**
 * 伴学消息的 Markdown 渲染 + 可点击引用。
 *
 * 引用 [1] 会在正文里变成可点击角标，点击后打开右侧"引用原文"面板对应片段——
 * 学生能核对"回答依据的到底是哪段话"，而不是只看到编号。
 */
const md: MarkdownIt = new MarkdownIt({ html: false, linkify: true, breaks: true });

export const MarkdownText: React.FC<{
  text: string;
  onCitation?: (id: number) => void;
  className?: string;
}> = ({ text, onCitation, className }) => {
  const html = useMemo(() => {
    const raw = md.render(text || '');
    // 把 [1] [2] 变成可点击角标（只处理 1-99 的纯数字，避免误伤数组写法）
    return raw.replace(/\[(\d{1,2})\]/g,
      (_m, n) => '<span class="tutor-cite" data-cite="' + n + '" role="button" tabindex="0">[' + n + ']</span>');
  }, [text]);

  const handleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const el = (e.target as HTMLElement);
    const id = el?.getAttribute?.('data-cite');
    if (id && onCitation) onCitation(Number(id));
  };

  return (
    <div className={'markdown-body ' + (className || '')}
         onClick={handleClick}
         onKeyDown={(e) => { if (e.key === 'Enter') handleClick(e as any); }}
         dangerouslySetInnerHTML={{ __html: html }} />
  );
};

export default MarkdownText;
