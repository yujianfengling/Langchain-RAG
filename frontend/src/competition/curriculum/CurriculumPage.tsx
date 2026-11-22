import React, { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert, Button, Card, Col, Empty, Input, List, Menu, Row, Space, Tag, Typography,
} from 'antd';
import { BookOutlined, FileTextOutlined, LinkOutlined } from '@ant-design/icons';
import { TOPICS } from '../shared/ids';
import { listKnowledgePoints } from '../classAnalytics/data';
import { listQuestionsByKp } from '../studentAnswers/data';
import { getCurriculum, getSourceByQuestion } from './data';
import TextbookDrawer from '../shared/TextbookDrawer';
import { getBundle, getSourceLabel } from '../dataSource';
import type { KnowledgePoint, TextbookSource } from '../shared/types';
import type { CurriculumIndicator } from '../dataSource';

const { Title, Text, Paragraph } = Typography;

/** 官方切片具体页 → 教材抽屉所需来源对象（三个页码概念分开存展）。 */
function sourceFromPage(cur: CurriculumIndicator, idx: number): TextbookSource {
  const p = cur.textbook_pages[idx];
  return {
    docId: p.material_id,
    docName: `赛方教材切片 ${p.material_id}`,
    page: p.pdf_page,
    exampleTitle: p.anchor,
    pdfUrl: `/api/competition/official-pdf/${p.material_id}`,
    printedPage: p.printed,
    verified: p.verified,
    standard: cur.standard.code ? {
      code: cur.standard.code,
      pdfPage: cur.standard.pdf_page ?? null,
      printed: cur.standard.printed ?? null,
    } : undefined,
  };
}

/** 只格式化真实印刷页，不根据文件页推算教材页码。 */
function printedPageTitle(printed?: string): string {
  if (!printed?.trim()) return '教材页码待定位';
  const match = printed.trim().match(/^(上册|下册)?\s*(\d+)$/);
  return match ? `教材${match[1] || ''}第${match[2]}页` : `教材${printed}`;
}

