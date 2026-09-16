"""R2 修复 A：版本化 / 确定性 / 公司无关的主题边界策略（从冻结 Contract v2 + WritingSpec 派生）。

对 mixed block 区分「主题内标题」与「主题外标题」：在**首个真实主题外标题**处停边界，
并给出「当前主题相关前缀 + 首个主题外标题 + 边界处置 + 是否停止该方向扩读」。

只做确定性结构判断（零 LLM / 零网络 / 零公司硬编码 / 零固定页码），分类法按 ``aspect_id``
版本化。**不再维护只针对「当前三个样例」的手工词典**：internal/external 关键词全部从
冻结 Contract v2（topic 标题 / question / aspect requirement_text）与 WritingSpec 归属
（toc H2 + mappings 的 subsection 归属）确定性派生，绑定契约内容指纹（版本化）。

匹配顺序：先 external（越界标题，命中即停），再 internal（主题内，继续）；均不命中时，
**结合文档标题层级**（P1-A.3）：层级深于主题小节标题 → in_topic（主题小节内部子标题），
同级/更浅 → 仍记 ``ambiguous``（**不是** out_of_topic），由结构另行判为「主题小节关闭」。

P1-A.1/A.4：策略状态四态化。``policy_self_consistent``（策略可派生且内部自洽，旧实现
误称 ``boundary_semantics_verified``）与 ``boundary_semantics_verified``（由**文档自身
标题层级**派生的用例独立验证通过）严格分开；``boundary_eligibility`` 供正式扩读路径消费。

P1-A.4：``ambiguous`` ≠ ``out_of_topic``。同级/更浅的兄弟标题只**关闭主题小节**（结构性、
向前），绝不冒充主题外，也绝不触发对已采纳同 Topic 材料的回溯撤回。

未知 aspect（不在 Contract v2 topic_harness 覆盖内）→ ``boundary_policy_unavailable``
（fail-closed）：绝不默认 ambiguous 后无限采纳、也绝不静默视为边界闭合；上层扩读与验收
据此显式保持 ``boundary_incomplete``。

本模块被 ``harness.context_expansion`` 单向依赖（本模块不回引 context_expansion，
避免循环导入）。子标题编号正则与 context_expansion 的历史结构信号同语义；标题层级判定
统一由 ``harness.heading_structure``（单一真相）提供。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

TOPIC_BOUNDARY_VERSION = "2"

# 子主题标题编号（与 context_expansion 历史结构信号同语义；MULTILINE 以识别行首标题）。
_SUBHEADING_NUMBERING = re.compile(
    r"^\s*(?:"
    r"[（(]\s*[一二三四五六七八九十百千\d]+\s*[）)]\s*"  # （一）（二）（1）（2）
    r"|[一二三四五六七八九十百千\d]+[、.．]"              # 一、 二、 1、 2.
    r"|第\s*[一二三四五六七八九十百千\d]+\s*[章节条款]"
    r")", re.MULTILINE)

# 条目枚举（一是/二是/三是）→ 继续同一主题，非新标题。
_ITEM_ENUMERATION = re.compile(r"^\s*[一二三四五六七八九十百千\d]+\s*是")

# 非锚定子标题编号（用于块内新标题检测，可识别句读后紧跟的标题）。
# 刻意**不含** ASCII ``.`` / 全角 ``．`` 编号：避免把「26.9%」之类百分比数值误判为
# 「1.」编号标题（P58 行业分析数值块）。顿号「、」与「（一）/（1）/第X章」无此歧义。
_SUBHEADING_ANYWHERE = re.compile(
    r"(?:"
    r"[（(]\s*[一二三四五六七八九十百千\d]+\s*[）)]\s*"  # （一）（1）(1)
    r"|[一二三四五六七八九十百千\d]+\s*[、]"              # 一、 1、
    r"|第\s*[一二三四五六七八九十百千\d]+\s*[章节条款]"    # 第X章
    r")")

# 分类取值。
TOPIC_IN_TOPIC = "in_topic"
TOPIC_OUT_OF_TOPIC = "out_of_topic"
TOPIC_AMBIGUOUS = "ambiguous"

# 边界策略不可用标记（fail-closed）：该 aspect 主题边界无法从冻结契约派生 → 上层扩读/验收
# 据此保持 boundary_incomplete，绝不默认 ambiguous 无限采纳、绝不静默视为边界闭合。
BOUNDARY_POLICY_UNAVAILABLE = "boundary_policy_unavailable"

# 覆盖审计四态（修复 A.1 / P1-A.1）：把「策略可生成 / 内部自洽」与「真实边界已验证」
# 彻底分开 —— 前者只说明策略能从冻结契约派生且自证不矛盾（循环自证，**不构成**验证），
# 后者必须由**文档自身标题层级**派生的独立用例验证（见 ``verify_boundary_semantics``）。
POLICY_GENERATED = "policy_generated"          # 策略已派生（available=True），未自洽
POLICY_SELF_CONSISTENT = "policy_self_consistent"  # 策略内部自洽（自身 topic 标题 in_topic、
                                                   # 至少一个其他 topic 标题 out_of_topic）
BOUNDARY_SEMANTICS_VERIFIED = "boundary_semantics_verified"  # 已由文档结构独立用例验证

# 冻结 Contract v2 载体（R2 材料验收的权威契约，与 material_slice_runner 同一载体）。
_CONTRACT_V2_ASSET = Path("templates/contracts/standard_v3.yaml")
# 冻结 WritingSpec 载体（WritingSpec 归属：toc H2 + mappings 的 subsection 归属）。
_WRITING_SPEC_ASSET = Path("templates/writing_specs/credit_report_v1.yaml")


# ---------------------------------------------------------------------------
# 确定性领域词组切分（契约标题 / requirement_text / question 文本 → 领域词组）
# ---------------------------------------------------------------------------

def _dedup_preserve(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return tuple(out)


def _normalize_segment(q: str) -> str:
    q = " ".join(q.split())
    q = q.strip(" \t,，;；:：、。；")
    # 去尾缀「等…」（如「定向增发等影响经营的事件」→「定向增发」）；「等」仅作列表收尾语气词。
    if "等" in q:
        q = q.split("等", 1)[0].strip()
    return q


def _cjk_len(kw: str) -> int:
    """CJK 字符数（中文标题关键词按中文计长，英文字母/数字不计）。"""
    return sum(1 for c in kw if "一" <= c <= "鿿")


def _is_cjk_keyword(kw: str, min_cjk: int) -> bool:
    """领域词组是否可作中文披露标题关键词（确定性结构过滤，零词典）。

    - 剔除含 ASCII 字母/数字/百分号/半角波浪号的片段（英文缩写/数值，如 NOT_APPLICABLE、
      ROE、3~5、15% —— 不会出现在中文标题）。
    - 按 CJK 字符计长，不足 ``min_cjk`` 的剔除（单字「以/者/家」与过短泛词不入关键词）。
    """
    if not kw:
        return False
    if re.search(r"[A-Za-z0-9%~]", kw):
        return False
    return _cjk_len(kw) >= min_cjk


_CONNECTORS = "及和与"


def _split_connectors(p: str) -> list[str]:
    """按连接词 及/和/与 切分，但仅当连接词**两侧都 ≥2 CJK 字符**时才切。

    及/和/与 既可作列表连接词，也可作词内字（「参与者」的「与」、「涉及主体」的「及」）。
    仅当两侧都足够长（≥2 CJK）才视为连接词：``客户与供应商集中度`` → ``客户``/``供应商集中度``；
    ``参与者`` → 不动；``涉及主体`` → 不动；``金融机构借款和对外担保`` → ``金融机构借款``/``对外担保``。
    """
    positions = [i for i, ch in enumerate(p) if ch in _CONNECTORS]
    if not positions:
        return [p]
    out: list[str] = []
    start = 0
    for i in positions:
        left = p[start:i]
        right = p[i + 1:]
        if _cjk_len(left) >= 2 and _cjk_len(right) >= 2:
            out.append(left)
            start = i + 1
    out.append(p[start:])
    return [seg for seg in out if seg.strip()]


def domain_segments(text: str | None) -> tuple[str, ...]:
    """把中文披露标题/需求文本确定性拆为领域词组（去括号、按列表分隔符/连接词/空白切分）。

    仅作结构切分（零语义/零词典），结果用于 internal/external 关键词派生。切分字符：
    空白、顿号/逗号/分号/冒号/斜杠 与连接词 及/和/与。每个词组去首尾标点、去「等…」尾缀。

    折行处理：括号换作顿号（「主营业务构成（分板块/分部）」→「主营业务构成 / 分板块 / 分部」），
    相邻 CJK 字符之间的空白视为 YAML 折行 artifact 而合并（「主要参与 者」→「主要参与者」），
    避免折行产生「主要参」「其偿债影响」等残缺片段。
    """
    if not text:
        return ()
    s = unicodedata.normalize("NFC", str(text))
    s = s.replace("（", "、").replace("）", "、").replace("(", "、").replace(")", "、")
    # 合并相邻 CJK 之间的空白（YAML 折行 artifact）；CJK 与 ASCII 之间的空白仍作分隔。
    s = re.sub(r"(?<=[一-鿿])\s+(?=[一-鿿])", "", s)
    parts = re.split(r"[\s、，,；;：:/]+", s)
    out: list[str] = []
    for p in parts:
        for q in _split_connectors(p):
            seg = _normalize_segment(q)
            if seg:
                out.append(seg)
    return _dedup_preserve(out)


# ---------------------------------------------------------------------------
# 冻结资产加载（契约 v2 + WritingSpec v1，均 fail-closed）
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_contract():
    """加载并校验冻结 Contract v2（fail-closed：非 v2 / 校验失败 → 抛错）。"""
    from contracts import loader_v2 as LV2
    from contracts import schema_v2 as SV2
    from contracts import validator_v2 as VV2

    contract = LV2.load_contract_v2(str(_CONTRACT_V2_ASSET))
    if contract.contract_version != SV2.CONTRACT_VERSION_V2:
        raise RuntimeError(f"主题边界策略要求 Contract v2，得到 {contract.contract_version!r}")
    result = VV2.validate_contract_v2(contract)
    if not result.valid:
        raise RuntimeError(f"冻结 Contract v2 校验失败: {result.errors[:5]}")
    return contract


@lru_cache(maxsize=1)
def _load_writing_spec() -> dict:
    """加载 WritingSpec v1（toc + mappings），返回 {subsection_id -> h2, aspect_id -> subsection_id}。"""
    import yaml

    raw = yaml.safe_load(_WRITING_SPEC_ASSET.read_text(encoding="utf-8"))
    toc = raw.get("toc") or {}
    h2_by_sub: dict[str, str] = {}
    for _group, subs in toc.items():
        if not isinstance(subs, list):
            continue
        for sub in subs:
            if isinstance(sub, dict):
                h2_by_sub[str(sub.get("subsection_id", ""))] = str(sub.get("h2", ""))
    aspect_to_sub: dict[str, str] = {}
    for m in raw.get("mappings") or []:
        if isinstance(m, dict) and m.get("aspect_id") and m.get("subsection_id"):
            aspect_to_sub[str(m["aspect_id"])] = str(m["subsection_id"])
    return {"h2_by_sub": h2_by_sub, "aspect_to_sub": aspect_to_sub}


# ---------------------------------------------------------------------------
# 版本化主题边界策略（派生）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TopicBoundaryPolicy:
    """某 aspect 的版本化、确定性主题边界策略（全部从冻结契约派生，零手工公司词典）。"""

    aspect_id: str
    available: bool
    internal_keywords: tuple[str, ...]
    external_keywords: tuple[str, ...]
    topic_id: str
    writing_subsection: str
    policy_version: str            # 绑定契约/写作规范派生内容指纹（版本化确定性）
    reason: str = ""               # unavailable 时的原因（boundary_policy_unavailable）


def _aspect_own_segments(contract, aspect_id: str) -> tuple[str, ...]:
    """某 aspect 的自身领域词组：其 topic 标题 + 自身 requirement_text + 自身 question。"""
    for section in contract.sections:
        for topic in section.topics:
            for q in topic.questions:
                for a in q.aspects:
                    if a.aspect_id == aspect_id:
                        return _dedup_preserve(
                            list(domain_segments(topic.title))
                            + list(domain_segments(a.requirement_text))
                            + list(domain_segments(q.question)))
    return ()


def _topic_id_of(contract, aspect_id: str) -> str:
    for section in contract.sections:
        for topic in section.topics:
            for q in topic.questions:
                for a in q.aspects:
                    if a.aspect_id == aspect_id:
                        return topic.topic_id
    return ""


def _external_pool_for(contract, own_topic_id: str) -> list[str]:
    """其他 topic（≠ own_topic）**标题**的领域词组并集。

    外部越界标记只取各 topic 的权威简明标题（19 条标题是「该主题讲什么」的确定性浓缩），
    **不**取 requirement_text / question 的冗长叙述（其中夹带英文缩写、数值与短泛词，会产生
    「以/者/风险/原因/单位」等错误越界标记）。标题已覆盖全部跨主题边界标记（如「在建工程」
    「公司治理」「关联交易」「对外担保」）。
    """
    pool: list[str] = []
    for section in contract.sections:
        for topic in section.topics:
            if topic.topic_id == own_topic_id:
                continue
            pool.extend(domain_segments(topic.title))
    return _dedup_preserve(pool)


def _conflict(kw: str, internal: tuple[str, ...]) -> bool:
    """external 关键词若与任一 internal 关键词互为子串 → 冲突（须剔除，避免误停主题内）。

    例：internal 含「供应商集中度」，external 含「集中度」→「集中度」是「供应商集中度」子串，
    若保留会把主题内标题「客户集中度」误判越界，故剔除。
    """
    return any(kw in ik or ik in kw for ik in internal)


@lru_cache(maxsize=1)
def _derive_policies() -> dict[str, TopicBoundaryPolicy]:
    contract = _load_contract()
    ws = _load_writing_spec()
    h2_by_sub = ws["h2_by_sub"]
    aspect_to_sub = ws["aspect_to_sub"]

    # 逐 topic 的标题词组（供 external 派生时跨 topic 命中）。
    topic_title_segments: dict[str, tuple[str, ...]] = {}
    for section in contract.sections:
        for topic in section.topics:
            topic_title_segments[topic.topic_id] = domain_segments(topic.title)

    policies: dict[str, TopicBoundaryPolicy] = {}
    for section in contract.sections:
        for topic in section.topics:
            if topic.producer_kind != "topic_harness":
                continue
            external_pool = tuple(
                kw for kw in _external_pool_for(contract, topic.topic_id)
                if _is_cjk_keyword(kw, min_cjk=3))
            for q in topic.questions:
                for a in q.aspects:
                    if a.producer_kind != "topic_harness":
                        continue
                    sub = aspect_to_sub.get(a.aspect_id, "")
                    own_h2 = h2_by_sub.get(sub, "")
                    own = _dedup_preserve([
                        kw for kw in (
                            list(domain_segments(topic.title))
                            + list(domain_segments(a.requirement_text))
                            + list(domain_segments(q.question))
                            + list(domain_segments(own_h2)))
                        if _is_cjk_keyword(kw, min_cjk=2)])
                    if not own:
                        policies[a.aspect_id] = TopicBoundaryPolicy(
                            aspect_id=a.aspect_id, available=False,
                            internal_keywords=(), external_keywords=(),
                            topic_id=topic.topic_id, writing_subsection=sub,
                            policy_version=TOPIC_BOUNDARY_VERSION,
                            reason=f"{BOUNDARY_POLICY_UNAVAILABLE}: 无法从契约派生主题内关键词")
                        continue
                    external = tuple(
                        kw for kw in external_pool if kw and not _conflict(kw, own))
                    policies[a.aspect_id] = TopicBoundaryPolicy(
                        aspect_id=a.aspect_id, available=True,
                        internal_keywords=own, external_keywords=external,
                        topic_id=topic.topic_id, writing_subsection=sub,
                        policy_version=_policy_identity(a.aspect_id, own, external),
                        reason="")
    return policies


@lru_cache(maxsize=1)
def _content_fingerprints() -> dict[str, str]:
    """冻结资产内容指纹（Contract v2 + WritingSpec v1 原始字节 sha256）。

    policy_version 必须绑定这两个指纹：派生关键词/契约/写作规范内容任一漂移 → 指纹变化
    （修复 A.1「policy identity binding Contract/WritingSpec content fingerprints」）。
    """
    return {
        "contract": hashlib.sha256(_CONTRACT_V2_ASSET.read_bytes()).hexdigest(),
        "writing_spec": hashlib.sha256(_WRITING_SPEC_ASSET.read_bytes()).hexdigest(),
    }


def _policy_identity(aspect_id: str, internal: tuple[str, ...],
                     external: tuple[str, ...]) -> str:
    """主题边界策略内容指纹（版本化 + 绑定冻结资产内容指纹）。"""
    fps = _content_fingerprints()
    return hashlib.sha256(json.dumps(
        {"version": TOPIC_BOUNDARY_VERSION, "aspect_id": aspect_id,
         "internal": list(internal), "external": list(external),
         "contract_fingerprint": fps["contract"],
         "writing_spec_fingerprint": fps["writing_spec"]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


_UNAVAILABLE_POLICY = TopicBoundaryPolicy(
    aspect_id="", available=False, internal_keywords=(), external_keywords=(),
    topic_id="", writing_subsection="", policy_version=TOPIC_BOUNDARY_VERSION,
    reason=f"{BOUNDARY_POLICY_UNAVAILABLE}: aspect 不在冻结 Contract v2 topic_harness 覆盖内")


def topic_boundary_policy(aspect_id: str) -> TopicBoundaryPolicy:
    """取某 aspect 的派生主题边界策略；未知 aspect → ``available=False``（fail-closed）。"""
    policies = _derive_policies()
    return policies.get(aspect_id, _UNAVAILABLE_POLICY)


def topic_taxonomy(aspect_id: str) -> dict[str, tuple[str, ...]] | None:
    """向后兼容包装：返回 ``{"internal": …, "external": …}``；无可用策略 → None。"""
    policy = topic_boundary_policy(aspect_id)
    if not policy.available:
        return None
    return {"internal": policy.internal_keywords, "external": policy.external_keywords}


@lru_cache(maxsize=1)
def _topic_title_by_id() -> dict[str, str]:
    """topic_id → 冻结 Contract v2 的 topic 权威标题（供边界语义验证使用）。"""
    contract = _load_contract()
    out: dict[str, str] = {}
    for section in contract.sections:
        for topic in section.topics:
            out[topic.topic_id] = topic.title
    return out


def _policy_self_consistent(policy: TopicBoundaryPolicy) -> bool:
    """策略内部自洽检查（P1-A.1：**改名**自旧的 ``_boundary_semantics_verified``）。

    旧实现把它当作 ``boundary_semantics_verified``，但它的两个输入都来自策略自身派生的
    关键词（自身 topic 权威标题 + 其他 topic 权威标题），是**循环自证**：它只能说明
    「策略没有自我矛盾」，**不能**说明真实文档里的边界已被验证。因此它现在只判
    ``policy_self_consistent``。

    条件：策略可用 + internal/external 关键词均非空 + 自身 topic 标题判 in_topic +
    至少一个其他 topic 标题判 out_of_topic + policy_version 已绑定冻结资产内容指纹。
    """
    if not policy.available:
        return False
    if not policy.internal_keywords or not policy.external_keywords:
        return False
    titles = _topic_title_by_id()
    own_title = titles.get(policy.topic_id, "")
    if not own_title:
        return False
    if classify_heading_topic(own_title, policy.aspect_id) != TOPIC_IN_TOPIC:
        return False
    any_out = any(
        classify_heading_topic(t, policy.aspect_id) == TOPIC_OUT_OF_TOPIC
        for tid, t in titles.items() if tid != policy.topic_id and t)
    return any_out


# ---------------------------------------------------------------------------
# P1-A.1/A.3：独立边界语义验证（用例来自**文档自身标题层级**，非策略关键词）
# ---------------------------------------------------------------------------

# 验证用例的标题关系类别。
VERIFY_TOPIC_HEADING = "topic_heading"        # 主题小节标题本身（section_path 叶子）
VERIFY_CHILD = "child"                        # 主题小节内的更深层级子标题
VERIFY_SIBLING_OR_OUTER = "sibling_or_outer"  # 同级/更浅标题（结构性关闭信号）


@dataclass(frozen=True)
class HeadingCase:
    """一条独立验证用例：文档给的标题 + 其**结构性位置**（关系类别）。"""

    heading: str
    level: int | None
    relation: str   # topic_heading | child | sibling_or_outer
    source: str     # section_path | block_heading

    def to_dict(self) -> dict:
        return {"heading": self.heading, "level": self.level,
                "relation": self.relation, "source": self.source}


@dataclass(frozen=True)
class BoundarySemanticsVerification:
    """独立边界语义验证结论（P1-A.1）。

    ``verified=True`` 仅当：策略可用且自洽、用例集非空、含至少一个**主题小节内**用例
    （topic_heading/child）、且每个用例的分类都与**结构性期望**一致、且至少一个用例
    的分类是有判别的（in_topic/out_of_topic），证明策略真的在判别而非全 ambiguous。
    """

    aspect_id: str
    verified: bool
    reason: str
    cases: tuple[HeadingCase, ...] = ()
    policy_version: str = ""

    def to_dict(self) -> dict:
        return {"aspect_id": self.aspect_id, "verified": self.verified,
                "reason": self.reason, "policy_version": self.policy_version,
                "cases": [c.to_dict() for c in self.cases]}


def verification_cases_from_material(aspect_id: str, block_texts: tuple[str, ...] | list[str],
                                     section_path: tuple[str, ...] | list[str] | None
                                     ) -> tuple[HeadingCase, ...]:
    """从**真实材料**派生独立验证用例（P1-A.1/A.3）。

    期望标签来自文档自身结构，绝不来自策略关键词：

    - ``topic_heading``：``section_path`` 叶子在文本中的实际标题（主题小节标题本身）；
    - ``child``：层级**深于**主题小节标题的标题（结构上属于主题小节内部）；
    - ``sibling_or_outer``：层级**同级或更浅**的标题（结构上关闭主题小节）。

    只有层级两侧都已知时才产生 child/sibling_or_outer 用例（层级未知绝不虚构期望）。
    """
    from harness import heading_structure as HS

    texts = tuple(t or "" for t in (block_texts or ()))
    leaf = ""
    for seg in reversed(tuple(section_path or ())):
        if seg and str(seg).strip():
            leaf = str(seg).strip()
            break

    topic_level: int | None = None
    topic_heading = ""
    if leaf:
        leaf_body = HS.strip_leading_numbering(leaf)
        for t in texts:
            for span in HS.iter_heading_spans(t):
                body = HS.strip_leading_numbering(span.heading)
                if not body:
                    continue
                if body == leaf_body or body.startswith(leaf_body) or leaf_body.startswith(body):
                    topic_level = span.level
                    topic_heading = span.heading
                    break
            if topic_heading:
                break
        if not topic_heading:
            topic_level, _src = HS.topic_level_of(texts[0] if texts else "",
                                                  tuple(section_path or ()))
            topic_heading = leaf_body

    cases: list[HeadingCase] = []
    seen: set[tuple[str, str]] = set()

    def _add(heading: str, level: int | None, relation: str, source: str) -> None:
        key = (heading, relation)
        if heading and key not in seen:
            seen.add(key)
            cases.append(HeadingCase(heading, level, relation, source))

    if topic_heading:
        _add(topic_heading, topic_level, VERIFY_TOPIC_HEADING, "section_path")
    for t in texts:
        for span in HS.iter_heading_spans(t):
            body = HS.strip_leading_numbering(span.heading)
            if not body:
                continue
            if topic_heading and HS.strip_leading_numbering(topic_heading) == body:
                continue
            if HS.sibling_or_outer(span.level, topic_level):
                _add(span.heading, span.level, VERIFY_SIBLING_OR_OUTER, "block_heading")
            elif HS.child_of_topic(span.level, topic_level):
                _add(span.heading, span.level, VERIFY_CHILD, "block_heading")
    return tuple(cases)


def verify_boundary_semantics(aspect_id: str,
                              cases: tuple[HeadingCase, ...] | list[HeadingCase]
                              ) -> BoundarySemanticsVerification:
    """独立验证主题边界语义（P1-A.1）：文档结构给期望，策略给分类，两者必须一致。

    - ``topic_heading`` / ``child``：策略**不得**判 ``out_of_topic``（否则会把主题自身或
      其内部子标题当作越界 → 静默丢材料或误停）；
    - ``sibling_or_outer``：策略**不得**判 ``in_topic``（否则会把兄弟小节内容当主题内材料）。
    """
    policy = topic_boundary_policy(aspect_id)
    cases = tuple(cases or ())
    if not policy.available:
        return BoundarySemanticsVerification(aspect_id, False, policy.reason, cases)
    if not _policy_self_consistent(policy):
        return BoundarySemanticsVerification(
            aspect_id, False, "policy_not_self_consistent", cases, policy.policy_version)
    if not cases:
        return BoundarySemanticsVerification(
            aspect_id, False, "no_document_derived_cases", cases, policy.policy_version)
    in_section = [c for c in cases
                  if c.relation in (VERIFY_TOPIC_HEADING, VERIFY_CHILD)]
    if not in_section:
        return BoundarySemanticsVerification(
            aspect_id, False, "no_in_section_case", cases, policy.policy_version)
    discriminating = False
    for c in cases:
        cls = classify_heading_topic(c.heading, aspect_id, level=c.level,
                                     topic_level=_topic_level_of_cases(cases))
        if cls in (TOPIC_IN_TOPIC, TOPIC_OUT_OF_TOPIC):
            discriminating = True
        if c.relation in (VERIFY_TOPIC_HEADING, VERIFY_CHILD):
            if cls == TOPIC_OUT_OF_TOPIC:
                return BoundarySemanticsVerification(
                    aspect_id, False,
                    f"in_section_heading_classified_out_of_topic: {c.heading[:40]}",
                    cases, policy.policy_version)
        elif c.relation == VERIFY_SIBLING_OR_OUTER:
            if cls == TOPIC_IN_TOPIC:
                return BoundarySemanticsVerification(
                    aspect_id, False,
                    f"sibling_heading_classified_in_topic: {c.heading[:40]}",
                    cases, policy.policy_version)
    if not discriminating:
        return BoundarySemanticsVerification(
            aspect_id, False, "policy_never_discriminates", cases, policy.policy_version)
    return BoundarySemanticsVerification(
        aspect_id, True, "document_structure_agrees", cases, policy.policy_version)


def _topic_level_of_cases(cases: tuple[HeadingCase, ...]) -> int | None:
    """从用例集中取主题小节标题的层级（无 topic_heading 用例 → None）。"""
    for c in cases:
        if c.relation == VERIFY_TOPIC_HEADING and c.level is not None:
            return c.level
    return None


@dataclass(frozen=True)
class BoundaryEligibility:
    """边界资格状态（P1-A.2）：正式扩读路径必须消费本状态，不得静默扩读。

    ``eligible`` 仅当边界语义已由文档结构**独立验证**；``available`` 但未验证 →
    ``eligible=False``（材料不得被验收为 accepted），策略不可用 → fail-closed 不扩读。
    """

    aspect_id: str
    eligible: bool
    status: str
    reason: str
    policy_version: str = ""

    def to_dict(self) -> dict:
        return {"aspect_id": self.aspect_id, "eligible": self.eligible,
                "status": self.status, "reason": self.reason,
                "policy_version": self.policy_version}


def boundary_eligibility(aspect_id: str, *,
                         verification: BoundarySemanticsVerification | None = None,
                         policy: TopicBoundaryPolicy | None = None) -> BoundaryEligibility:
    """消费边界资格状态（P1-A.2）：无可用策略 → unavailable；未独立验证 → 不 eligible。"""
    p = policy if policy is not None else topic_boundary_policy(aspect_id)
    if not p.available:
        return BoundaryEligibility(aspect_id, False, BOUNDARY_POLICY_UNAVAILABLE,
                                   p.reason, p.policy_version)
    if verification is not None and verification.verified:
        return BoundaryEligibility(aspect_id, True, BOUNDARY_SEMANTICS_VERIFIED,
                                   verification.reason, p.policy_version)
    if _policy_self_consistent(p):
        return BoundaryEligibility(
            aspect_id, False, POLICY_SELF_CONSISTENT,
            "boundary_semantics_not_verified: 策略内部自洽未经文档结构独立验证",
            p.policy_version)
    return BoundaryEligibility(aspect_id, False, POLICY_GENERATED,
                               "policy_not_self_consistent", p.policy_version)


def topic_boundary_coverage(aspect_ids: tuple[str, ...] | None = None,
                            verifications: "dict[str, BoundarySemanticsVerification] | None" = None
                            ) -> dict:
    """覆盖审计（P1-A.1）：对全部 topic_harness aspects 四态分类。

    返回 ``{"version", "total", "boundary_policy_unavailable", "policy_generated",
    "policy_self_consistent", "boundary_semantics_verified", "aspects": {...}}``。

    四态：``boundary_policy_unavailable`` / ``policy_generated``（派生但未自洽）/
    ``policy_self_consistent``（内部自洽，**仍非**已验证）/ ``boundary_semantics_verified``
    （由 ``verifications`` 传入的文档结构独立验证通过）。**不传** ``verifications`` 时
    绝不会有任何 aspect 被判为已验证（真实边界未被验证，如实报告）。
    """
    if aspect_ids is None:
        contract = _load_contract()
        aspect_ids = tuple(a.aspect_id for a in contract.all_aspects()
                           if a.producer_kind == "topic_harness")
    coverage = {
        "version": TOPIC_BOUNDARY_VERSION,
        "total": len(aspect_ids),
        "boundary_policy_unavailable": 0,
        "policy_generated": 0,
        "policy_self_consistent": 0,
        "boundary_semantics_verified": 0,
        "aspects": {},
    }
    for aid in aspect_ids:
        p = topic_boundary_policy(aid)
        ver = (verifications or {}).get(aid)
        if not p.available:
            status = BOUNDARY_POLICY_UNAVAILABLE
            coverage["boundary_policy_unavailable"] += 1
        elif ver is not None and ver.verified:
            status = BOUNDARY_SEMANTICS_VERIFIED
            coverage["boundary_semantics_verified"] += 1
        elif _policy_self_consistent(p):
            status = POLICY_SELF_CONSISTENT
            coverage["policy_self_consistent"] += 1
        else:
            status = POLICY_GENERATED
            coverage["policy_generated"] += 1
        coverage["aspects"][aid] = {
            "available": p.available,
            "status": status,
            "topic_id": p.topic_id,
            "writing_subsection": p.writing_subsection,
            "internal_keywords": list(p.internal_keywords),
            "external_keywords": list(p.external_keywords),
            "policy_version": p.policy_version,
            "reason": p.reason,
            "verification": ver.to_dict() if ver is not None else None,
        }
    return coverage


def classify_heading_topic(heading: str, aspect_id: str, *,
                           level: int | None = None,
                           topic_level: int | None = None) -> str:
    """分类单个标题片段：``in_topic`` / ``out_of_topic`` / ``ambiguous``。

    P1-A.3：分类必须结合**文档标题层级**，不能只看关键词。匹配顺序：

    1. external 关键词命中 → ``out_of_topic``（停止）；
    2. internal 关键词命中 → ``in_topic``（继续）；
    3. 结构：标题层级**深于**主题小节标题层级 → ``in_topic``（它是主题小节内部的子标题，
       如「（1）整体情况 / （2）销售情况 / （3）生产情况」）；
    4. 其余 → ``ambiguous``。

    注意第 4 条：**同级/更浅的兄弟标题仍是 ``ambiguous``，不是 ``out_of_topic``**（P1-A.4）。
    它由 ``find_topic_boundary`` 按结构判为「主题小节关闭」，既不冒充主题外，也不回溯撤回
    同 Topic 材料。``level``/``topic_level`` 未知时不做结构判定（绝不猜）。
    """
    h = unicodedata.normalize("NFC", (heading or "").strip())
    if not h:
        return TOPIC_AMBIGUOUS
    tax = topic_taxonomy(aspect_id)
    if tax is None:
        return TOPIC_AMBIGUOUS
    for kw in tax.get("external", ()):
        if kw in h:
            return TOPIC_OUT_OF_TOPIC
    for kw in tax.get("internal", ()):
        if kw in h:
            return TOPIC_IN_TOPIC
    from harness import heading_structure as HS
    if HS.child_of_topic(level, topic_level):
        return TOPIC_IN_TOPIC
    return TOPIC_AMBIGUOUS


def is_structurally_outer_heading(heading: str, *, level: int | None,
                                  topic_level: int | None) -> bool:
    """该标题是否被**文档结构证明**为主题小节的同级/更浅标题（P1-A.4 撤回前提）。

    仅当「层级两侧已知且 level ≤ topic_level」时成立。ambiguous 且层级未知 → 绝不成立
    （禁止据 ambiguous 自动撤回同 Topic 材料）。
    """
    from harness import heading_structure as HS
    if not (heading or "").strip():
        return False
    return HS.sibling_or_outer(level, topic_level)


def extract_new_headings(text: str | None) -> list[tuple[int, str]]:
    """提取块内**所有**新标题片段（``(char_offset, heading)``），按出现顺序。

    识别句读后（或行首）紧跟的子标题编号（（一）/1、/第X节/一、），并取编号后**标题全文**
    （到行尾，截 40 字符）供主题分类（仅编号「（四）」不含「安全生产」等关键词，无法分类）。
    「一是/二是」条目枚举不算新标题；块首（char_offset==0）不算「块内新标题」。
    与 context_expansion 历史结构信号同语义；额外排除「26.9%」等数值编号（见 _SUBHEADING_ANYWHERE）。
    """
    s = unicodedata.normalize("NFC", (text or "").strip())
    if not s:
        return []
    headings: list[tuple[int, str]] = []
    for m in _SUBHEADING_ANYWHERE.finditer(s):
        if m.start() == 0:
            continue
        head = m.group(0).strip()
        tail = s[m.end():m.end() + 1]
        if _ITEM_ENUMERATION.match(head + tail):
            continue
        # 编号后标题全文（到行尾），供 external/internal 关键词分类。
        heading_text = s[m.start():].split("\n", 1)[0].strip()
        headings.append((m.start(), heading_text[:40]))
    return headings


@dataclass(frozen=True)
class TopicBoundaryResult:
    """mixed block 的主题边界判定结果。

    - ``has_out_of_topic``：块内是否出现**关键词证明的**主题外标题；
    - ``out_of_topic_heading``：首个真实主题外标题（存在时）；
    - ``relevant_prefix``：主题内相关前缀（边界标题之前的文本，已 strip）；
    - ``char_offset``：边界标题起始字符位置（前缀 == 原文[:char_offset]）；
    - ``stop_direction``：是否停止该方向扩读（出现边界标题即 True）；
    - ``closure_heading`` / ``closure_kind``（P1-A.4）：**结构性关闭**点——同级/更浅的
      兄弟小节标题，文档层级证明主题小节在此结束。它**不是** ``out_of_topic``
      （``has_out_of_topic`` 仍为 False），也绝不触发对已采纳材料的回溯撤回。
    """

    has_out_of_topic: bool
    out_of_topic_heading: str | None
    relevant_prefix: str
    char_offset: int | None
    stop_direction: bool
    closure_heading: str | None = None
    closure_kind: str | None = None
    closure_level: int | None = None
    topic_level: int | None = None

    @property
    def has_closure(self) -> bool:
        return self.closure_kind is not None


def block_start_topic_class(text: str | None, aspect_id: str) -> str | None:
    """块首标题的主题分类（``in_topic`` / ``out_of_topic`` / ``ambiguous``）；无块首标题 → None。

    用于「块首即为新标题」的块：若块首标题即主题外（如十、未来发展规划），应直接哨兵停止，
    无主题内前缀可投影。只识别块首行标题；「一是/二是」条目枚举 → None（非标题）。
    """
    s = unicodedata.normalize("NFC", (text or "").strip())
    if not s:
        return None
    m = _SUBHEADING_NUMBERING.match(s)
    if not m:
        return None
    head = m.group(0).strip()
    tail = s[m.end():m.end() + 1]
    if _ITEM_ENUMERATION.match(head + tail):
        return None
    line = s[m.start():].split("\n", 1)[0].strip()
    return classify_heading_topic(line, aspect_id)


def find_topic_boundary(text: str | None, aspect_id: str,
                        topic_level: int | None = None) -> TopicBoundaryResult:
    """在 mixed block 内定位主题边界：返回首个边界标题 + 其前的主题内前缀。

    两类边界（P1-A.3/A.4）：

    1. **关键词证明的主题外标题**（external 命中）→ ``has_out_of_topic=True``，
       按旧口径给出哨兵 + 前缀片段；
    2. **文档结构证明的兄弟标题**（层级 ≤ 主题小节标题层级，且不是主题外标题）→
       ``closure_kind="same_or_shallower_level_sibling"``，``has_out_of_topic`` 仍为
       False：主题小节在此结束（同级小节开始），但**不冒充主题外**，也不回溯撤回此前已
       采纳的同 Topic 材料。

    无边界 → ``has_out_of_topic=False`` 且无 ``closure``（不停止，交由上层既有
    mixed-block 逻辑）。``topic_level`` 未知（None）→ 不做结构性关闭（绝不猜）。
    本函数**不**判断「主题内是否语义充分」，只做结构边界判定（充分性留给 R3）。
    """
    from harness import heading_structure as HS

    s = unicodedata.normalize("NFC", (text or "").strip())
    if not s:
        return TopicBoundaryResult(False, None, s, None, False, topic_level=topic_level)
    for span in HS.iter_heading_spans(s):
        heading, level = span.heading, span.level
        cls = classify_heading_topic(heading, aspect_id, level=level,
                                     topic_level=topic_level)
        if cls == TOPIC_OUT_OF_TOPIC:
            return TopicBoundaryResult(
                has_out_of_topic=True,
                out_of_topic_heading=heading,
                relevant_prefix=s[:span.offset].strip(),
                char_offset=span.offset,
                stop_direction=True,
                closure_level=level,
                topic_level=topic_level)
        if cls != TOPIC_IN_TOPIC and HS.sibling_or_outer(level, topic_level):
            return TopicBoundaryResult(
                has_out_of_topic=False,
                out_of_topic_heading=None,
                relevant_prefix=s[:span.offset].strip(),
                char_offset=span.offset,
                stop_direction=True,
                closure_heading=heading,
                closure_kind="same_or_shallower_level_sibling",
                closure_level=level,
                topic_level=topic_level)
    return TopicBoundaryResult(False, None, s, None, False, topic_level=topic_level)
