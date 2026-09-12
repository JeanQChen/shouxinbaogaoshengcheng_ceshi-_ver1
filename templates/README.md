# Template 与 Contract 角色索引

> 模板是运行时展示资产，不是上位设计。现行产品与架构以根目录文档权威链为准。

| 文件 | 状态 | 用途 |
|---|---|---|
| `contracts/standard_v2.yaml` | ACTIVE COMPAT CONTRACT v1 / P3R REPLACEMENT REQUIRED | 当前代码兼容的 52 问 Contract；已知 aspect/source/display 语义不足，R1 必须发布兼容 Contract v2，禁止原地覆盖历史语义和指纹 |
| `standard.md` | V1_LEGACY_ONLY | V1 Markdown 报告模板；其中“报告期间”、固定目录和综合授信建议不能充当 V2 Contract/WritingSpec |
| `simple.md` | V1_LEGACY_ONLY | V1 简版模板，不进入 P3R/P4R 正式内容链 |

P3R/P4R 必须新增版本化 `SectionWritingSpec` / `ReportPresentationProfile`：

- Contract 管“研究什么、最低证据、缺口与阻断”；
- WritingSpec 管“多个 Topic 如何组合成人类可读小节、段落、表格、明确期间和必显/可选内容”；
- PresentationProfile 管整份报告的顺序、摘要、引用和附录；
- 三者均不得包含 300750、宁德时代事实、固定页码或 gold。
- R1 必须确定唯一机器可读 WritingSpec/Profile 资产目录、schema、loader、validator 和首版版本；在此之前任何 Prompt 或 Markdown 模板都不得被视为该规格。

Contract v2 还必须显式区分外部 `search_external_sources` 与 `fetch_external_content` capability；正文成功后的 snapshot 是受 Registry/预算/审计约束的 Rules-internal 步骤，不是 LLM 可见 capability。

旧 Markdown 模板只有在显式 V1 兼容入口中才可调用。正式 V2 调用链测试必须证明它们调用次数为 0。
