# 云端 Embedding（阿里云百炼 text-embedding-v4）

目标：首次入库不再依赖本地算力。**默认走云端**，本地 BGE 保留为显式可选项。

## 一、你需要配置的字段（.env）

必填一项：

    DASHSCOPE_API_KEY=sk-xxxxxxxx        # 阿里云百炼控制台 -> API-KEY 管理 申请

可选（都有默认值，通常不用改）：

    EMBEDDING_PROVIDER=cloud             # cloud(默认) | local(本地 BGE)
    EMBEDDING_CLOUD_MODEL=text-embedding-v4
    EMBEDDING_CLOUD_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    EMBEDDING_CLOUD_DIMENSIONS=1024      # 可选 2048/1536/1024/768/512/256/128/64
    EMBEDDING_CLOUD_MAX_ROWS=10          # 官方硬上限，勿改大
    EMBEDDING_CLOUD_CONCURRENCY=4        # 受控并发
    EMBEDDING_CLOUD_MAX_RPM=60           # 客户端限流（按你账号的实际 RPM 调整）
    EMBEDDING_CLOUD_MAX_RETRIES=4        # 429/5xx 退避重试次数
    EMBEDDING_CLOUD_TIMEOUT=60
    EMBEDDING_CLOUD_BUDGET_TOKENS=0      # >0 时达到预算即暂停（0=不限）

**地域**：百炼账号分北京/新加坡等地域，接口域名不同。如果你的账号不在默认地域，
把 EMBEDDING_CLOUD_BASE_URL 改成控制台给出的地址（含 WorkspaceId 的自定义域名亦可）。

## 二、官方限制（按百炼文档核对，2026-10-08）

| 项目 | 值 |
|---|---|
| 单次请求最多行数 | **10** |
| 单行最多 token | 8,192 |
| 维度 | 2048 / 1536 / **1024(默认)** / 768 / 512 / 256 / 128 / 64 |
| 计费 | 按输入 token；新加坡 $0.07 / 1M，北京 $0.072 / 1M |
| 免费额度 | 1M token（激活后 90 天内） |
| 语言 | 100+，含中/英/俄 |

RPM/TPM 档位与账号相关，官方限流页为 JS 渲染，未能读到具体数值；
因此适配层采用**受控并发 + 429 指数退避 + 可配 RPM**，而不是硬编码假设。

## 三、向量兼容性（关键）

- 缓存签名 = `供应商/模型/维度`，并已把 schema 升到 `emb-v2`；
  **旧 BGE 向量即使维度相同也不会被云端复用**。
- 索引 meta 记录 `embedding_provider`；加载索引时按 meta 选择编码器
  （`get_embedding_for_index`）：云端索引用云端编码器，旧本地索引用本地编码器。
- **绝不用云端查询向量搜索本地索引（反之亦然）**；无法匹配时降级 BM25，不混用。

## 四、迁移步骤（保留旧库与回退）

1. 配好 DASHSCOPE_API_KEY，把 EMBEDDING_PROVIDER 保持 cloud；
2. 用**小样本学科**先建一次，核对引用与页码（见第五节）；
3. 大库用现有 staging + 原子发布机制重建：构建期间线上旧索引继续服务，
   旧查询仍走本地编码器（`get_embedding_for_index` 按 meta 选择）；
4. 校验通过后成对切换；上一版留在 `<name>.prev`，可回退；
5. 迁移**不删除** 92 份资料，**不清除**解析缓存、分词缓存与旧向量缓存，
   **不重跑**视觉解析（解析结果复用，只重算向量）。

## 五、待实测（本轮未做，不承诺收益）

由于当前 `.env` **没有可用的百炼密钥**，以下**尚未实测**：
- 云端相对本地 CPU 的真实墙钟与检索质量（中文/英文/俄文核对）；
- 大库迁移的实际 token 与费用；
- 真实限流档位下的吞吐。

估算（供参考，非实测）：按大库 13,109 块、约 6.5M 字符中文估算输入约 4–5M token，
按 $0.07/1M 约 **$0.3**；新账号 1M 免费额度可覆盖小样本与部分迁移。
实际数字以接入密钥后的实测为准。