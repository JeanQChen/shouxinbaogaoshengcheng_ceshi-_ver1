# AGENTS.md

> **Project constitution. Read this file completely before changing code or project documents.**
> When instructions conflict, follow the authority order below. Do not use a historical report or runtime Prompt as an implementation instruction.

## 1. Project snapshot

| | |
|---|---|
| Product | A-share listed-company credit analysis report generator |
| Purpose | Local Streamlit interview demo; not a production credit decision system |
| Primary user | Corporate banking relationship manager / interviewer |
| Demo subject | CATL (300750), without company-specific production rules |
| Priority | 演示稳定 > 亮点突出 > 功能全面 > 工程严谨；任何亮点不得以错误事实、错误数字或伪造引用为代价 |
| Current stage | P3R/P4R tree-structure adjustment before Topic runtime integration |

The current design is [DESIGN_V2.md](./DESIGN_V2.md). The original [DESIGN.md](./DESIGN.md) is a historical V1 baseline only.

## 2. Documentation authority

Read [DOCUMENTATION_INDEX.md](./DOCUMENTATION_INDEX.md) for the full map. The binding order is:

1. `AGENTS.md` — engineering and safety constitution.
2. `DESIGN_V2.md` — current product, business, and architecture design.
3. Confirmed business baselines — frozen Contract v2 (`templates/contracts/standard_v3.yaml`), Source Policy, WritingSpec, PresentationProfile, `FORMULA_REVIEW.md`, plus immutable v1 compatibility assets (`templates/contracts/standard_v2.yaml`, `contracts/sc_decisions.yaml`) within their declared scope.
4. `V2_IMPLEMENTATION_PLAN.md` — stage order and exit gates.
5. The current umbrella task — `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md`; its only active implementation subtask is `TREE_STRUCTURE_ADJUSTMENT_TASK.md` while that gate is open.
6. `V2_TODO.md` — progress record only.
7. `CLAUDE.md` — thin Claude Code bootstrap only.

Historical task books, acceptance reports, generated reports, debug files, reference documents, and `llm/prompts/*.txt` never override this chain.

## 3. Active architecture gate

Phase 2/3 historical runs, gold, split manifests, and frozen results remain immutable safety and regression baselines. They do **not** prove that a complete topic can be written from the current P3 output.

The target single production chain is:

```text
SectionContract / SectionTask
  → Worker orchestration shell
  → Harness-owned TopicResearchState
  → (InformationNeed → Router → existing atomic executor → ToolRegistry)*
  → immutable EvidenceBlock provenance
  → versioned PageLayout / read-only DocumentOutline
  → OutlineSpan / TableObject inspection + bounded fallback expansion
  → fact/source validation
  → TopicResearchPack
  → Worker writer
  → SectionClaim + NarrativeParagraph + Table + Unresolved
  → Section Evaluator
```

Rules:

- `ResearchOutcome` is one atomic research record and a compatibility-evaluation object. It is not P4's sole content input.
- `EvidenceBlock` is an immutable provenance/citation anchor, not a reliable business boundary, paragraph, table, or completion unit. Historical Evidence IDs and Evidence Sets are never rewritten to simulate semantic structure.
- Every supported electronic PDF has a versioned `PageLayout` and read-only `DocumentOutline` derived from the raw PDF or the same canonical layout source. Bookmarks and table-of-contents entries are candidates; body headings, including small subheadings, provide the confirming anchors.
- Local RAG, `TopicResearchPack`, and P4 consume `OutlineSpan` and `TableObject` material units. They may cite the parent Evidence plus exact span/page locators, but must not promote a cross-heading whole Evidence block as one semantic material.
- Contract-to-outline title/synopsis similarity is candidate navigation only. It never proves an aspect covered; coverage still requires qualified material, facts, citations, authority, and the Contract completion rule.
- Adjacent-block/page expansion, rolling frontiers, and explicit-reference traversal remain bounded fallbacks for unavailable/low-confidence outlines or cross-node references, not the normal source-boundary algorithm.
- Every fallback result still becomes a precisely located `OutlineSpan`; the whole `EvidenceBlock` never becomes a formal material. Fallback, low-confidence, or `unassigned` spans may support candidate facts, but cannot by themselves establish `set_complete` without a verified outline/table boundary.
- Navigable nodes use deterministic, extractive, source-linked synopses. Aspect-to-node queries come from a versioned, company-independent navigation profile derived from the frozen Contract; synopsis/profile versions enter dependency fingerprints and neither can serve as evidence.
- Harness owns the only authoritative `TopicResearchPack`, topic state, aspect scheduler, cumulative budget, and checkpoint.
- `sections.topic_research` and similar experimental code may contribute pure algorithms but must not become a second Router/Harness/tool/LLM runtime.
- Company and industry writers consume the complete current Pack set whose topic IDs exactly equal `SectionTask.topic_ids`. Missing, duplicate, stale, wrong-task, wrong-company, wrong-`report_as_of`, or wrong-Contract Packs produce an explicit gap/block; writers must not select only convenient Packs and present the Section as complete. The financial writer consumes authoritative `FinancialFactPack` plus validated Evidence-backed note facts.
- P4 keeps atomic auditable Claims and separately produces human-readable paragraphs/tables supported by multiple Claims.
- Phase 4 Section Evaluator is a bounded chapter-quality loop, not the final report approver. Phase 5 Assurance Controller controls only system-Assurance/release eligibility: deterministic gates first, evidence-grounded semantic review second, and a deterministic version-bound status aggregation. An LLM may emit structured issues but may not override hard failures, rewrite the report, or approve its own output by self-assertion. Final human acceptance remains a separate state and is never inferred by the Controller.
- Phase 5 is blocked until the P3R/P4R content gate passes.

