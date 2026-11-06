import React, { useEffect, useState } from 'react';
import { getReadyStatus } from '../../api/health';
import QuietNotice from '../QuietNotice';

/** 能力键 → 人话 */
const CAP_LABEL: Record<string, string> = {
  llm: '文本模型',
  web_search: '联网搜题',
  vision: '图片识别',
  speech: '语音服务',
};

/**
 * 外部能力降级横幅（2026-10-02）
 *
 * 项目历史上多次出现"功能静默降级、只在日志里"的问题（AGENTS.md 有记录）：
 * knowledge_expander 坏了一年、VISION_MODE=ocr 让图片永远进不了库。
 * 后端现在把真实调用结果汇总到 /health/ready 的 external_capabilities，
 * 这里把它摆到界面上——降级必须被人看见。
 *
 * 只在确实降级时出现、可关闭；能力恢复后横幅自动消失。
 */
const SystemStatusBanner: React.FC = () => {
  const [degraded, setDegraded] = useState<{ key: string; reason: string }[]>([]);
  const [budgetOver, setBudgetOver] = useState<string[]>([]);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const ready: any = await getReadyStatus();
        const caps = ready?.external_capabilities || {};
        const bad = Object.keys(caps)
          .filter((k) => caps[k]?.ok === false)
          .map((k) => ({
            key: k,
            reason: caps[k]?.last_error || caps[k]?.reason || '未知原因',
          }));
        const kinds = ready?.usage_today?.kinds || {};
        const over = Object.keys(kinds).filter((k) => kinds[k]?.over);
        if (alive) {
          setDegraded(bad);
          setBudgetOver(over);
        }
      } catch {
        // 健康检查本身失败时保持静默：别在错误页面上再叠一层错误
      }
    };
    load();
    const timer = window.setInterval(load, 60000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  if (!degraded.length && !budgetOver.length) return null;
  if (dismissed) return null;

  const parts = degraded.map((d) => (CAP_LABEL[d.key] || d.key) + '：' + d.reason);
  if (budgetOver.length > 0) {
    parts.push(
      '今日外部调用已达预算上限（' + budgetOver.map((k) => CAP_LABEL[k] || k).join('、')
      + '），自动题库预生成已暂停',
    );
  }

  return (
    <QuietNotice onClose={() => setDismissed(true)}>
      部分功能降级。{parts.join('；')}
    </QuietNotice>
  );
};

export default SystemStatusBanner;
