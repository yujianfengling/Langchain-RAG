import React, { useMemo } from 'react';
import { Button, Segmented, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { QuestionCircleOutlined, TrophyOutlined } from '@ant-design/icons';
import type { Bundle } from '../dataSource';

const { Text, Title } = Typography;

export type RankScope = 'mine' | 'all';

export interface ClassRankingProps {
  bundle: Bundle;
  quizId: string;
  focusClassId: string;
  scope: RankScope;
  onScopeChange: (scope: RankScope) => void;
  className: (id: string) => string;
  onGoStudent: (studentId: string, questionId: string, classId: string) => void;
}

interface Row { key: string; rank: number; studentId: string; name: string; score: number; full: number; rate: number | null; firstQuestion: string }

const bandColor = (rate: number): string => (rate >= 85 ? '#389e0d' : rate >= 60 ? '#285c85' : rate >= 40 ? '#d48806' : '#cf1322');

/** 成绩排名：默认跟随顶栏讲评班级；切「全部班级对比」并列所有班（支持 3+ 班）。 */
const ClassRanking: React.FC<ClassRankingProps> = ({ bundle, quizId, focusClassId, scope, onScopeChange, className, onGoStudent }) => {
  const allClassIds = useMemo(() => [...new Set(bundle.students.map((s) => s.classId))].sort(), [bundle]);
  const classIds = useMemo(() => (scope === 'mine' ? [focusClassId] : allClassIds), [scope, focusClassId, allClassIds]);
  const tables = useMemo(() => classIds.map((cid) => {
    const members = bundle.students.filter((s) => s.classId === cid);
    const rows: Row[] = members.map((s) => {
      const srows = bundle.answers.filter((a) => a.quizId === quizId && a.studentId === s.id);
      const score = srows.reduce((n, a) => n + a.score, 0);
      const full = srows.reduce((n, a) => n + a.fullScore, 0);
      return { key: s.id, rank: 0, studentId: s.id, name: s.name || s.id, score, full,
        rate: full > 0 ? score / full * 100 : null, firstQuestion: srows[0]?.questionId || '' };
    }).sort((a, b) => (b.rate ?? -1) - (a.rate ?? -1));
    let rank = 0; let prev: number | null = null;
    rows.forEach((r, i) => {
      if (r.rate == null) { r.rank = 0; return; }
      if (prev == null || r.rate < prev) { rank = i + 1; prev = r.rate; }
      r.rank = rank;
    });
    return { cid, rows };
  }), [bundle, quizId, classIds]);

  return <section className="teacher-section teacher-ranking" aria-label="成绩排名" data-testid="class-ranking">
    <div className="teacher-section-heading">
      <Title level={4} style={{ margin: 0 }}><TrophyOutlined aria-hidden /> 成绩排名 · {scope === 'mine' ? className(focusClassId) : '全部班级'}</Title>
      <span className="teacher-ranking-tools">
        <Segmented
          value={scope}
          onChange={(v) => onScopeChange(v as RankScope)}
          options={[{ label: '本班', value: 'mine' }, { label: '全部班级对比', value: 'all' }]}
        />
        <Tooltip title="排名按个人得分率（有效得分÷对应满分）从高到低；因各人作答题目不同，不作整卷原始分名次；得分率相同者并列同名次；无有效记录者不参与排名。点击学生名查看个人学情档案。">
          <Text type="secondary"><QuestionCircleOutlined aria-hidden /> 排名口径：按得分率，并列同名次</Text>
        </Tooltip>
      </span>
    </div>
    <div className={'teacher-ranking-grid' + (tables.length === 1 ? ' is-single' : '')}>
      {tables.map(({ cid, rows }) => {
        const single = tables.length === 1;
        const columns: ColumnsType<Row> = [
          { title: '名次', dataIndex: 'rank', width: 60, render: (v: number) => v ? <Tag color={v === 1 ? 'gold' : v <= 3 ? 'blue' : 'default'}>{v}</Tag> : <Text type="secondary">—</Text> },
          { title: '学生', dataIndex: 'name', width: single ? 140 : undefined, render: (_, r) => <Button type="link" size="small" style={{ padding: 0 }} onClick={() => onGoStudent(r.studentId, r.firstQuestion, cid)}>{r.name}</Button> },
        ];
        if (single) columns.push({ title: '得分率水平（满格 100%）', render: (_, r) => r.rate == null ? <Text type="secondary">—</Text> : <div className="teacher-ranking-bar"><i style={{ width: `${Math.max(2, r.rate)}%`, background: bandColor(r.rate) }} /></div> });
        columns.push(
          { title: '得分/满分', width: 100, render: (_, r) => `${r.score}/${r.full}` },
          { title: '得分率', width: 90, render: (_, r) => r.rate == null ? <Text type="secondary">无记录</Text> : <Text strong>{r.rate.toFixed(1)}%</Text> },
        );
        return <div className="teacher-ranking-col" key={cid}>
          <div className="teacher-ranking-col-head"><strong>{className(cid)}</strong><Text type="secondary">{rows.filter((r) => r.rate != null).length}/{rows.length} 人有有效成绩</Text></div>
          <Table size="small" pagination={rows.length > 12 ? { pageSize: 12, showSizeChanger: false } : false} rowKey="key" dataSource={rows}
            rowClassName={(r) => (r.rank === 1 ? 'teacher-ranking-top' : '')}
            columns={columns} />
        </div>;
      })}
    </div>
  </section>;
};

export default ClassRanking;