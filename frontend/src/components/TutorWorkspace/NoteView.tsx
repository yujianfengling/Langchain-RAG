import React from 'react';
import { Typography } from 'antd';

const { Text } = Typography;

/**
 * 学习笔记的可读渲染（2026-10-31）。
 *
 * 旧实现把 a.content 原样 JSON.stringify 进 textarea，学生看到的是
 * schema 而不是笔记。这里把结构化字段拼成 Markdown 正文：
 * concept/example/mistakes/class_tips 各成一节，字段缺失自然省略。
 * 只渲染文本，不执行任何模型输出的 HTML/脚本。
 */
export const noteToMarkdownText = (content: any): string => {
  if (!content || typeof content !== 'object') {
    return typeof content === 'string' ? content : '';
  }
  const note = content.note || content;
  const parts: string[] = [];
  const section = (title: string, body: unknown) => {
    const text = typeof body === 'string' ? body.trim() : '';
    if (!text) return;
    parts.push(`## ${title}\n\n${text}\n`);
  };
  if (note.title) parts.push(`# ${note.title}\n`);
  section('概念讲解', note.concept?.content ?? note.concept);
  section('例子', note.example?.content ?? note.example);
  section('易错点', note.mistakes?.content ?? note.mistakes);
  section('课堂提示', note.class_tips?.content ?? note.class_tips);
  // 兜底：结构不认识时把剩余字符串字段拼出来，不输出 JSON schema
  if (parts.length === 0) {
    for (const v of Object.values(note)) {
      if (typeof v === 'string' && v.trim()) parts.push(`${v.trim()}\n`);
      else if (v && typeof v === 'object' && typeof (v as any).content === 'string') {
        parts.push(`${(v as any).content.trim()}\n`);
      }
    }
  }
  return parts.join('\n');
};

/** Markdown → 简单安全渲染：只处理标题/加粗/换行，不注入 HTML。 */
const renderPlain = (md: string) => {
  const lines = md.split('\n');
  return lines.map((line, i) => {
    const escaped = line
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    if (escaped.startsWith('# ')) {
      return <div key={i} style={{ fontSize: 17, fontWeight: 650, margin: '10px 0 6px' }}>{escaped.slice(2)}</div>;
    }
    if (escaped.startsWith('## ')) {
      return <div key={i} style={{ fontSize: 14, fontWeight: 600, margin: '10px 0 4px', color: '#1f2937' }}>{escaped.slice(3)}</div>;
    }
    if (!escaped.trim()) return <div key={i} style={{ height: 6 }} />;
    // **加粗** 简单替换
    const html = escaped.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    return <div key={i} style={{ lineHeight: 1.75 }} dangerouslySetInnerHTML={{ __html: html }} />;
  });
};

const NoteView: React.FC<{ content: any }> = ({ content }) => {
  const md = noteToMarkdownText(content);
  if (!md.trim()) {
    return <Text type="secondary" style={{ fontSize: 12 }}>这份笔记没有可显示的内容</Text>;
  }
  return <div className="tutor-note-view">{renderPlain(md)}</div>;
};

export default NoteView;