## 4. Hard constraints

- No scanned PDF or OCR in the first release. Electronic PDFs must have an extractable text layer; low quality fails fast.
- No PPT, image, Word, or arbitrary office-document input in the first release.
- Financial input may be electronic PDF, Excel, or both. Financial PDF numbers must be structurally extracted, reconciled, and conflict-checked before use; ordinary RAG cannot authorize financial numbers.
- The LLM never calculates amounts, ratios, growth rates, comparisons, or table aggregates. Python/SQL with Decimal semantics calculates them.
- Do not invent a single merged authority for all numbers. FinancialSnapshot, Evidence-backed note facts, and ExternalSnapshot retain separate authority/version checks; a unified Fact Registry is a read model and semantic identity layer.
- `TableObject` is a structured Evidence-backed material and navigation object. It does not become a `FinancialSnapshot`; main statements, Evidence-backed note facts, ordinary business tables, and external facts retain separate authority checks.
- No user authentication, encryption, multi-user isolation, production concurrency, or disaster recovery unless a later approved task explicitly adds them.
- No module-specific branch for `300750`, CATL, a case id, gold page, answer keyword, or fixed document page.
- No gold document/page in runtime query planning, retrieval decisions, sufficiency, stopping, answer generation, or grading. Gold is offline diagnosis only.
- Missing evidence means “not obtained within the searched scope,” never “does not exist,” unless an authoritative source explicitly supports the negative fact.
- Search snippets and URLs are navigation only. External facts require fetched non-empty content, immutable snapshot, authority/date/source-policy checks, and citations.
- Current external search runtime is Bocha. Tavily is not a runtime dependency or fallback and must not be silently enabled.
- Do not weaken fail-closed correctness to improve FULL or completion rates. Safety passing also does not prove content completeness.

## 5. Research and writing semantics

- Formal Contract `required_aspects` are the scheduling and completion units. One broad retrieval can cover multiple aspects; only gaps trigger focused follow-up.
- An atomic `ANSWER` ends the current need, not the whole Topic.
- After a relevant hit, first resolve the smallest sufficient outline node/subtree and load its `OutlineSpan`/`TableObject` members. Follow typed table-continuation and explicit-reference relations when needed. Only when structure is unavailable or low-confidence may the system use bounded adjacent-block/page expansion; stop at unrelated nodes, unresolved structure, no-new-information boundaries, or hard budget.
- Outline navigation metadata and deterministic synopses may improve candidate ranking, but are not Evidence and cannot be cited as facts.
- PageLayout-to-Evidence character alignment is versioned and auditable. Ambiguous alignment fails closed or produces a new append-only Evidence Set; offsets must never be guessed.
- Preserve every validated relevant material/fact in the Pack even if a short answer omitted it.
- Topic budgets are adaptive but finite and versioned. Never solve completeness by globally increasing top-k or tool calls, and never tune budgets by company/case.
- External research uses an auditable funnel: intent → candidates → rank → fetch → automatic snapshot → extract → validate → adopt/reject.
- P4 writers organize supported facts into a readable business narrative. They may order, merge, and add non-factual transitions; they may not add facts, numbers, entities, periods, or unsupported conclusions.
- Flow/activity/event language uses explicit periods such as “2025年度” or “2025年内”; point-in-time balances use explicit dates. Do not use an undefined “报告期内”.
- Optional or diagnostic financial metrics that cannot be computed may stay out of the main body according to a versioned display policy; required gaps remain explicit.
- Report length follows Contract coverage and information density. There is no 8,000-character hard cap; 20,000–30,000 Chinese characters is only a human reference for a fuller report, not a pass condition.

## 6. Implementation discipline

### Plan before code

Before a new module or cross-module refactor, state:

- public input/output types;
- main internal functions;
- dependencies and authority boundaries;
- CLI/self-check command;
- tests, migration effects, commit split, and stop condition.

