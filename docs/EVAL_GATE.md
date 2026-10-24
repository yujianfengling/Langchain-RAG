# 检索黄金集门禁（EVAL GATE）

> 更新时间：2026-10-24
> 目标：防止「检索悄悄退化」——不是教学效果评测，也不替代真实模型/人工验收。

## 1. 它测什么

| 指标 | 含义 | 默认阈值 |
|---|---|---|
| Recall@5 | 期望资料是否进前 5 | ≥ 0.95 |
| Hit@1 | 期望资料是否排第 1 | ≥ 0.85 |
| KeywordHit@1 | 第 1 名资料是否包含答案关键词 | ≥ 0.85 |
| MRR | 期望资料排名的倒数均值 | ≥ 0.85 |
| PassRate | Hit@1 且 KeywordHit@1 的比例 | ≥ 0.90 |

当前 20 道题、4 份资料（3 份二叉树/ BST + 1 份无关干扰），基线见
[`tests/eval/baseline.json`](../tests/eval/baseline.json)。

## 2. 怎么跑

```bash
# 只看指标（不比对基线）
python tests/eval/golden_gate.py --baseline __none__

# 发布前门禁：低于基线 5 个百分点即失败（exit 1）
python tests/eval/golden_gate.py --baseline tests/eval/baseline.json

# 同时落盘结果
python tests/eval/golden_gate.py --json tests/eval/output/golden_gate.json
```

在 pytest 里也会跑（`tests/test_eval_gate.py`），所以 `pytest tests` 自带门禁。

## 2.5 引用 / Faithfulness 门禁

`tests/eval/faithfulness_gate.py` 审计回答里的引用是否真实、是否与原文有词面交集：

| 指标 | 含义 | 阈值 |
|---|---|---|
| Citation Validity | 引用的 [n] 是否真实存在于 sources | 1.0 |
| Citation Rate | 有引用的回答占比 | ≥ 0.8 |
| Faithfulness | 引用有效 + 至少一个引用片段词面支撑回答 | ≥ 0.8 |

```bash
python tests/eval/faithfulness_gate.py --baseline tests/eval/faithfulness_baseline.json
```

**边界**：这是词面代理指标，不能证明语义忠实；它只防「引用编号造假 / 回答与原文完全无关」。真正的 LLM-as-judge 需要另接模型，不在 CI 默认路径里。

## 3. 黄金集格式

`tests/eval/golden_set.json`：

```json
{
  "cases": [
    {
      "id": "bt-01",
      "question": "二叉树第 i 层最多有多少个结点？",
      "expected_source": "01_二叉树的定义与性质.txt",
      "keyword": "2^(i-1)"
    }
  ]
}
```

新增用例时必须同时满足：

1. `expected_source` 真实存在于 `tests/eval/dataset/materials/`；
2. `keyword` 是该资料里**能核对**的原文关键词，不要写泛化词（如「结点」）；
3. 题目不要和已有题重复问法，尽量覆盖不同小节。

## 4. 基线怎么更新

只有在**确认检索变好**时才更新 `baseline.json`：

1. 跑门禁，记录新指标；
2. 人工抽查 5 条以上 case 的 top1 是否真的对；
3. 同一次提交里更新 `baseline.json`，并在提交信息写明「为什么变好」。

**不要**为了让 CI 变绿直接下调基线或阈值。

## 5. 边界（必须知道）

- 这是 **BM25 单路**门禁，不等于线上 FAISS+BM25+RRF 的完整效果；它只防「明显退化」。
- 它只测检索；Faithfulness 由 `faithfulness_gate.py` 做**词面代理**审计，语义级答案质量仍由 `tests/eval/run_eval.py` 与人工抽查负责。
- 20 道题样本很小，指标抖动大；新增能力（如页面级检索）后应补对应 case，而不是只看总数。
- 页面级检索的评测要单独加：图片题需要 `expected_page` 与页面图/OCR 命中，不要混进这份纯文本 BM25 基线。

## 6. CI 接入

仓库已加 `.github/workflows/eval-gate.yml`，push / PR 会跑：

```bash
python tests/eval/golden_gate.py --baseline tests/eval/baseline.json
python tests/eval/faithfulness_gate.py --baseline tests/eval/faithfulness_baseline.json
```

CI 只安装 `rank-bm25` 与 `jieba`，不加载 torch/FAISS；这两道门禁因此很快。
如果 CI 环境没有 jieba，会退化为正则分词（指标可能变化，建议保留依赖）。
