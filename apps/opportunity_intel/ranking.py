"""Recommend-only buyer-signal ranking."""

from __future__ import annotations

from apps.opportunity_intel.contracts import (
    CommentEvidence,
    QueryDefinition,
    RankedComment,
    RankLevel,
)

HIGH_PRIORITY_NEEDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("internal_tools", ("internal tool", "admin tool", "back office", "ops tool")),
    ("ai_workflow_automation", ("ai workflow", "agent workflow", "automate this", "automation")),
    ("product_engineering", ("product engineering", "prototype", "mvp", "ship this", "build this")),
    ("data_dashboards", ("dashboard", "reporting", "metrics", "kpi")),
    ("spreadsheet_operations", ("spreadsheet", "google sheet", "excel", "manual tracker")),
)

POSITIVE_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "explicit_help_ask",
        ("who can help", "recommend someone", "looking for someone", "need help"),
    ),
    ("first_person_workflow_pain", ("we are stuck", "we still do this manually", "our process")),
    ("automation_backlog", ("need to automate", "automation backlog", "too manual")),
    ("timing_or_budget", ("this quarter", "budget", "approved", "ready to start")),
    ("active_evaluation", ("evaluating", "comparing tools", "migration", "replace")),
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
)

NOISE_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("recruiting_staffing", ("recruiter", "staffing", "talent acquisition", "sourcer")),
    ("job_seeker", ("open to work", "looking for a job", "my resume", "student", "internship")),
    ("vendor_promo", ("i help companies", "book a call with me", "my agency", "my services")),
)

DIRECT_BUYER_ASK_TERMS: tuple[str, ...] = (
    "we need",
    "our team needs",
    "looking for someone",
    "who can build",
    "who can help",
    "recommend someone",
)


def rank_comment(comment: CommentEvidence, query: QueryDefinition | None = None) -> RankedComment:
    comment_text = comment.comment_text.casefold()
    headline = comment.commenter_headline.casefold()
    company = comment.commenter_company.casefold()
    combined_person_text = f"{headline} {company}"

    positive_signals = _matching_signal_names(comment_text, POSITIVE_SIGNALS)
    need_categories = _matching_signal_names(comment_text, HIGH_PRIORITY_NEEDS)
    if query is not None:
        need_categories = tuple(dict.fromkeys((*need_categories, *query.need_categories)))

    direct_buyer = (
        _contains_any(combined_person_text, DIRECT_BUYER_HEADLINE_TERMS)
        or _contains_any(comment_text, DIRECT_BUYER_ASK_TERMS)
    )
    reject_reasons = _matching_signal_names(f"{comment_text} {combined_person_text}", NOISE_TERMS)
    if reject_reasons and not _contains_any(comment_text, DIRECT_BUYER_ASK_TERMS):
        return RankedComment(
            comment=comment,
            rank_level=RankLevel.REJECT,
            rank_points=0,
            direct_buyer=False,
            need_categories=need_categories,
            positive_signals=positive_signals,
            fit_reasons=(),
            reject_reasons=reject_reasons,
            evidence_quote=comment.comment_text,
        )

    rank_points = 0
    fit_reasons: list[str] = []
    if direct_buyer:
        rank_points += 3
        fit_reasons.append("direct_buyer")
    if need_categories:
        rank_points += 2 + min(len(need_categories), 3)
        fit_reasons.append("priority_need")
    if positive_signals:
        rank_points += 2 + min(len(positive_signals), 3)
        fit_reasons.append("buying_signal")
    if comment.commenter_company:
        rank_points += 1
        fit_reasons.append("company_present")

    if not direct_buyer:
        return RankedComment(
            comment=comment,
            rank_level=RankLevel.REJECT,
            rank_points=rank_points,
            direct_buyer=False,
            need_categories=need_categories,
            positive_signals=positive_signals,
            fit_reasons=tuple(fit_reasons),
            reject_reasons=("not_direct_buyer",),
            evidence_quote=comment.comment_text,
        )

    if rank_points >= 9:
        rank_level = RankLevel.HOT
    elif rank_points >= 6:
        rank_level = RankLevel.WARM
    elif rank_points >= 4:
        rank_level = RankLevel.WATCH
    else:
        rank_level = RankLevel.REJECT

    return RankedComment(
        comment=comment,
        rank_level=rank_level,
        rank_points=rank_points,
        direct_buyer=direct_buyer,
        need_categories=need_categories,
        positive_signals=positive_signals,
        fit_reasons=tuple(fit_reasons),
        reject_reasons=reject_reasons,
        evidence_quote=comment.comment_text,
    )


def _matching_signal_names(
    text: str, grouped_terms: tuple[tuple[str, tuple[str, ...]], ...]
) -> tuple[str, ...]:
    return tuple(name for name, terms in grouped_terms if _contains_any(text, terms))


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
