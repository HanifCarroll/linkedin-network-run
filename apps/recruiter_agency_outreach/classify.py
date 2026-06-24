"""Deterministic recruiter/agency classification parity helpers."""

from __future__ import annotations

from .models import (
    AgencyAccount,
    AgencyAccountStatus,
    Lead,
    LeadStatus,
    LeadType,
)
from .utils import (
    clean_text,
    contains_any,
    dedupe_strings,
    domain_from_website,
    first_name,
    normalized_linkedin_account_url,
    optional_clean,
    stable_agency_account_id,
    stable_lead_id,
    truncate_evidence,
)

DEFAULT_ELIGIBLE_SCORE = 70
DEFAULT_REVIEW_SCORE = 50


def classify_lead(
    source: str,
    title: str | None,
    company: str | None,
    evidence: str,
    agency_account: AgencyAccount | None = None,
) -> tuple[LeadType, int, list[str], list[str]]:
    title_text = clean_text(title).lower()
    company_text = clean_text(company).lower()
    evidence_text = evidence.lower()
    profile_text = " ".join([title_text, company_text, evidence_text])
    score = 0
    reasons: list[str] = []
    rejects: list[str] = []

    title_recruiter = contains_any(
        title_text,
        "recruiter",
        "talent acquisition",
        "talent partner",
        "sourcer",
        "account manager",
        "staffing",
    )
    title_resource = contains_any(
        title_text,
        "resource manager",
        "resourcing",
        "talent manager",
        "head of talent",
    )
    title_delivery = contains_any(
        title_text,
        "delivery",
        "technical director",
        "engineering director",
        "head of engineering",
        "vp engineering",
        "head of delivery",
        "client services",
        "partnerships",
    )
    title_founder = contains_any(
        title_text,
        "founder",
        "partner",
        "principal",
        "owner",
        "ceo",
        "president",
        "managing director",
    )
    account_agency = agency_account is not None and (
        agency_account.status == AgencyAccountStatus.QUALIFIED
    )
    company_agency = account_agency or contains_any(
        company_text,
        "product studio",
        "digital product",
        "digital agency",
        "software agency",
        "development agency",
        "design agency",
        "dev shop",
        "studio",
        "consultancy",
        "consulting",
        "agency",
    )
    company_staffing = contains_any(
        company_text,
        "staffing",
        "recruiting",
        "recruitment",
        "talent solutions",
        "consulting firm",
    )
    contract_signal = contains_any(
        profile_text,
        "contract",
        "c2c",
        "1099",
        "consultant",
        "fractional",
        "freelance",
        "temporary",
        "staff augmentation",
    )
    software_signal = contains_any(
        profile_text,
        "react",
        "typescript",
        "node",
        "frontend",
        "front-end",
        "full-stack",
        "full stack",
        "product engineer",
        "software engineer",
        "ai",
        "genai",
        "saas",
    )

    lead_type = LeadType.BAD_FIT
    if title_recruiter or company_staffing:
        lead_type = LeadType.CONTRACT_RECRUITER
        score += 40
        reasons.append("recruiter/staffing signal")
    elif company_agency and (title_resource or title_delivery or title_founder):
        if title_resource:
            lead_type = LeadType.AGENCY_RESOURCE
            reasons.append("agency resource/resourcing title")
        elif title_delivery:
            lead_type = LeadType.AGENCY_DELIVERY
            reasons.append("agency delivery/technical leadership title")
        else:
            lead_type = LeadType.AGENCY_FOUNDER
            reasons.append("agency founder/partner title")
        score += 40
    else:
        rejects.append("not a recruiter or agency/resource target")

    if title_recruiter or title_resource or title_delivery or title_founder:
        score += 25
        reasons.append("title matches target persona")
    if contract_signal:
        score += 15
        reasons.append("contract/fractional signal")
    if software_signal:
        score += 12
        reasons.append("software/product/AI signal")
    if company_agency or company_staffing:
        score += 10
        reasons.append("company/source matches target market")
    if account_agency:
        score += 10
        reasons.append("qualified agency account context")
    if contains_any(
        profile_text,
        "onsite only",
        "clearance",
        "secret clearance",
        "top secret",
        "w2 only",
        "local candidates only",
    ):
        score -= 35
        rejects.append("likely blocked by onsite, clearance, or W2-only requirement")
    if contains_any(profile_text, "europe", "uk only", "india only", "latam only", "canada only"):
        if not contains_any(profile_text, "us", "united states", "remote"):
            score -= 20
            rejects.append("market/location signal may not match US contract work")

    score = max(0, min(100, score))
    if not reasons:
        reasons.append("weak target evidence")
    if lead_type == LeadType.BAD_FIT and not rejects:
        rejects.append("failed target-persona classification")
    return lead_type, score, dedupe_strings(reasons), dedupe_strings(rejects)


