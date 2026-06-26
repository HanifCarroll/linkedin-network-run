"""Recommend-only buyer-signal scoring."""

from __future__ import annotations

import re

from apps.opportunity_intel.contracts import (
    CommentEvidence,
    QueryDefinition,
    RankedComment,
    RankLevel,
)

PRIORITY_NEEDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("internal_tools", ("internal tool", "admin tool", "back office", "ops tool")),
    (
        "ai_workflows",
        ("ai workflow", "agent workflow", "ai automation", "productionize ai"),
    ),
    ("automation", ("automate", "automation", "manual process", "copy paste")),
    ("product_engineering", ("prototype", "mvp", "ship this", "build this", "custom app")),
    ("dashboards", ("dashboard", "reporting", "metrics", "kpi", "decision surface")),
    ("data_workflows", ("data workflow", "data sync", "spreadsheet", "excel", "google sheet")),
    ("integrations", ("integration", "api", "sync", "connected tools", "stitching tools")),
    ("custom_software", ("custom software", "custom tool", "internal app", "bespoke")),
)

BUYING_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("explicit_help_ask", ("who can help", "recommend someone", "looking for someone")),
    ("need_statement", ("we need", "our team needs", "my team needs", "need help")),
    ("build_request", ("who can build", "build this", "turn this into", "make this real")),
    (
        "active_evaluation",
        ("evaluating", "comparing tools", "migration", "replace our", "replace my", "replace the"),
    ),
    ("approved_work", ("budget", "approved", "ready to start", "funded")),
)

ACTION_TERMS: tuple[str, ...] = (
    "who can help",
    "recommend someone",
    "looking for someone",
    "need help",
    "who can build",
    "build this",
    "turn this into",
    "integrate",
    "automate",
)

IMMEDIACY_TERMS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        2,
        (
            "asap",
            "urgent",
            "this week",
            "this month",
            "this quarter",
            "right now",
            "ready to start",
        ),
    ),
    (1, ("soon", "next month", "currently", "planning", "roadmap")),
)

DIRECT_BUYER_HEADLINE_TERMS: tuple[str, ...] = (
    "founder",
    "co-founder",
    "owner",
    "ceo",
    "coo",
    "cfo",
    "cto",
    "head of",
    "vp ",
    "director",
    "manager",
    "operator",
    "operations",
    "product",
    "engineering",
    "finance",
    "controller",
    "revops",
    "chief",
)

BUYER_CONTEXT_TERMS: tuple[str, ...] = (
    "we ",
    "we're",
    "we are",
    "our ",
    "our team",
    "my team",
    "at my company",
)

REJECT_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("recruiter", ("recruiter", "talent acquisition", "sourcer", "hiring for")),
    ("agency", ("agency", "staffing", "recruitment agency")),
    (
        "vendor",
        (
            "i help businesses",
            "i help companies",
            "i help founders",
            "i help teams",
            "how i can help",
            "here's what i do",
            "what i do:",
            "my expertise",
            "i specialize",
            "recent shipped work",
            "more of my",
            "my portfolio",
            "portfolio:",
            "github:",
            "dms open",
            "dm me",
            "let's talk",
            "love to connect",
            "would love to connect",
            "open to connecting",
            "potential collaboration",
            "i’ve sent you a connection request",
            "i've sent you a connection request",
            "book a call",
            "my services",
            "our platform",
            "our product",
            "our tool",
            "we built",
            "i wrote more",
            "happy to share",
            "consultant",
        ),
    ),
    ("job seeker", ("open to work", "looking for a job", "my resume", "student", "internship")),
    (
        "entertainment_noise",
        ("dashboard confessional", "dashboard for cutie", "soundtrack", "superband tour"),
    ),
)

FIRST_PERSON_NEED_PATTERN = re.compile(
    r"\bi need (?:help|someone|a developer|an engineer|to "
    r"(?:automate|build|replace|integrate|fix|ship|turn|connect|create))\b",
    re.IGNORECASE,
)


