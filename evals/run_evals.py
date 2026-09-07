"""Eval 跑分入口。

用法: python -m evals.run_evals [--real-llm]
"""

import importlib
import os
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EVAL_MODULES = [
    "evals.test_schema",
    "evals.test_template",
    "evals.test_metrics_pure",
    "evals.test_db",
    "evals.test_excel_parser",
    "evals.test_metrics_db",
    "evals.test_pipeline",
    "evals.test_schema_mapper_llm",
    "evals.test_analyzer",
    "evals.test_assembler",
    "evals.test_pdf_parser",
    "evals.test_retrieval",
    "evals.test_company_subject",
    "evals.test_industry",
    "evals.test_synthesizer",
    "evals.test_verifier",
    "evals.test_word_exporter",
    "evals.test_baseline_runner",
    "evals.test_contracts",
    "evals.test_evidence",
    "evals.test_financial_v2_schema",
    "evals.test_financial_v2_snapshot_schema",
    "evals.test_financial_v2_store",
    "evals.test_financial_v2_source_registry",
    "evals.test_financial_v2_migration",
    "evals.test_financial_v2_decimal",
    "evals.test_financial_v2_metadata_confirmation",
    "evals.test_financial_v2_extractors",
    "evals.test_financial_v2_pdf_extractor",
    "evals.test_financial_v2_mapping",
    "evals.test_financial_v2_normalization",
    "evals.test_financial_v2_checks",
    "evals.test_financial_v2_reconciliation",
    "evals.test_financial_v2_resolutions",
    "evals.test_financial_v2_resolutions_ui",
    "evals.test_financial_v2_snapshot_store",
    "evals.test_financial_v2_snapshots",
    "evals.test_financial_v2_formulas",
    "evals.test_financial_v2_metrics",
    "evals.test_financial_v2_invalidation",
    "evals.test_financial_v2_adapters",
    "evals.test_financial_v2_progress",
]


def main() -> None:
    real_llm = "--real-llm" in sys.argv

    if not real_llm:
        os.environ["EVAL_MOCK_LLM"] = "true"

    print(f"\n{'=' * 64}")
    print(f"  Eval Suite  {'(MOCK LLM)' if not real_llm else '(REAL LLM)'}")
    print(f"{'=' * 64}\n")

    all_results: dict[str, dict] = {}
    t0 = time.time()

    for module_name in EVAL_MODULES:
        short_name = module_name.replace("evals.", "")
        try:
            mod = importlib.import_module(module_name)
            result = mod.main()
            all_results[module_name] = result
        except Exception as e:
            import traceback
            all_results[module_name] = {
                "passed": 0,
                "failed": 1,
                "skipped": 0,
                "details": [f"CRASH: {type(e).__name__}: {e}\n{traceback.format_exc()}"],
            }

        r = all_results[module_name]
        if r["failed"] == 0 and r["passed"] > 0:
            status = " PASS"
        elif r["skipped"] > 0 and r["failed"] == 0:
            status = " SKIP"
        else:
            status = " FAIL"
        print(f"  [{status}] {short_name}  "
              f"(+{r['passed']} / -{r['failed']} / ~{r['skipped']})")

    elapsed = time.time() - t0

    total_passed = sum(r["passed"] for r in all_results.values())
    total_failed = sum(r["failed"] for r in all_results.values())
    total_skipped = sum(r["skipped"] for r in all_results.values())
    total_tests = total_passed + total_failed + total_skipped

    print(f"\n{'=' * 64}")
    print(f"  TOTAL: {total_passed} passed, {total_failed} failed, "
          f"{total_skipped} skipped  ({elapsed:.1f}s)")
    print(f"{'=' * 64}\n")

    # Print failures
    if total_failed > 0:
        print("FAILURES:\n")
        for name, r in all_results.items():
            for d in r.get("details", []):
                if d.startswith("FAIL") or d.startswith("CRASH"):
                    print(f"  [{name}] {d}")
        print()

    sys.exit(0 if total_failed == 0 else 1)


if __name__ == "__main__":
    main()
