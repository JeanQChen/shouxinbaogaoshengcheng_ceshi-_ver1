# R2 显式引用验收样本冻结清单（v12，evaluation-only）
冻结时刻：20260916T200000Z；清单指纹：`baa3a92868b373cc5ce170b6c078603bf953b42f91beb31f88278da57d855e1c`
本清单在真实重跑**之前**生成，冻结：输入身份与指纹、正样本（`company_subsidiaries.major_subsidiaries`）、预期断言（同块标记后首个表对象）、六类 run 绑定。
重跑后**不得**改绑 run 或重新挑样本；`financial_notes` 记 `NOT_TESTED / boundary_policy_unavailable`，其修法进入 R3/Contract successor changelist（R2 不改冻结 Contract）。
## 冻结时 run 目录是否存在

```json
[]
```
## 正样本预期目标（独立于生产实现推出）

- 锚点块：p41 blk0，文本 sha256 `abdae581b4e9b8c1ae5bd6db615ad670ce7431a2e5ce7d384b451f73b4e0e538`
- 标记出现：`[{"marker": "如下表", "start": 294, "end": 297}]`
- 预期目标行：`表5-5截至2025年12月末发行人主要参股及联营、合营企业情况`
- 必须拒绝：P43 `049de1a26d73f3e89611679694ff4f06`「表5-6 发行人组织结构图」