Do not code a major batch until its plan is reviewed when the active task requires a review gate.

### Protect the workspace

- Inspect `git status` first. Existing tracked or untracked changes belong to the user or another agent.
- Do not overwrite, delete, reset, stash, or silently include unrelated changes.
- Never commit `.env`, API keys, databases, logs, generated results, debug files, personal settings, or reference documents.
- SQLite schema changes are append-only migrations. Historical migrations and accepted run artifacts are immutable.
- Read-only inspection paths must not initialize, create, migrate, or mutate databases.

### One responsibility per change

- Each commit has one reviewable responsibility; tests may accompany the responsible module.
- Do not mix governance documents, runtime logic, generated results, and unrelated cleanup.
- Reuse existing public interfaces. If an interface must change, version it and provide compatibility/migration tests.

### Runtime observability

- Core modules need a CLI or deterministic self-check unless the approved task explicitly classifies them as pure private helpers.
- All retrieval goes through the approved retrieval/ToolRegistry path and writes retrieval trace.
- All LLM calls go through the shared client and write call id, prompt/version, model, latency, usage when available, completion status, and errors.
- Prompts live in `llm/prompts/*.txt`; do not inline them in Python.
- Do not expose hidden chain-of-thought. Persist actions, evidence, decisions, and concise reasons only.

### Testing

- Add regression tests for every fixed defect.
- Run focused tests first, then the full `python -m evals.run_evals` gate before a code commit.
- Mock tests prove deterministic behavior; at least one bounded formal-chain integration test proves orchestration; small real vertical slices prove content usefulness.
- Tree-structure tests separately measure body-heading recall/precision, hierarchy accuracy, cross-heading span purity, unmapped text, table integrity/continuation, outline-aware retrieval, Pack retention, and P4 material consumption. Page-level retrieval recall alone cannot close this gate.
- A green test count is not a product-quality result. Report separately: safety, aspect coverage, material/fact retention, external-funnel yield, narrative readability, and unresolved gaps.
- Never rerun frozen evaluation splits to tune them. New refactor evaluation uses new versioned fixtures/runs.

## 7. UI and demo boundary

- `streamlit_app.py` remains thin: input, service invocation, progress, artifact loading, and display only.
- Prefer loading a persisted real artifact for screenshots and recording; do not spend LLM/network calls merely to view an existing run.
- The current interview-demo release is read-only after generation: it shows actual status, missing information, searched scope, impact, failure reasons, and suggested future material types, but does not implement user supplementary upload, gap-to-material binding, Evidence mutation, or user-triggered resume/continue. Keep structured extension seams without exposing inactive actions.
- Status is a product feature, not a decorative percentage. Distinguish process completion, preview availability, system-Assurance outcome, and final human acceptance; progress must come from persisted units/artifacts rather than model estimates.
- Demo may focus on CATL, but code, Contract, policy, and tests must remain company-independent.
- Word export is not a prerequisite for the current interview demo; Markdown/Streamlit presentation comes first. A future change requires an explicit business decision first reflected in `AGENTS.md`, `DESIGN_V2.md`, and the roadmap; a lower-level Phase task cannot change this order by itself.

## 8. Current execution boundary

The active umbrella task is `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md`; the only active implementation subtask is `TREE_STRUCTURE_ADJUSTMENT_TASK.md` until its exit gate passes.

At this point:

- R0 is closed; R1-A is frozen; R1-B is closed. Existing R2 code/results remain preserved as material-store, authority, trace, table-identity, and fallback-expansion work, but the old whole-Evidence/adjacent-block boundary model is superseded as the primary path.
- The user approved the tree-structure adjustment on 2026-09-16. Complete its documentation, implementation plan, versioned schema/store/index integration, and real vertical-slice gate before R3. R3–R7 and Phase 5/6 remain blocked. Exact worktree/test status belongs only in `V2_TODO.md`.
- Do not continue page/seed-specific R2 boundary patching, skip directly to report polishing, or rerun the full 41 questions before the tree gate passes.
- Do not start Phase 5 or Phase 6 product work while the P3R/P4R gate is open.

Common checks:

```bash
python -m scripts.demo_preflight
python -m evals.run_evals
streamlit run streamlit_app.py
```

Use module-specific CLI and focused eval commands from the active task before the full gate.

## 9. Documentation maintenance

- Product/architecture decisions change `DESIGN_V2.md` first, then roadmap, active task, and TODO.
- Historical reports retain their original facts and receive only a clear historical/superseded banner when needed.
- `CLAUDE.md` stays a short bootstrap; never copy this constitution into it.
- `README.md` describes what a user can actually run. Unverified fresh-install claims must be labeled.
- After doc changes, check authority/version links, stale status language, Markdown links, and whitespace. Documentation changes are committed separately from code.
