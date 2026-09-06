"""Section Contracts（Phase 0B）：机器可读报告章节契约。"""

from contracts.schema import (
    BLOCKING_LEVELS,
    COVERAGE_ROLES,
    CREDIT_TYPES,
    QUESTION_STATES,
    SECTION_ORDER,
    BaselineContractMapping,
    Condition,
    CompletionRule,
    ContractReviewMatrix,
    ContractValidationResult,
    EvaluationRule,
    EvidenceRequirement,
    KeyQuestion,
    MissingPolicy,
    OutputRequirement,
    ResolvedSectionContract,
    ResolvedTopic,
    SectionContract,
    TopicContract,
)
from contracts.loader import load_contracts, parse_contracts
from contracts.validator import validate_contracts
from contracts.blocking import classify_blocking
from contracts.review import build_review_matrix, resolve_contracts

__all__ = [
    "BLOCKING_LEVELS",
    "COVERAGE_ROLES",
    "CREDIT_TYPES",
    "QUESTION_STATES",
    "SECTION_ORDER",
    "BaselineContractMapping",
    "Condition",
    "CompletionRule",
    "ContractReviewMatrix",
    "ContractValidationResult",
    "EvaluationRule",
    "EvidenceRequirement",
    "KeyQuestion",
    "MissingPolicy",
    "OutputRequirement",
    "ResolvedSectionContract",
    "ResolvedTopic",
    "SectionContract",
    "TopicContract",
    "load_contracts",
    "parse_contracts",
    "validate_contracts",
    "classify_blocking",
    "build_review_matrix",
    "resolve_contracts",
]
