import { useId, useState } from 'react';
import { Button } from 'antd';

export default function PracticeHints({ text }: { text?: string }) {
  const [shown, setShown] = useState(0);
  const hintId = useId();
  const lines = (text || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return null;
  return <div className="practice-hints">
    <div id={hintId} role="region" aria-label="分步提示" aria-live="polite">
      {lines.slice(0, shown).map((line, index) => <p key={index}>{line}</p>)}
    </div>
    {shown < lines.length && <Button type="link" aria-expanded={shown > 0} aria-controls={hintId}
      onClick={() => setShown(shown + 1)}>
      {shown ? '再看一步提示' : '卡住时看提示'}
    </Button>}
  </div>;
}
