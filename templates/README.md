# Template 与 Contract 角色索引

> 模板是运行时展示资产，不是上位设计。现行产品与架构以根目录文档权威链为准。

| 文件 | 状态 | 用途 |
|---|---|---|
| `contracts/standard_v2.yaml` | FROZEN COMPAT CONTRACT v1 | 历史兼容 52 问 Contract，固定 hash，禁止原地覆盖历史语义和指纹 |
| `contracts/standard_v3.yaml` | FROZEN CONTRACT v2 | R1-A 已冻结的 52 问 / 187 aspects 现行业务契约；树结构调整不得改变业务语义 |
| `policies/source_policy_v1.yaml` | FROZEN SOURCE POLICY | 来源等级、充分性与外部事实门槛 |
| `writing_specs/credit_report_v1.yaml` | FROZEN WRITING SPEC | aspect 到授信报告小节、表格和正文角色的映射 |
| `presentation_profiles/interview_demo_v1.yaml` | FROZEN PRESENTATION PROFILE | 当前面试版展示边界 |
| `standard.md` | V1_LEGACY_ONLY | V1 Markdown 报告模板；其中“报告期间”、固定目录和综合授信建议不能充当 V2 Contract/WritingSpec |
| `simple.md` | V1_LEGACY_ONLY | V1 简版模板，不进入 P3R/P4R 正式内容链 |

P3R/P4R 已冻结版本化 `SectionWritingSpec` / `ReportPresentationProfile`，后续实现必须消费而不得另造影子规格：

- Contract 管“研究什么、最低证据、缺口与阻断”；
- WritingSpec 管“多个 Topic 如何组合成人类可读小节、段落、表格、明确期间和必显/可选内容”；
- PresentationProfile 管整份报告的顺序、摘要、引用和附录；
- 三者均不得包含 300750、宁德时代事实、固定页码或 gold。
- 已冻结的机器可读资产、schema、loader、validator 和版本是唯一现行载体；任何 Prompt 或 Markdown 模板都不得被视为该规格。

Contract v2 已显式区分外部 `search_external_sources` 与 `fetch_external_content` capability；正文成功后的 snapshot 是受 Registry/预算/审计约束的 Rules-internal 步骤，不是 LLM 可见 capability。

当前 R3 前强制门是 `TREE_STRUCTURE_ADJUSTMENT_TASK.md`：Contract 通过标题路径、导航简介和表题只定位候选 `OutlineNode`/`TableObject`；这些导航信息不能直接证明 aspect covered，也不能替代 span/table 的底层 Evidence 引用。

旧 Markdown 模板只有在显式 V1 兼容入口中才可调用。正式 V2 调用链测试必须证明它们调用次数为 0。
