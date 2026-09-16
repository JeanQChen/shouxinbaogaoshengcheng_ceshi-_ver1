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

## 树结构调整评测（R3 前强制门）

该评测验证正式材料边界，不用“旧 Evidence 命中”或“单元测试全绿”替代真实文档检查。至少分别报告：

1. `PageLayout` 是否覆盖全部电子 PDF 页面、正文行和可获得的源坐标；目录关键词误命中不得导致正文丢失。
2. 目录/书签候选与正文一级至小标题的对齐率、层级准确率、重复标题路径身份和低置信 reason codes。
3. 正文归属率：所有内容进入可信节点/祖先或显式 `unassigned`，静默丢失为 0。
4. 跨标题 Evidence 的 span 切分准确率与无损重构；不得把整块混合 Evidence 继续作为正式材料输入。
5. `TableObject` 的表题、单位、物理表头、表体、合计、续表和 component provenance 完整率；表格与说明文字分离但关系可回查。
6. 树感知检索实际返回 node/span/table；报告跨标题污染率、重复率和 legacy Evidence fallback 次数/原因。
7. Contract→标题/简介相似度只产生候选；必须用反例证明它不能自动改变 aspect coverage、set_complete 或 sufficiency。
8. Pack/P4 引用落到底层 span/table component 或结构化权威；导航简介直接被引用的数量必须为 0。
9. 主营业务、核心竞争力、主要子公司、财务附注、显式引用及非 300750 样本的 before/after。
10. 历史 Evidence、索引、Pack 与结果仍可读；outline/span/table/index 版本变化正确触发 stale。
11. 每个可导航节点具有抽取式、可回溯简介或明确 unavailable 原因；版本化 AspectNavigationProfile 的候选召回、误召回和公司无关性可审计。
12. PageLayout 原文与 Evidence 规范化文本的 alignment 可复核；歧义映射 fail-closed，字符偏移不得猜测。
13. fallback/unassigned 仍输出精确 OutlineSpan，不能把 whole Evidence 升级为材料，也不能在缺少已验证 outline/table 边界时单独证明 set_complete。

真实树结构产物使用新 `evaluation/results/tree_structure_<run_id>/`，不得覆盖历史 R2 目录。至少三份真实电子 PDF 必须逐份产出和审查，另有一个非 300750 fixture；不得用同一文档重复冒充多份真实样本。`boundary_incomplete` 或 `unassigned` 可以是诚实结果，但必须说明范围、原因和对后续报告的影响。

`evaluation/results/**` 是生成的审计证据，不是项目指令，默认不得提交或批量改写。