def classify_agency_account(
    source: str,
    name: str,
    industry: str | None,
    evidence: str,
) -> tuple[AgencyAccountStatus, int, list[str], list[str]]:
    account_text = " ".join([name, clean_text(industry), evidence]).lower()
    source_text = source.lower()
    score = 0
    reasons: list[str] = []
    rejects: list[str] = []

    if contains_any(
        account_text,
        "product studio",
        "digital product",
        "software development",
        "custom software",
        "web development",
        "mobile app",
        "application development",
        "product design",
        "ux design",
        "design services",
        "it services and it consulting",
    ):
        score += 45
        reasons.append("software/product delivery account signal")
    if contains_any(
        account_text,
        "agency",
        "studio",
        "consultancy",
        "consulting",
        "development shop",
        "dev shop",
    ):
        score += 20
        reasons.append("agency/studio services signal")
    if contains_any(
        source_text,
        "product studio",
        "development agency",
        "digital agency",
        "software development",
    ):
        score += 15
        reasons.append("matched generated agency account source")
    if contains_any(account_text, "react", "typescript", "node", "ai", "saas", "platform", "mvp"):
        score += 10
        reasons.append("technical/product stack signal")

    website_build = contains_any(
        account_text,
        "wordpress",
        "shopify",
        "webflow",
        "cms",
        "web design",
        "web designer",
        "web developer",
        "website design",
        "website development",
        "high-performing websites",
    )
    if website_build:
        score += 35
        reasons.append("website/wordpress build account signal")

    marketing_only = (
        contains_any(
            account_text,
            "seo",
            "paid media",
            "media buying",
            "advertising",
            "social media marketing",
            "performance marketing",
            "lead generation",
            "public relations",
            "branding agency",
        )
        and not website_build
        and not contains_any(
            account_text,
            "software",
            "product",
            "web development",
            "application",
            "mobile app",
            "ux",
            "ui",
        )
    )
    if marketing_only:
        score -= 40
        rejects.append("marketing/advertising-only account signal")
    if contains_any(account_text, "staffing", "recruiting", "recruitment", "talent solutions"):
        score -= 20
        rejects.append("staffing account belongs in recruiter lane")

    score = max(0, min(100, score))
    if not reasons:
        reasons.append("weak agency account evidence")
    status = AgencyAccountStatus.NEEDS_REVIEW
    if score >= 65:
        status = AgencyAccountStatus.QUALIFIED
    elif score < 45:
        status = AgencyAccountStatus.REJECTED
    return status, score, dedupe_strings(reasons), dedupe_strings(rejects)


def account_from_capture_row(
    source: str,
    row: dict[str, object],
    captured_at: str | None,
    imported_at: str,
) -> AgencyAccount | None:
    name = clean_text(row.get("name") or row.get("Name"))
    if not name:
        return None
    account_url = normalized_linkedin_account_url(row.get("accountUrl") or row.get("account_url"))
    website = optional_clean(row.get("website"))
    domain = domain_from_website(website)
    evidence = truncate_evidence(account_evidence(row))
    industry = optional_clean(row.get("industry"))
    status, score, reasons, rejects = classify_agency_account(source, name, industry, evidence)
    return AgencyAccount(
        id=stable_agency_account_id(source, name, account_url, domain),
        source=source,
        name=name,
        account_url=account_url,
        website=website,
        domain=domain,
        industry=industry,
        headcount=optional_clean(row.get("headcount")),
        location=optional_clean(row.get("location")),
        status=status,
        fit_score=score,
        fit_reasons=reasons,
        reject_reasons=rejects,
        evidence_text=evidence,
        captured_at=captured_at,
        imported_at=imported_at,
        updated_at=imported_at,
    )


def account_evidence(row: dict[str, object]) -> str:
    parts: list[str] = []
    for key in ("text", "industry", "headcount", "location", "website"):
        value = clean_text(row.get(key))
        if value:
            parts.append(value)
    links = row.get("links")
    if isinstance(links, list):
        for link in links:
            if not isinstance(link, dict):
                continue
            for key in ("text", "aria"):
                value = clean_text(link.get(key))
                if value:
                    parts.append(value)
            href = clean_text(link.get("href"))
            if href and "linkedin.com" not in href:
                parts.append(href)
    return "\n".join(parts)


