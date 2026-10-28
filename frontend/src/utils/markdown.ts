/**
 * 统一的 Markdown 渲染器（含数学公式）
 *
 * 背景（2026-10-03）：AI 讲解会输出 LaTeX（`$$...$$` / `$...$`），而项目此前只用
 * 裸 markdown-it，公式被原样显示成 "$$I_{min}=\\frac{...}" 这种，学生根本看不懂。
 * 这里统一挂上 KaTeX，三个渲染点（导师工作区 / 聊天 / 教师端）共用。
 *
 * 分隔符用 dollars：`$$...$$` 块级、`$...$` 行内——这也是模型最常用的写法。
 */
import MarkdownIt from 'markdown-it';
import katex from 'katex';
// 该包没有自带类型声明，见 src/types/markdown-it-texmath.d.ts
import texmath from 'markdown-it-texmath';
import 'katex/dist/katex.min.css';

export function createMarkdown(options?: MarkdownIt.Options): MarkdownIt {
  const md = new MarkdownIt({
    html: false,
    linkify: true,
    breaks: true,
    ...(options || {}),
  });
  md.use(texmath, {
    engine: katex,
    delimiters: 'dollars',
    // 公式写错时不要把整条消息炸掉：原样显示那一段比白屏好
    katexOptions: { throwOnError: false, strict: false },
  });
  return md;
}

export default createMarkdown;
