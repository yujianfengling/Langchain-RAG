import React, { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert, Button, Card, Col, Empty, List, Menu, Row, Space, Tag, Typography,
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
    exampleTitle: `${cur.requirement}；${p.anchor}（${p.verified ? '已核对具体页' : '待定位'}）`,
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

/** 官方数据视图：标准条目 → 对应能力 → 原始出处，一条线连起来。 */
const OfficialView: React.FC<{ focusQuestionId: string | null }> = ({ focusQuestionId }) => {
  const bundle = getBundle();
  const indicators = bundle?.curriculumMap?.indicators ?? [];
  const [textbook, setTextbook] = useState<TextbookSource | null>(null);

  const focusedIndicator = useMemo(() => {
    if (!focusQuestionId) return null;
    const q = bundle?.questions.find((x) => x.id === focusQuestionId);
    return q?.kpId || null;
  }, [focusQuestionId, bundle]);

  const [indicatorId, setIndicatorId] = useState<string>(() => focusedIndicator || indicators[0]?.indicator_id || '');
  const current = indicators.find((x) => x.indicator_id === indicatorId) || indicators[0];
  const [openTheme, setOpenTheme] = useState<string>(current?.theme_id || TOPICS[0]?.id || '');

  if (!current) {
    return <Empty description="官方课标映射未就绪" />;
  }

  const questions = current.questions || [];
  const cards = current.learning_cards || [];

  return (
    <>
      {focusQuestionId && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="从答卷题目反查出处"
          description={`已定位到题目 ${focusQuestionId} 所属考点（${focusedIndicator || '按首个指标展示'}），下方可打开对应教材原页。`} />
      )}
      <Row gutter={16}>
        <Col xs={24} md={7}>
          <Card size="small" title="三主题 · 九指标" style={{ marginBottom: 12 }}>
            <Menu
              mode="inline"
              selectedKeys={[current.indicator_id]}
              openKeys={[`theme-${openTheme}`]}
              onOpenChange={(keys) => {
                const last = keys[keys.length - 1];
                if (last?.startsWith('theme-')) setOpenTheme(last.replace('theme-', ''));
              }}
              onClick={({ key }) => { if (!key.startsWith('theme-')) setIndicatorId(key); }}
              items={TOPICS.map((t) => ({
                key: `theme-${t.id}`, label: t.name,
                children: indicators.filter((x) => x.theme_id === t.id).map((x) => ({
                  key: x.indicator_id,
                  label: `${x.indicator_id} · ${x.requirement}`,
                })),
              }))}
            />
          </Card>
        </Col>

        <Col xs={24} md={17}>
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Card size="small" title={<Space><BookOutlined />{current.indicator_id} · {current.requirement}</Space>}>
              <Title level={5} style={{ fontSize: 14, marginBottom: 6 }}>课标要求</Title>
              <Paragraph style={{ marginBottom: 8 }}>
                {current.theme_name}：{current.theme_goal || current.requirement}
              </Paragraph>
              <Space wrap size={[8, 8]}>
                <Tag color="blue">课标整理编号 {current.standard.code || '待定位'}</Tag>
                <Tag>课标文件页 {current.standard.pdf_page ?? '—'}</Tag>
                <Tag>课标正文页 {current.standard.printed ?? '—'}</Tag>
                <Tag color={current.locating_status === '已核对具体页' ? 'green' : 'default'}>
                  教材定位：{current.locating_status}
                </Tag>
              </Space>
              <div style={{ marginTop: 10 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {current.standard.note || '课标来源按官方课程要求表登记'}；教材范围 {current.textbook_range}。
                </Text>
              </div>
            </Card>

            <Card size="small" title="教材具体页（PDF 文件页 / 教材印刷页分开）">
              {current.textbook_pages.length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={`该指标尚未定位到具体教材页，仅有主题范围 ${current.textbook_range}`} />
              ) : (
                <List
                  dataSource={current.textbook_pages}
                  renderItem={(p, i) => (
                    <List.Item key={`${p.material_id}-${p.pdf_page}`}
                      actions={[<Button key="open" size="small" type="link" icon={<FileTextOutlined />}
                        onClick={() => setTextbook(sourceFromPage(current, i))}>打开教材原页</Button>]}>
                      <List.Item.Meta
                        title={`${p.material_id} · PDF 第 ${p.pdf_page} 页 · 教材印刷页 ${p.printed}`}
                        description={<span>{p.anchor} {p.verified ? '（已逐页核对）' : '（未逐页核对）'}</span>}
                      />
                    </List.Item>
                  )}
                />
              )}
            </Card>

            <Card size="small" title="关联题目与学习材料">
              {questions.length === 0 && cards.length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="官方题目表未把材料关联到该指标（不是数据丢失）" />
              ) : (
                <List
                  dataSource={[...questions, ...cards]}
                  renderItem={(m, i) => (
                    <List.Item key={`${m.material_id}-${i}`}>
                      <Space wrap>
                        <Tag>{m.material_id}</Tag>
                        <Text>{m.name}</Text>
                      </Space>
                    </List.Item>
                  )}
                />
              )}
            </Card>
          </Space>
        </Col>
      </Row>
      <TextbookDrawer open={!!textbook} source={textbook} onClose={() => setTextbook(null)} />
    </>
  );
};

/** 演示样例视图：保留原结构供回归使用，但不再自称「官方」，空状态文案改为中性。 */
const DemoView: React.FC<{ focusQuestionId: string | null }> = ({ focusQuestionId }) => {
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
          <Card size="small" title="赛题主题与考点" style={{ marginBottom: 12 }}>
            <Menu mode="inline" selectedKeys={[kpId]} onClick={({ key }) => setKpId(key)}
              items={TOPICS.map((t) => ({
                key: `topic-${t.id}`, label: t.name,
                children: listKnowledgePoints(t.id).map((k) => ({ key: k.id, label: k.name })),
              }))}
              openKeys={[`topic-${topicId}`]}
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
                <Title level={5} style={{ fontSize: 14 }}>课标要求</Title>
                {curriculum ? (
                  <><Tag color="blue">{curriculum.level}</Tag>
                    <Paragraph style={{ marginTop: 8 }}>{curriculum.requirement}</Paragraph></>
                ) : <Text type="secondary">该考点暂无课标要求</Text>}
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
      <div className="competition-header">
        <div>
          <div className="competition-eyebrow">考后分析 · 第三步</div>
          <Title level={2} className="competition-title">课标与教材</Title>
          <Text type="secondary">
            {official
              ? '三主题九指标：标准条目 → 对应能力 → 原始出处；可从答卷题目反查，关闭抽屉回到原位置。'
              : '按赛题主题看考点、课标要求与教材出处（当前为演示样例，非赛题数据）。'}
          </Text>
        </div>
        <Tag color={official ? 'green' : 'gold'}>{source}</Tag>
      </div>
      {official
        ? <OfficialView focusQuestionId={focusQuestionId} />
        : <DemoView focusQuestionId={focusQuestionId} />}
    </div>
  );
};

export default CurriculumPage;