def lead_from_capture_row(
    source: str,
    row: dict[str, object],
    captured_at: str | None,
    imported_at: str,
    agency_account: AgencyAccount | None = None,
) -> Lead | None:
    name = clean_text(row.get("name") or row.get("Name"))
    if not name:
        return None
    profile_url = optional_clean(row.get("profileUrl") or row.get("profile_url"))
    sales_profile_urn = optional_clean(row.get("scrollUrn") or row.get("sales_profile_urn"))
    source_evidence = raw_lead_evidence(row)
    evidence = truncate_evidence(source_evidence)
    company = company_from_links(row)
    if agency_account is not None and not company:
        company = agency_account.name
    title = extract_title(name, source_evidence, company)
    lead_type, score, reasons, rejects = classify_lead(
        source,
        title,
        company,
        evidence,
        agency_account,
    )
    status = LeadStatus.ELIGIBLE
    if lead_type == LeadType.BAD_FIT or score < DEFAULT_REVIEW_SCORE:
        status = LeadStatus.REJECTED
    elif score < DEFAULT_ELIGIBLE_SCORE:
        status = LeadStatus.NEEDS_REVIEW
    lead = Lead(
        id=stable_lead_id(source, name, profile_url, sales_profile_urn),
        source=source,
        name=name,
        first_name=first_name(name),
        profile_url=profile_url,
        sales_profile_urn=sales_profile_urn,
        title=title,
        company=company,
        lead_type=lead_type,
        status=status,
        fit_score=score,
        fit_reasons=reasons,
        reject_reasons=rejects,
        evidence_text=evidence,
        menu_state=clean_text(row.get("menuState") or row.get("menu_state") or "unknown"),
        captured_at=captured_at,
        imported_at=imported_at,
        updated_at=imported_at,
    )
    if agency_account is not None:
        link_lead_to_agency_account(lead, agency_account)
    return lead


def link_lead_to_agency_account(lead: Lead, account: AgencyAccount) -> None:
    lead.agency_account_id = account.id
    lead.agency_account_name = account.name
    lead.agency_account_url = account.account_url
    lead.agency_account_reasons = list(account.fit_reasons)
    lead.agency_account_evidence = account.evidence_text
    if not lead.company:
        lead.company = account.name


def raw_lead_evidence(row: dict[str, object]) -> str:
    parts: list[str] = []
    value = str(row.get("text") or "").replace("\r\n", "\n").strip()
    if value:
        parts.append(value)
    links = row.get("links")
    if isinstance(links, list):
        for link in links:
            if not isinstance(link, dict):
                continue
            for key in ("text", "aria"):
                item = clean_text(link.get(key))
                if item:
                    parts.append(item)
    menu_labels = row.get("menuLabels")
    if isinstance(menu_labels, list):
        parts.extend(clean_text(item) for item in menu_labels if clean_text(item))
    return "\n".join(parts)


def company_from_links(row: dict[str, object]) -> str | None:
    links = row.get("links")
    if not isinstance(links, list):
        return None
    for link in links:
        if not isinstance(link, dict):
            continue
        href = clean_text(link.get("href"))
        text = clean_text(link.get("text"))
        if "/sales/company/" in href and text:
            return text
    return None


def extract_title(name: str, evidence: str, company: str | None) -> str | None:
    clean_name = clean_text(name).lower()
    company_text = clean_text(company).lower()
    for line in evidence.splitlines():
        cleaned = clean_text(line)
        lower = cleaned.lower()
        if lower in {"about:", "experience:"}:
            break
        if (
            not cleaned
            or lower == clean_name
            or lower == company_text
            or lower in {"2nd", "3rd+", "viewed", "saved"}
            or lower.startswith("add ")
            or lower.startswith("·")
        ):
            continue
        if contains_any(
            lower,
            "connect",
            "message",
            "save",
            "more actions",
            "selection",
            "degree connection",
            "linkedin premium",
            "last active",
        ):
            continue
        if contains_any(
            lower,
            "recruiter",
            "talent",
            "resource",
            "delivery",
            "technical director",
            "engineering",
            "founder",
            "partner",
            "principal",
            "owner",
            "ceo",
            "account manager",
            "sourcer",
        ):
            if company:
                without_company = cleaned.replace(f" {company}", "").strip()
                return without_company or cleaned
            return cleaned
    return None
