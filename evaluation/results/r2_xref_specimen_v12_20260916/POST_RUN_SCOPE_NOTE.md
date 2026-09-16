# 事后范围澄清说明（**不改动**已冻结清单）

本文件写于 v12 真实重跑**之后**，只做术语澄清，**不修改** `specimen_manifest.json` 的任何字段
（该文件仍是冻结版本，sha256 见下）。冻结清单只在前置目录不存在时生成一次，其内容不得因重跑
结果而回填或改写。

## 1. `not_tested_record` 的作用域

冻结清单中 `not_tested_record` 的 `material_state: not_obtained` / `capability_verdict:
NOT_TESTED` 指的是**显式引用能力在 `financial_notes` run 上的状态**（该 aspect 的 boundary
policy 不可用 ⇒ 整轮扩读 fail-closed ⇒ 该 run 对本能力无信息量）。

它与 v12 六类 manifest 里 `financial_notes` **类别行**的 `boundary_incomplete / PASS` **不是同一
个判据**：后者是该类别材料状态的诚实陈述（多 `document_version` 共存，不合并为单一完整集合），
三轴模型下二者互不自动映射。两者并存不是矛盾，各自有独立派生来源。

## 2. `expected_target` 的范围语义

冻结清单 `pre_declared_expectation.expected_target.start/end`（300/333）只标识**表题行**，
由冻结时的独立行级正则推出；生产绑定的 `target_start/target_end`（300/423）另含单位/表头/表体
行，边界必然 ≥ 表题行，允许不等。字段内 `extent_semantics` 已就此自述。

## 3. 冻结文件指纹（核对用）

```text
evaluation/results/r2_xref_specimen_v12_20260916/specimen_manifest.json
specimen_fingerprint = baa3a92868b373cc5ce170b6c078603bf953b42f91beb31f88278da57d855e1c
```

同目录另存 `specimen_manifest.freeze1_superseded.json`：第一次冻结（生成后、任何 v12 run 存在
之前）即被第二次冻结取代，原因是补上 `extent_semantics` 自述；两次冻结之间**没有任何 run 目录
存在**（两份清单的 `frozen_before_run.all_run_dirs_absent` 均为 true）。该文件仅作留痕，不参与
任何核对。
