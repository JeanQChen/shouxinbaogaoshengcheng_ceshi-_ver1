"""Eval: A5 Streamlit 薄 UI 间谍测试（§8.7 / §12.5）。

用法: python -m evals.test_financial_v2_resolutions_ui

不启动 Streamlit 运行时，注入 fake streamlit 模块 + monkeypatch 服务接口，验证：
- 无 pending 时 _render_financial_v2_confirmation 只调用 list_pending，不渲染确认区、
  不调用任何 submit 接口；
- UI 层只引用 resolutions 服务接口（list_pending / submit_mapping / submit_value）
  与 schema 常量，不 import / 调用 mapping、reconciliation、checks、normalization 等
  业务逻辑（科目映射、容差、冲突选择默认值、事务、失效、权限规则一律不在 UI 实现）。

用源码检查辅助证明 UI 薄层，不依赖真实数据库 / Streamlit 运行时。
"""

from __future__ import annotations

import inspect
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _empty_pending(company_id, filters=None):
    from financial_v2.resolutions import PendingIssueList
    return PendingIssueList(company_id=company_id, items=[],
                            mapping_confirmation_count=0,
                            value_source_resolution_count=0,
                            insufficient_scope_count=0)


def _make_fake_streamlit():
    m = types.ModuleType("streamlit")
    calls: list[str] = []

    def rec(name, default=None):
        def fn(*a, **k):
            calls.append(name)
            return default
        return fn

    for name in ("set_page_config", "title", "caption", "header", "subheader", "divider",
                 "write", "markdown", "info", "warning", "success", "error", "status",
                 "expander", "columns", "download_button", "file_uploader", "text", "code",
                 "dataframe", "table", "empty", "spinner", "rerun", "container"):
        setattr(m, name, rec(name))
    m.checkbox = rec("checkbox", False)
    m.radio = rec("radio", "__none__")
    m.selectbox = rec("selectbox", "AUDITED_SOURCE")
    m.text_input = rec("text_input", "")
    m.button = rec("button", False)
    m.session_state = {}
    return m, calls


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    original_streamlit = sys.modules.get("streamlit")
    fake_st, _st_calls = _make_fake_streamlit()
    sys.modules["streamlit"] = fake_st

    try:
        import financial_v2.resolutions as resmod

        saved = {
            "list_pending": resmod.list_pending,
            "submit_mapping_resolutions": resmod.submit_mapping_resolutions,
            "submit_value_resolutions": resmod.submit_value_resolutions,
        }
        call_log: list[str] = []

        # 服务接口间谍：list_pending 返回空，submit_* 记录调用。
        def spy_list_pending(company_id, filters=None):
            call_log.append("list_pending")
            return _empty_pending(company_id, filters)

        def spy_submit_mapping(req):
            call_log.append("submit_mapping_resolutions")
            return None

        def spy_submit_value(req):
            call_log.append("submit_value_resolutions")
            return None

        resmod.list_pending = spy_list_pending
        resmod.submit_mapping_resolutions = spy_submit_mapping
        resmod.submit_value_resolutions = spy_submit_value

        import streamlit_app as app

        # 1) 无 pending → 只调 list_pending，不渲染确认区、不调用 submit 接口。
        call_log.clear()
        app._render_financial_v2_confirmation("ACME")
        check("list_pending" in call_log, "无 pending 时仍调用 list_pending 查询")
        check("submit_mapping_resolutions" not in call_log
              and "submit_value_resolutions" not in call_log,
              "无 pending 时不调用任何 submit 接口（不渲染确认区）")

        # 2) 源码薄层检查：UI 函数只引用服务接口，不实现业务逻辑。
        src = inspect.getsource(app._render_financial_v2_confirmation)
        for banned in ("map_candidate", "group_records", "run_reconciliation", "run_checks",
                       "_common_intersection", "tolerance", "derive_record_id", "commit_"):
            check(banned not in src, f"UI 不实现业务逻辑：{banned}")
        check("list_pending" in src and "submit_mapping_resolutions" in src
              and "submit_value_resolutions" in src,
              "UI 引用三个服务接口")
        check("from financial_v2 import reconciliation" not in src
              and "from financial_v2 import mapping" not in src,
              "UI 不 import 对账/映射业务模块")
        check("import normalization" not in src and "import checks" not in src,
              "UI 不 import 标准化/勾稽业务模块")

    finally:
        # 还原 monkeypatch + fake streamlit，避免污染后续 eval 模块。
        if "financial_v2.resolutions" in sys.modules:
            resmod2 = sys.modules["financial_v2.resolutions"]
            for k, v in saved.items():
                setattr(resmod2, k, v)
        if original_streamlit is None:
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = original_streamlit

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
