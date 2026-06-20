use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use chrono::{DateTime, Local, NaiveDate};
use clap::ValueEnum;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum DraftStrategy {
    AsapContractV1,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AcceptedDraftCandidate {
    pub run_id: Uuid,
    pub run_date: NaiveDate,
    pub source: String,
    pub name: String,
    pub profile_url: Option<String>,
    pub sent_at: DateTime<Local>,
    pub accepted_at: DateTime<Local>,
    pub relationship: Option<String>,
    pub acceptance_note: Option<String>,
    pub acceptance_evidence: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AcceptanceFollowupLedger {
    #[serde(default)]
    pub drafts: Vec<AcceptanceFollowupRecord>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AcceptanceFollowupRecord {
    pub key: String,
    pub source: String,
    pub name: String,
    pub profile_url: Option<String>,
    pub drafted_at: DateTime<Local>,
    pub accepted_at: DateTime<Local>,
    pub strategy: DraftStrategy,
    pub report_path: PathBuf,
    pub research_path: Option<PathBuf>,
}

impl AcceptanceFollowupLedger {
    pub fn has_draft_for(&self, candidate: &AcceptedDraftCandidate) -> bool {
        let key = candidate_key(
            &candidate.source,
            &candidate.name,
            candidate.profile_url.as_deref(),
        );
        self.drafts.iter().any(|record| record.key == key)
    }

    pub fn record_report(
        &mut self,
        report: &DraftReport,
        report_path: &Path,
        research_path: Option<&Path>,
    ) -> usize {
        let mut written = 0;
        for item in &report.items {
            let key = candidate_key(
                &item.candidate.source,
                &item.candidate.name,
                item.candidate.profile_url.as_deref(),
            );
            if self.drafts.iter().any(|record| record.key == key) {
                continue;
            }
            self.drafts.push(AcceptanceFollowupRecord {
                key,
                source: item.candidate.source.clone(),
                name: item.candidate.name.clone(),
                profile_url: item.candidate.profile_url.clone(),
                drafted_at: report.generated_at,
                accepted_at: item.candidate.accepted_at,
                strategy: report.strategy,
                report_path: report_path.to_path_buf(),
                research_path: research_path.map(Path::to_path_buf),
            });
            written += 1;
        }
        written
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct AcceptedResearchArtifact {
    #[serde(default, rename = "capturedAt")]
    pub captured_at: Option<String>,
    #[serde(default)]
    pub rows: Vec<AcceptedResearchRow>,
}

impl AcceptedResearchArtifact {
    pub fn from_path(path: &Path) -> Result<Self> {
        let raw = fs::read_to_string(path)
            .with_context(|| format!("reading accepted research {}", path.display()))?;
        serde_json::from_str(&raw)
            .with_context(|| format!("parsing accepted research {}", path.display()))
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct AcceptedResearchRow {
    pub source: String,
    pub name: String,
    #[serde(default, alias = "profileUrl")]
    pub profile_url: Option<String>,
    #[serde(default, rename = "salesNav")]
    pub sales_nav: Option<SalesNavResearch>,
    #[serde(default)]
    pub web: Option<WebResearch>,
    #[serde(default)]
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct SalesNavResearch {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub company: Option<String>,
    #[serde(default)]
    pub location: Option<String>,
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default)]
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct WebResearch {
    #[serde(default)]
    pub query: Option<String>,
    #[serde(default)]
    pub results: Vec<WebResult>,
    #[serde(default)]
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct WebResult {
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default)]
    pub snippet: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct DraftReport {
    pub generated_at: DateTime<Local>,
    pub strategy: DraftStrategy,
    pub research_path: Option<PathBuf>,
    pub research_captured_at: Option<String>,
    pub items: Vec<DraftItem>,
    pub skipped_names: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct DraftItem {
    pub candidate: AcceptedDraftCandidate,
    pub angle: String,
    pub draft: String,
    pub evidence: Vec<String>,
    pub warnings: Vec<String>,
}

pub fn candidate_key(source: &str, name: &str, profile_url: Option<&str>) -> String {
    let url = profile_url.map(normalize_linkedin_url).unwrap_or_default();
    format!("{}|{}|{}", source.trim(), name.trim(), url)
}

pub fn build_draft_report(
    candidates: Vec<AcceptedDraftCandidate>,
    artifact: Option<AcceptedResearchArtifact>,
    strategy: DraftStrategy,
    research_path: Option<PathBuf>,
) -> DraftReport {
    let research_captured_at = artifact
        .as_ref()
        .and_then(|artifact| artifact.captured_at.clone());
    let research_by_key = artifact
        .map(|artifact| {
            artifact
                .rows
                .into_iter()
                .map(|row| {
                    let key = candidate_key(&row.source, &row.name, row.profile_url.as_deref());
                    (key, row)
                })
                .collect::<BTreeMap<_, _>>()
        })
        .unwrap_or_default();

    let mut seen = BTreeSet::new();
    let mut items = Vec::new();
    let mut skipped_names = Vec::new();

    for candidate in candidates {
        let key = candidate_key(
            &candidate.source,
            &candidate.name,
            candidate.profile_url.as_deref(),
        );
        if !seen.insert(key.clone()) {
            skipped_names.push(candidate.name);
            continue;
        }
        let research = research_by_key.get(&key);
        items.push(build_draft_item(candidate, research, strategy));
    }

    DraftReport {
        generated_at: Local::now(),
        strategy,
        research_path,
        research_captured_at,
        items,
        skipped_names,
    }
}

pub fn render_markdown(report: &DraftReport) -> String {
    let mut lines = Vec::new();
    lines.push(format!(
        "# LinkedIn Accepted Follow-Up Drafts {}",
        report.generated_at.date_naive()
    ));
    lines.push(String::new());
    lines.push(format!(
        "- Generated: `{}`",
        report.generated_at.to_rfc3339()
    ));
    lines.push(format!("- Strategy: `{:?}`", report.strategy));
    lines.push(format!("- Draft count: {}", report.items.len()));
    if let Some(path) = &report.research_path {
        lines.push(format!("- Research artifact: `{}`", path.display()));
    }
    if let Some(captured_at) = &report.research_captured_at {
        lines.push(format!(
            "- Research captured: `{}`",
            clean_inline(captured_at)
        ));
    }
    if !report.skipped_names.is_empty() {
        lines.push(format!(
            "- Duplicate candidates skipped: {}",
            report.skipped_names.join(", ")
        ));
    }

    if report.items.is_empty() {
        lines.push(String::new());
        lines.push("No newly accepted connections need first-message drafts.".to_string());
        return lines.join("\n");
    }

    for item in &report.items {
        lines.push(String::new());
        lines.push(format!("## {}", clean_inline(&item.candidate.name)));
        lines.push(format!(
            "- Source: {}",
            clean_inline(&item.candidate.source)
        ));
        if let Some(url) = &item.candidate.profile_url {
            lines.push(format!("- Profile: {}", clean_inline(url)));
        }
        lines.push(format!(
            "- Accepted at: `{}`",
            item.candidate.accepted_at.to_rfc3339()
        ));
        lines.push(format!("- Best angle: {}", clean_inline(&item.angle)));
        if !item.evidence.is_empty() {
            lines.push("- Evidence used:".to_string());
            for evidence in &item.evidence {
                lines.push(format!("  - {}", clean_inline(evidence)));
            }
        }
        if !item.warnings.is_empty() {
            lines.push("- Warnings:".to_string());
            for warning in &item.warnings {
                lines.push(format!("  - {}", clean_inline(warning)));
            }
        }
        lines.push(String::new());
        lines.push("Draft:".to_string());
        lines.push(String::new());
        lines.push(format!("> {}", clean_inline(&item.draft)));
    }

    lines.join("\n")
}

fn build_draft_item(
    candidate: AcceptedDraftCandidate,
    research: Option<&AcceptedResearchRow>,
    strategy: DraftStrategy,
) -> DraftItem {
    match strategy {
        DraftStrategy::AsapContractV1 => build_asap_contract_draft(candidate, research),
    }
}

fn build_asap_contract_draft(
    candidate: AcceptedDraftCandidate,
    research: Option<&AcceptedResearchRow>,
) -> DraftItem {
    let sales_nav = research.and_then(|row| row.sales_nav.as_ref());
    let title = sales_nav
        .and_then(|row| row.title.as_deref())
        .filter(|value| !value.is_empty());
    let company = sales_nav
        .and_then(|row| row.company.as_deref())
        .filter(|value| !value.is_empty());
    let web_result = research
        .and_then(|row| row.web.as_ref())
        .and_then(|web| web.results.first());
    let first_name = first_name(&candidate.name);
    let angle = choose_angle(&candidate.source, title, company, web_result);
    let draft = match angle.kind {
        DraftAngleKind::Recruiter => format!(
            "Thanks for connecting, {first_name}. I am actively looking for contract or freelance work: US citizen, operating through HC Studio LLC, based in Buenos Aires and working EST/CST hours. Best fit is senior product engineering, AI workflow automation, and fast MVP/prototype work. If you handle contract roles where that maps, I can send a concise proof sheet."
        ),
        DraftAngleKind::Agency => format!(
            "Thanks for connecting, {first_name}. I am opening up contract/freelance capacity through HC Studio LLC. If your team needs senior product-engineering help on AI workflow automation, MVPs, prototypes, or client delivery overflow, I can plug in quickly and work US hours from Buenos Aires."
        ),
        DraftAngleKind::TechnicalLeader => format!(
            "Thanks for connecting, {first_name}. I am taking on contract/freelance work through HC Studio LLC: senior product engineering, AI workflow automation, and fast prototype-to-production work. Based on your work{}{}, the useful angle is probably helping ship a concrete workflow or product slice without adding a full-time hire.",
            company
                .map(|value| format!(" at {value}"))
                .unwrap_or_default(),
            title.map(|value| format!(" ({value})")).unwrap_or_default()
        ),
        DraftAngleKind::ProofMatched => format!(
            "Thanks for connecting, {first_name}. I am taking on contract/freelance work through HC Studio LLC and thought the fit may be around proof-matched product work: marketplaces, ecommerce workflows, events/discovery, language-learning, or AI-assisted operations. If there is a concrete workflow or product slice you want moved faster, I can help on a contractor basis."
        ),
        DraftAngleKind::GeneralFounder => format!(
            "Thanks for connecting, {first_name}. I am actively taking on contract/freelance work through HC Studio LLC. I am strongest where product engineering, AI workflow automation, and fast prototyping meet. If you have a concrete workflow, MVP, or internal tool you want shipped quickly without a full-time hire, I would be glad to compare notes."
        ),
    };

    let mut evidence = Vec::new();
    if let Some(title) = title {
        evidence.push(format!("Sales Nav title/headline: {title}"));
    }
    if let Some(company) = company {
        evidence.push(format!("Sales Nav company: {company}"));
    }
    if let Some(sales_nav) = sales_nav {
        if let Some(name) = sales_nav.name.as_deref().filter(|value| !value.is_empty()) {
            evidence.push(format!("Sales Nav displayed name: {name}"));
        }
        if let Some(location) = sales_nav
            .location
            .as_deref()
            .filter(|value| !value.is_empty())
        {
            evidence.push(format!("Sales Nav location: {location}"));
        }
        if let Some(url) = sales_nav.url.as_deref().filter(|value| !value.is_empty()) {
            evidence.push(format!("Sales Nav URL after load: {url}"));
        }
    }
    if let Some(relationship) = &candidate.relationship {
        evidence.push(format!("Sales Nav relationship: {relationship}"));
    }
    if let Some(note) = &candidate.acceptance_note {
        evidence.push(format!("Acceptance check: {note}"));
    }
    if let Some(result) = web_result {
        if let Some(title) = &result.title {
            evidence.push(format!("Public web result: {title}"));
        }
        if let Some(url) = &result.url {
            evidence.push(format!("Public web URL: {url}"));
        }
        if let Some(snippet) = &result.snippet {
            evidence.push(format!("Public web snippet: {snippet}"));
        }
    }
    if let Some(web) = research.and_then(|row| row.web.as_ref()) {
        if let Some(query) = web.query.as_deref().filter(|value| !value.is_empty()) {
            evidence.push(format!("Public web query: {query}"));
        }
    }

    let mut warnings = Vec::new();
    if research.is_none() {
        warnings.push("No research row matched this accepted candidate; draft uses source and ledger evidence only.".to_string());
    }
    if let Some(row) = research {
        warnings.extend(row.warnings.clone());
        if let Some(sales_nav) = &row.sales_nav {
            warnings.extend(sales_nav.warnings.clone());
        }
        if let Some(web) = &row.web {
            warnings.extend(web.warnings.clone());
        }
    }
    if title.is_none() && company.is_none() {
        warnings
            .push("Sales Nav title/company were not extracted; review before sending.".to_string());
    }

    DraftItem {
        candidate,
        angle: angle.label,
        draft,
        evidence,
        warnings,
    }
}

#[derive(Debug, Clone, Copy)]
enum DraftAngleKind {
    Recruiter,
    Agency,
    TechnicalLeader,
    ProofMatched,
    GeneralFounder,
}

#[derive(Debug, Clone)]
struct DraftAngle {
    kind: DraftAngleKind,
    label: String,
}

fn choose_angle(
    source: &str,
    title: Option<&str>,
    company: Option<&str>,
    web_result: Option<&WebResult>,
) -> DraftAngle {
    let source_lower = source.to_ascii_lowercase();
    let title_lower = title.unwrap_or_default().to_ascii_lowercase();
    let company_suffix = company
        .map(|value| format!(" for {}", clean_inline(value)))
        .unwrap_or_default();
    let web_suffix = web_result
        .and_then(|result| result.title.as_deref())
        .map(|value| format!("; public result: {}", clean_inline(value)))
        .unwrap_or_default();

    if source_lower.contains("recruiter") || source_lower.contains("staffing") {
        return DraftAngle {
            kind: DraftAngleKind::Recruiter,
            label: format!("contract-role availability ask{company_suffix}{web_suffix}"),
        };
    }
    if source_lower.contains("agency") || source_lower.contains("delivery") {
        return DraftAngle {
            kind: DraftAngleKind::Agency,
            label: format!(
                "agency overflow or specialist contractor capacity{company_suffix}{web_suffix}"
            ),
        };
    }
    if source_lower.contains("cto")
        || source_lower.contains("engineering")
        || title_lower.contains("cto")
        || title_lower.contains("engineering")
    {
        return DraftAngle {
            kind: DraftAngleKind::TechnicalLeader,
            label: format!(
                "senior product-engineering contractor help{company_suffix}{web_suffix}"
            ),
        };
    }
    if source_lower.contains("vertical") || source_lower.contains("proof") {
        return DraftAngle {
            kind: DraftAngleKind::ProofMatched,
            label: format!("proof-matched product/workflow help{company_suffix}{web_suffix}"),
        };
    }
    DraftAngle {
        kind: DraftAngleKind::GeneralFounder,
        label: format!("fast contract product-engineering help{company_suffix}{web_suffix}"),
    }
}

fn first_name(name: &str) -> String {
    name.split_whitespace()
        .next()
        .filter(|value| !value.is_empty())
        .unwrap_or("there")
        .to_string()
}

fn clean_inline(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn normalize_linkedin_url(url: &str) -> String {
    let trimmed = url.trim();
    trimmed
        .split(['?', '#'])
        .next()
        .unwrap_or(trimmed)
        .trim_end_matches('/')
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn candidate(source: &str) -> AcceptedDraftCandidate {
        AcceptedDraftCandidate {
            run_id: Uuid::new_v4(),
            run_date: NaiveDate::from_ymd_opt(2026, 6, 20).unwrap(),
            source: source.to_string(),
            name: "Jamie Rivera".to_string(),
            profile_url: Some("https://www.linkedin.com/sales/lead/abc?_ntb=x".to_string()),
            sent_at: Local::now(),
            accepted_at: Local::now(),
            relationship: Some("1st".to_string()),
            acceptance_note: Some("lead page shows 1st-degree relationship".to_string()),
            acceptance_evidence: None,
        }
    }

    #[test]
    fn followup_ledger_dedupes_by_normalized_linkedin_url() {
        let candidate = candidate("ASAP - Contract Recruiters Staffing");
        let mut ledger = AcceptanceFollowupLedger::default();
        let report = build_draft_report(
            vec![candidate.clone()],
            None,
            DraftStrategy::AsapContractV1,
            None,
        );

        let inserted = ledger.record_report(&report, Path::new("/tmp/report.md"), None);

        assert_eq!(inserted, 1);
        assert!(ledger.has_draft_for(&candidate));
    }

    #[test]
    fn recruiter_source_gets_contract_availability_message() {
        let report = build_draft_report(
            vec![candidate("ASAP - Contract Recruiters Staffing")],
            None,
            DraftStrategy::AsapContractV1,
            None,
        );

        assert!(report.items[0].draft.contains("contract roles"));
        assert!(report.items[0].draft.contains("HC Studio LLC"));
    }

    #[test]
    fn research_title_and_company_are_used_as_evidence() {
        let artifact = AcceptedResearchArtifact {
            captured_at: Some("2026-06-20T00:00:00Z".to_string()),
            rows: vec![AcceptedResearchRow {
                source: "ASAP - Startup CTO Eng Leaders".to_string(),
                name: "Jamie Rivera".to_string(),
                profile_url: Some("https://www.linkedin.com/sales/lead/abc".to_string()),
                sales_nav: Some(SalesNavResearch {
                    title: Some("CTO".to_string()),
                    company: Some("Acme AI".to_string()),
                    ..Default::default()
                }),
                ..Default::default()
            }],
        };

        let report = build_draft_report(
            vec![candidate("ASAP - Startup CTO Eng Leaders")],
            Some(artifact),
            DraftStrategy::AsapContractV1,
            None,
        );

        assert!(report.items[0].angle.contains("Acme AI"));
        assert!(
            report.items[0]
                .evidence
                .iter()
                .any(|item| item.contains("Sales Nav company: Acme AI"))
        );
    }
}