const OfficialView: React.FC<{ focusQuestionId: string | null }> = ({ focusQuestionId }) => {
  const bundle = getBundle();
  const indicators = bundle?.curriculumMap?.indicators ?? [];
  const [textbook, setTextbook] = useState<TextbookSource | null>(null);
  const [search, setSearch] = useState('');
  const focusedIndicator = indicators.find((x) =>
    x.indicator_id === bundle?.questions.find((q) => q.id === focusQuestionId)?.kpId);
  const [indicatorId, setIndicatorId] = useState(() => focusedIndicator?.indicator_id || indicators[0]?.indicator_id || '');
  const current = indicators.find((x) => x.indicator_id === indicatorId) || indicators[0];
  const [openThemes, setOpenThemes] = useState<string[]>(() => [current?.theme_id || '']);

  const focusedId = focusedIndicator?.indicator_id;
  const focusedTheme = focusedIndicator?.theme_id;
  React.useEffect(() => {
    if (!focusedId || !focusedTheme) return;
    setIndicatorId(focusedId);
    setOpenThemes([focusedTheme]);
    setSearch('');
  }, [focusQuestionId, focusedId, focusedTheme]);

  const keyword = search.trim().toLocaleLowerCase();
  const matching = indicators.filter((x) =>
    [x.requirement, x.theme_name, ...x.textbook_pages.map((p) => p.anchor)]
      .join(' ').toLocaleLowerCase().includes(keyword));
  const themeIds = Array.from(new Set(matching.map((x) => x.theme_id)));

  if (!current) return <Empty description="教材资料尚未就绪" />;

  return (
    <>
      {focusQuestionId && (
        <p className="teacher-muted">
          {focusedIndicator ? '已定位当前题的教材考点，关闭教材后仍停留在当前位置。' : '当前题暂未关联教材考点，可按章节查找。'}
        </p>
      )}
      <Row gutter={[24, 16]} className="teacher-resource-layout">
        <Col xs={24} md={8} lg={7}>
          <nav className="teacher-resource-nav" aria-label="章节与考点">
            <h2 className="teacher-section-heading">章节与考点</h2>
            <Input.Search allowClear placeholder="搜索考点" aria-label="搜索考点"
              value={search} onChange={(event) => setSearch(event.target.value)} style={{ marginBottom: 12 }} />
            {matching.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的考点" /> : (
              <Menu mode="inline" selectedKeys={[current.indicator_id]}
                openKeys={(keyword ? themeIds : openThemes).map((id) => `theme-${id}`)}
                onOpenChange={(keys) => setOpenThemes(keys.map((key) => key.replace('theme-', '')))}
                onClick={({ key }) => setIndicatorId(key)}
                items={themeIds.map((id) => ({
                  key: `theme-${id}`,
                  label: indicators.find((x) => x.theme_id === id)?.theme_name
                    || bundle?.topics.find((t) => t.id === id)?.name || '教材章节',
                  children: matching.filter((x) => x.theme_id === id).map((x) => ({
                    key: x.indicator_id,
                    label: <span style={{ whiteSpace: 'normal', lineHeight: 1.6 }}>{x.requirement}</span>,
                    style: { height: 'auto', minHeight: 40, paddingTop: 8, paddingBottom: 8 },
                  })),
                }))} />
            )}
          </nav>
        </Col>
        <Col xs={24} md={16} lg={17} style={{ minWidth: 0 }}>
          <section className="teacher-section" aria-label="教材条目">
            <h2 className="teacher-section-heading">{current.requirement}</h2>
            {current.textbook_pages.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={`暂无已定位教材页${current.textbook_range ? `，教材范围：${current.textbook_range}` : ''}`} />
            ) : (
              <List dataSource={current.textbook_pages} renderItem={(p, i) => (
                <List.Item key={`${p.material_id}-${p.pdf_page}`} style={{ display: 'block', padding: '20px 0' }}>
                  <Title level={4} style={{ margin: '0 0 8px' }}>{printedPageTitle(p.printed)}</Title>
                  <Paragraph>{p.anchor}</Paragraph>
                  <div className="teacher-actions">
                    <Button type="primary" icon={<FileTextOutlined />}
                      disabled={!Number.isInteger(p.pdf_page) || p.pdf_page < 1}
                      onClick={() => setTextbook(sourceFromPage(current, i))}>打开教材</Button>
                  </div>
                  <details style={{ marginTop: 12 }}>
                    <summary style={{ cursor: 'pointer' }}>出处详情</summary>
                    <div className="teacher-muted" style={{ marginTop: 8, lineHeight: 1.9 }}>
                      <div>教材编号：{p.material_id} · 考点编号：{current.indicator_id}</div>
                      <div>PDF 文件页：{p.pdf_page > 0 ? p.pdf_page : '待定位'} · 实际印刷页：{p.printed || '待定位'}</div>
                      <div>核对状态：{p.verified ? '已核对具体页' : '未核对具体页'}</div>
                    </div>
                  </details>
                </List.Item>
              )} />
            )}
          </section>
          <details key={current.indicator_id} className="teacher-section">
            <summary className="teacher-section-heading" style={{ cursor: 'pointer' }}>课程要求</summary>
            <Paragraph style={{ marginTop: 12 }}>{current.theme_goal || current.requirement}</Paragraph>
            <div className="teacher-muted" style={{ lineHeight: 1.9 }}>
              <div>课标编号：{current.standard.code || '待定位'}</div>
              <div>课标文件页：{current.standard.pdf_page ?? '待定位'} · 正文页：{current.standard.printed ?? '待定位'}</div>
              <div>课标核对状态：{current.standard.verified === true ? '已核对' : current.standard.verified === false ? '未核对' : '未提供'}</div>
              <div>教材定位：{current.locating_status || '待定位'}</div>
              {current.standard.note && <div>{current.standard.note}</div>}
            </div>
          </details>
          <section className="teacher-section">
            <h2 className="teacher-section-heading">相关题目与学习材料</h2>
            <List locale={{ emptyText: '暂无关联材料' }}
              dataSource={[...(current.questions || []), ...(current.learning_cards || [])]}
              renderItem={(m, i) => (
                <List.Item key={`${m.material_id}-${i}`}>
                  <div>
                    <Text>{m.name}</Text>
                    <details style={{ marginTop: 8 }}>
                      <summary style={{ cursor: 'pointer' }}>出处详情</summary>
                      <Text type="secondary">材料编号：{m.material_id}</Text>
                    </details>
                  </div>
                </List.Item>
              )} />
          </section>
        </Col>
      </Row>
      <TextbookDrawer open={!!textbook} source={textbook} onClose={() => setTextbook(null)} />
    </>
  );
};

