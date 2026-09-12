# 授信报告生成器（Credit Report Generator）

面向 A 股上市公司的本地 Streamlit 授信研究 Demo。系统把电子 PDF、Excel、核准财务快照和可信外部信息组织为可回查事实，再生成公司信用、财务和行业章节。项目用于面试展示，不是生产授信审批系统，也不会替用户自动决定授信额度、期限、评级或增信方案。

## 当前状态

V2 的 Evidence、Financial Snapshot、Router、Tool Registry、单题 Research Harness、章节 Worker/Evaluator/Store 和只读报告预览基础已经具备。真实样本同时暴露了一个系统性缺口：单题短答案会在 P3→P4 边界压缩连续正文、表格上下文、跨来源事实和互联网研究成果。

项目当前执行 **P3R/P4R Topic Research 与章节内容完整性重整**：

```text
SectionTask
  → P4 Worker 编排外壳
  → Harness Topic runtime（按 required aspect 研究、扩读和补缺）
  → TopicResearchPack（材料、事实、来源、预算、冲突、缺口）
  → 同一 P4 Worker 的 writer 阶段
  → Claims + NarrativeParagraphs + Tables
  → Section Evaluator
```

因此，历史 Phase 3/4 “测试通过”不等于当前产品内容已经关闭；Phase 5 暂未进入。

## 输入与边界

- 支持：A 股上市公司、可提取文本的电子 PDF、Excel，以及财务 PDF/Excel 混合输入。
- 财务数字：必须先结构化抽取、勾稽、冲突检查并由 Python/SQL 计算；LLM 不计算数字。
- 不支持：扫描 PDF/OCR、图片、PPT、Word 输入。
- 外部搜索：当前正式 Provider 为博查（Bocha）；搜索结果必须经过正文获取、不可变快照和来源政策校验后才能引用。
- Demo 可聚焦宁德时代（300750），但生产规则、Contract 和测试不得写死公司或 case id。

## 文档入口

开发前不要从历史报告或 Prompt 猜架构。按以下顺序阅读：

1. [AGENTS.md](./AGENTS.md) — 项目宪法和硬约束
2. [DOCUMENTATION_INDEX.md](./DOCUMENTATION_INDEX.md) — 文档权威与历史/现行分类
3. [DESIGN_V2.md](./DESIGN_V2.md) — 现行 V2 设计
4. [V2_IMPLEMENTATION_PLAN.md](./V2_IMPLEMENTATION_PLAN.md) — 阶段路线与门禁
5. [PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md](./PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md) — 当前唯一任务书
6. [V2_TODO.md](./V2_TODO.md) — 当前工作区和下一动作

`DESIGN.md`、旧任务书和交付报告均为历史基线。`CLAUDE.md` 只是 Claude Code 导航页，不是第二份项目宪法。

## 在现有 Demo 工作区运行

`.env` 至少需要：

```dotenv
DEEPSEEK_API_KEY=<your-key>
DEEPSEEK_BASE_URL=https://api.deepseek.com
EXTERNAL_SEARCH_PROVIDER=bocha
BOCHA_API_KEY=<your-key>
DEMO_MODE=true
```

密钥不得提交到 Git，也不要在日志、截图或报告中展示。

先执行只读环境检查：

```bash
python -m scripts.demo_preflight
```

启动网页：

```bash
streamlit run streamlit_app.py
```

页面演示优先加载已经持久化的真实报告产物，避免仅为查看结果重新消耗 LLM/网络调用。当前面试版本以网页截图、录屏、可复制 Markdown 和可展开引用为优先；Word 不作为当前关闭门。

运行完整离线评测：

```bash
python -m evals.run_evals
```

完整 eval 全绿只代表相应代码/回归门通过，还必须单独检查 aspect 覆盖、材料与事实保留、外部来源采用率、表文一致和章节可读性。

## 新机器安装说明

仓库保留 V1 的 `make setup` / `make demo-data` 等命令，但它们尚未按当前 V2 全部数据存储与 P3R/P4R 主链重新做“全新机器从零安装”验收。当前不要把这些命令宣传成新机器一键可用。Phase 6 将统一安装、Demo 数据准备、持久化报告加载和演示脚本；在此之前，以现有已准备工作区和 `scripts.demo_preflight` 为准。

## 安全与使用声明

- 输出必须保留引用、数据期间、口径和资料限制。
- “未检索到”不等于“没有发生”。
- 任何生成内容都需要人工复核，仅供演示和研究辅助。
- 公开材料的版权和数据权利归原权利人所有。