def rank_comment(comment: CommentEvidence, query: QueryDefinition | None = None) -> RankedComment:
    text = comment.comment_text.casefold()
    person_text = f"{comment.commenter_headline} {comment.commenter_company}".casefold()
    combined = f"{text} {person_text}"

    matched_need_categories = _matching_signal_names(text, PRIORITY_NEEDS)
    need_categories = matched_need_categories
    if query is not None:
        need_categories = tuple(dict.fromkeys((*need_categories, *query.need_categories)))
    positive_signals = _matching_buying_signal_names(text)
    reject_reasons = _matching_signal_names(combined, REJECT_SIGNALS)
    if _same_person(comment.commenter_name, comment.post_author_name):
        reject_reasons = tuple(dict.fromkeys((*reject_reasons, "post author")))

    if reject_reasons:
        return _ranked(
            comment=comment,
            rank_level=RankLevel.IRRELEVANT,
            problem_fit=0,
            buying_signal=0,
            buyer_fit=0,
            actionability=0,
            immediacy=0,
            direct_buyer=False,
            need_categories=need_categories,
            positive_signals=positive_signals,
            fit_reasons=(),
            reject_reasons=reject_reasons,
        )

    problem_fit = _problem_fit(text, matched_need_categories)
    buying_signal = _buying_signal(text, positive_signals)
    buyer_fit = _buyer_fit(text, person_text)
    actionability = _actionability(text)
    immediacy = _immediacy(text)
    direct_buyer = buyer_fit > 0

    fit_reasons: list[str] = []
    if problem_fit:
        fit_reasons.append("problem fit")
    if buying_signal:
        fit_reasons.append("buying signal")
    if buyer_fit:
        fit_reasons.append("buyer fit")
    if actionability:
        fit_reasons.append("actionable")
    if immediacy:
        fit_reasons.append("time signal")

    total = problem_fit + buying_signal + buyer_fit + actionability + immediacy
    if not direct_buyer:
        return _ranked(
            comment=comment,
            rank_level=RankLevel.IRRELEVANT,
            problem_fit=problem_fit,
            buying_signal=buying_signal,
            buyer_fit=buyer_fit,
            actionability=actionability,
            immediacy=immediacy,
            direct_buyer=False,
            need_categories=need_categories,
            positive_signals=positive_signals,
            fit_reasons=tuple(fit_reasons),
            reject_reasons=("not buyer",),
        )

    if problem_fit == 0:
        return _ranked(
            comment=comment,
            rank_level=RankLevel.IRRELEVANT,
            problem_fit=problem_fit,
            buying_signal=buying_signal,
            buyer_fit=buyer_fit,
            actionability=actionability,
            immediacy=immediacy,
            direct_buyer=True,
            need_categories=need_categories,
            positive_signals=positive_signals,
            fit_reasons=tuple(fit_reasons),
            reject_reasons=("not relevant",),
        )

    if buying_signal == 0:
        level = RankLevel.IRRELEVANT
    elif total >= 11:
        level = RankLevel.STRONG
    elif total >= 7:
        level = RankLevel.POSSIBLE
    elif total >= 4:
        level = RankLevel.WEAK
    else:
        level = RankLevel.IRRELEVANT

    return _ranked(
        comment=comment,
        rank_level=level,
        problem_fit=problem_fit,
        buying_signal=buying_signal,
        buyer_fit=buyer_fit,
        actionability=actionability,
        immediacy=immediacy,
        direct_buyer=direct_buyer,
        need_categories=need_categories,
        positive_signals=positive_signals,
        fit_reasons=tuple(fit_reasons),
        reject_reasons=(),
    )


def _problem_fit(text: str, need_categories: tuple[str, ...]) -> int:
    if len(need_categories) >= 3:
        return 4
    if len(need_categories) == 2:
        return 3
    if len(need_categories) == 1:
        return 2
    return 1 if _contains_any(text, ("workflow", "tooling", "process", "system")) else 0


def _buying_signal(text: str, positive_signals: tuple[str, ...]) -> int:
    if {"explicit_help_ask", "need_statement"} <= set(positive_signals):
        return 4
    if len(positive_signals) >= 2:
        return 3
    if len(positive_signals) == 1:
        return 2
    return (
        1
        if _contains_any(
            text,
            (
                "we're blocked",
                "we are blocked",
                "we're struggling",
                "we are struggling",
                "our team is struggling",
                "too manual",
                "we're stuck",
                "we are stuck",
            ),
        )
        else 0
    )


def _buyer_fit(text: str, person_text: str) -> int:
    role_match = _contains_any(person_text, DIRECT_BUYER_HEADLINE_TERMS)
    company_present = bool(person_text.strip())
    buyer_context = _contains_any(text, BUYER_CONTEXT_TERMS)
    if role_match and company_present:
        return 3
    if role_match or buyer_context:
        return 2
    return 1 if _contains_any(text, ("my company", "our company", "our org")) else 0


def _actionability(text: str) -> int:
    if _contains_any(text, ACTION_TERMS):
        return 2
    return 1 if _contains_any(text, ("build", "automate", "integrate")) else 0


def _immediacy(text: str) -> int:
    for points, terms in IMMEDIACY_TERMS:
        if _contains_any(text, terms):
            return points
    return 0


def _ranked(
    *,
    comment: CommentEvidence,
    rank_level: RankLevel,
    problem_fit: int,
    buying_signal: int,
    buyer_fit: int,
    actionability: int,
    immediacy: int,
    direct_buyer: bool,
    need_categories: tuple[str, ...],
    positive_signals: tuple[str, ...],
    fit_reasons: tuple[str, ...],
    reject_reasons: tuple[str, ...],
) -> RankedComment:
    return RankedComment(
        comment=comment,
        rank_level=rank_level,
        rank_points=problem_fit + buying_signal + buyer_fit + actionability + immediacy,
        problem_fit=problem_fit,
        buying_signal=buying_signal,
        buyer_fit=buyer_fit,
        actionability=actionability,
        immediacy=immediacy,
        direct_buyer=direct_buyer,
        need_categories=need_categories,
        positive_signals=positive_signals,
        fit_reasons=fit_reasons,
        reject_reasons=reject_reasons,
        evidence_quote=comment.comment_text,
    )


def _matching_signal_names(
    text: str, grouped_terms: tuple[tuple[str, tuple[str, ...]], ...]
) -> tuple[str, ...]:
    return tuple(name for name, terms in grouped_terms if _contains_any(text, terms))


def _matching_buying_signal_names(text: str) -> tuple[str, ...]:
    signals = list(_matching_signal_names(text, BUYING_SIGNALS))
    if FIRST_PERSON_NEED_PATTERN.search(text):
        signals.append("need_statement")
    return tuple(dict.fromkeys(signals))


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(_contains_term(text, term) for term in terms)


def _contains_term(text: str, term: str) -> bool:
    normalized = term.strip()
    if not normalized:
        return False
    prefix = r"(?<![a-z0-9])" if normalized[0].isalnum() else ""
    suffix = r"(?![a-z0-9])" if normalized[-1].isalnum() else ""
    return re.search(f"{prefix}{re.escape(normalized)}{suffix}", text) is not None


def _same_person(commenter_name: str, post_author_name: str) -> bool:
    commenter = _name_key(commenter_name)
    author = _name_key(post_author_name)
    return bool(commenter and author and commenter == author)


def _name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())