/** 演示样例视图：保留原结构供回归使用，但不再自称「官方」，空状态文案改为中性。 */
const DemoView: React.FC<{ focusQuestionId: string | null }> = ({ focusQuestionId }) => {
  const [search, setSearch] = useState('');
  const [topicId, setTopicId] = useState(TOPICS[0].id);
  const kps = useMemo(() => listKnowledgePoints(topicId), [topicId]);
  const [kpId, setKpId] = useState<string>(kps[0]?.id || '');
  const [textbook, setTextbook] = useState<TextbookSource | null>(null);

  React.useEffect(() => {
    if (!focusQuestionId) return;
    for (const t of TOPICS) {
      const found = listKnowledgePoints(t.id).find((k) =>
        listQuestionsByKp(k.id).some((q) => q.id === focusQuestionId));
      if (found) { setTopicId(t.id); setKpId(found.id); break; }
    }
  }, [focusQuestionId]);

  const currentKp: KnowledgePoint | undefined = kps.find((k) => k.id === kpId) || kps[0];
  const curriculum = currentKp ? getCurriculum(currentKp.id) : undefined;
  const questions = currentKp ? listQuestionsByKp(currentKp.id) : [];
  const openTopic = (tid: string) => {
    setTopicId(tid);
    const first = listKnowledgePoints(tid)[0];
    setKpId(first?.id || '');
  };

  return (
    <>
      {focusQuestionId && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="从答卷题目反查出处"
          description={`已定位到题目 ${focusQuestionId} 所属考点，并可打开对应教材页。`} />
      )}
      <Row gutter={16}>
        <Col xs={24} md={7}>
          <Card size="small" title="章节与考点" style={{ marginBottom: 12 }}>
            <Input.Search allowClear placeholder="搜索考点" aria-label="搜索演示考点"
              value={search} onChange={(event) => setSearch(event.target.value)} style={{ marginBottom: 12 }} />
            <Menu mode="inline" selectedKeys={[currentKp?.id || '']} onClick={({ key }) => {
              const topic = TOPICS.find((t) => listKnowledgePoints(t.id).some((k) => k.id === key));
              if (topic) setTopicId(topic.id);
              setKpId(key);
            }}
              items={TOPICS.map((t) => ({
                key: `topic-${t.id}`, label: t.name,
                children: listKnowledgePoints(t.id).filter((k) => k.name.includes(search.trim()))
                  .map((k) => ({ key: k.id, label: k.name })),
              })).filter((t) => t.children.length > 0)}
              openKeys={search.trim() ? TOPICS.map((t) => `topic-${t.id}`) : [`topic-${topicId}`]}
              onOpenChange={(keys) => {
                const last = keys[keys.length - 1];
                if (last?.startsWith('topic-')) openTopic(last.replace('topic-', ''));
              }} />
          </Card>
        </Col>
        <Col xs={24} md={17}>
          {!currentKp ? <Card><Empty description="请选择考点" /></Card> : (
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Card size="small" title={<Space><BookOutlined />考点：{currentKp.name}</Space>}>
                <details key={currentKp.id}>
                  <summary style={{ cursor: 'pointer' }}>课程要求</summary>
                  {curriculum ? (
                    <><Text type="secondary">{curriculum.level}</Text>
                      <Paragraph style={{ marginTop: 8 }}>{curriculum.requirement}</Paragraph></>
                  ) : <Text type="secondary">该考点暂无课程要求</Text>}
                </details>
              </Card>
              <Card size="small" title="对应题目与教材出处">
                {questions.length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该考点暂无关联题目" />
                ) : (
                  <List dataSource={questions} renderItem={(q) => (
                    <List.Item key={q.id}>
                      <Space direction="vertical" size={4} style={{ width: '100%' }}>
                        <Space wrap>
                          <Tag>{q.id}</Tag>
                          <Text strong style={{ color: q.id === focusQuestionId ? '#b45309' : undefined }}>{q.stem}</Text>
                          {q.id === focusQuestionId && <Tag color="gold">来自答卷</Tag>}
                        </Space>
                        <Space wrap>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            <LinkOutlined /> 出处：{getSourceByQuestion(q.id)
                              ? `${getSourceByQuestion(q.id)!.docName} · 第 ${getSourceByQuestion(q.id)!.page} 页`
                              : '暂无出处'}
                          </Text>
                          {getSourceByQuestion(q.id) && (
                            <Button size="small" type="link" icon={<FileTextOutlined />}
                              onClick={() => setTextbook(getSourceByQuestion(q.id)!)}>打开教材指定页</Button>
                          )}
                        </Space>
                      </Space>
                    </List.Item>
                  )} />
                )}
              </Card>
            </Space>
          )}
        </Col>
      </Row>
      <TextbookDrawer open={!!textbook} source={textbook} onClose={() => setTextbook(null)} />
    </>
  );
};

const CurriculumPage: React.FC = () => {
  const [params] = useSearchParams();
  const focusQuestionId = params.get('questionId');
  const source = getSourceLabel();
  const official = source === '官方赛题数据' && !!getBundle()?.curriculumMap;

  return (
    <div className="competition-page">
      <div className="teacher-page-header">
        <Title level={2} className="competition-title">教材资料</Title>
        <Text className="teacher-muted">{official ? '正式教材' : '演示样例 · 非正式教材'}</Text>
      </div>
      {official
        ? <OfficialView focusQuestionId={focusQuestionId} />
        : <DemoView focusQuestionId={focusQuestionId} />}
    </div>
  );
};

export default CurriculumPage;
