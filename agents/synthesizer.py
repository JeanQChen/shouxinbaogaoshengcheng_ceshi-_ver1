"""Agent 5: 综合报告主笔。

角色转变：从「写一小节综合意见」→「报告主笔」。
接收三份 agent 素材（完整，不截断）+ 模板全文 → 写出完整授信报告。

Contract:
  run(company_id, sections, section_spec, template_text="", template_vars=None) -> ReportSection
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.client import chat, load_prompt
from reporting.template import Citation, ReportSection, SectionSpec

logger = logging.getLogger(__name__)


def run(
    company_id: str,
    sections: list[ReportSection],
    section_spec: SectionSpec,
    template_text: str = "",
    template_vars: dict | None = None,
) -> ReportSection:
    """生成完整授信报告。

    1. 从前文章节提取完整素材（不截断）
    2. LLM 基于模板 + 素材 → 写出完整报告

    Args:
        company_id: 公司代码
        sections: 所有前文 agent 产出的 ReportSection（完整素材）
        section_spec: 本 agent 的 SectionSpec
        template_text: 模板全文（定义输出结构）
        template_vars: 模板变量 {company_name, stock_code, report_period, generated_at}
    """
    # ── 1. 提取各 agent 完整素材 ──
    company_subject_material = "（未生成公司主体素材。）"
    financial_material = "（未生成财务分析素材。）"
    industry_material = "（未生成行业分析素材。）"

    for s in sections:
        if "company_subject" in s.generated_by:
            company_subject_material = s.content
        elif "financial" in s.generated_by:
            financial_material = s.content
        elif "industry" in s.generated_by:
            industry_material = s.content

    # 统计素材长度
    for name, material in [
        ("company_subject", company_subject_material),
        ("financial", financial_material),
        ("industry", industry_material),
    ]:
        logger.info("Synthesizer input [%s]: %d chars", name, len(material))

    # ── 2. 模板变量 ──
    vars_dict = template_vars or {}
    company_name = vars_dict.get("company_name", company_id)
    stock_code = vars_dict.get("stock_code", company_id)
    report_period = vars_dict.get("report_period", "N/A")
    generated_at = vars_dict.get("generated_at", "N/A")

    # ── 3. LLM 写出完整报告 ──
    prompt = load_prompt("synthesizer")
    prompt = prompt.format(
        template=template_text or _default_template(),
        company_subject_material=company_subject_material,
        financial_material=financial_material,
        industry_material=industry_material,
        company_name=company_name,
        stock_code=stock_code,
        report_period=report_period,
        generated_at=generated_at,
        guidance=section_spec.guidance or (
            "以对公客户经理视角，基于以上三份素材，按照模板结构撰写完整授信报告。"
            "客观描述公司情况，不提及检索过程。凡素材标注[待补充]处，写为（暂无公开数据）。"
        ),
    )

    content = chat(
        messages=[{"role": "user", "content": prompt}],
        system="你是一个专业的对公客户经理。你的任务是将素材整理为一份正式的公司授信报告。客观、专业、不粉饰、不夸大。只输出 Markdown 格式的报告正文。",
        max_tokens=8192,  # 完整报告需要更多 token
    )

    return ReportSection(
        section_id=section_spec.section_id,
        title=section_spec.title,
        content=content.strip(),
        citations=[],
        generated_by="synthesizer_main",
    )


def _default_template() -> str:
    """返回默认报告模板（当无模板文件时使用）。"""
    return """# 授信分析报告

> 报告对象：{{ company_name }}（{{ stock_code }}）
> 报告期间：{{ report_period }}
> 生成日期：{{ generated_at }}

---

## 一、公司基本情况和经营情况
<!-- agent: company_subject -->

### 1.1 企业基本情况

### 1.2 历史沿革

### 1.3 股权结构

### 1.4 主要子公司

### 1.5 经营情况

### 1.6 核心竞争力和未来发展计划

### 1.7 公司治理

### 1.8 董事会和主要管理人员构成

---

## 二、财务分析
<!-- agent: financial -->

---

## 三、行业分析
<!-- agent: industry -->

---

## 四、综合授信建议
<!-- agent: synthesizer -->

### 4.1 综合评估

### 4.2 授信建议

### 4.3 风险提示与缓释措施
"""


# ── CLI ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("Usage: python -m agents.synthesizer", file=sys.stderr)
    print("synthesizer 由 streamlit_app 内部调用，无独立 CLI。", file=sys.stderr)
    print("如需测试，请通过 streamlit_app 生成报告。", file=sys.stderr)
    sys.exit(1)
