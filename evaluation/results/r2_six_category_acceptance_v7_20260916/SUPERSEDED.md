# SUPERSEDED（中间轮，非验收输入）

本目录是 R2 本轮（A–D + 通用材料身份收口）**定稿前**的中间产物，保留作为「旧实现失败」的
现场证据（§八.1 先增加反例，证明旧实现失败），**不得**作为本轮验收输入。

定稿前的实现缺陷（已在本轮修复）：

1. `harness/topic_boundary.py::build_boundary_verification_record` 会把「文档自身编号结构
   未给出主题小节层级」（`topic_level=None` / `topic_level_source="unknown"`）的记录标成
   `status="verified"` —— 即 §四.A.7 禁止的「未验证边界伪装成 verified」。
2. `harness/six_category_acceptance.py` 的主题外决策筛选键写成了人类发明的理由码
   `topic_boundary_out_of_topic`，而真实生产理由码是
   `topic_section_closed_sibling_heading` / `section_boundary_sibling` /
   `backward_previous_section_heading` 等 —— 主题边界门因此对真实 run 恒失败。
3. 验收器把「本 aspect 自身的另一个已声明 seed 块」误判为主题外内容混入材料
   （多 seed 结构性冲突未建模）。
4. 无 enumeration 条目的 aspect 被当成「负面枚举」（`material_type_supported=false`），
   使非 set_complete 类别落成 `unsupported`。

定稿轮见同级 `r2_material_slice_r2_sixcat_v8_*_20260916` 与
`r2_six_category_acceptance_v8_20260916`。
