# Evaluation 资产角色与冻结边界

> Evaluation 文件验证特定能力，不是运行时 Contract，也不能直接定义报告目录或研究充分性。

## 历史冻结资产

- `datasets/v1_baseline.jsonl`、`baseline_contract_mapping.jsonl`、`v1_baseline.split_manifest.json` 及既有 frozen/unseen 结果只用于 V1 检索、Router、单题实际路径和安全回归。
- 历史 `coverage_role=full` 表示当时原子 case/mapping 的口径，**不表示**完整 Topic 的 required aspects 或整章内容已覆盖。
- Gold document/page 只进入运行后离线诊断，不得进入 query、Router、Topic scheduler、补检、充分性、停止、写作或运行时评分。
- 冻结 dataset、split、gold、run 和历史指标不得为了 P3R/P4R 提分而修改、重标或覆盖。

## Phase 4 基础状态机资产

- `datasets/section_cases_v1.json` 主要验证 Section 状态流转、Store、Evaluator 和 fail-closed 编排。
- 合成引用或语义不真实的 fixture 只能证明状态机行为，不能作为主营业务、行业或财务章节内容正确性的证据。

## P3R/P4R 新评测

新版本必须与历史冻结集分开，至少覆盖：

1. 本地长叙述与受控上下文扩读；
2. 本地表格/财务附注；
3. 结构化财务与跨期变化；
4. 外部时效研究和来源政策；
5. 事件/负面核验；
6. 本地 + 结构化 + 外部混合 Topic；
7. 非 300750 公司和未见 Topic 组合。

结果必须分别报告：安全正确性、required-aspect 终态与覆盖、材料/事实保留、外部 funnel adopted yield、完整 Pack 集身份、Claim→Paragraph/Table 映射和人工可读性。完整 eval 0 failed 不能单独宣布内容通过。

`evaluation/results/**` 是生成的审计证据，不是项目指令，默认不得提交或批量改写。
