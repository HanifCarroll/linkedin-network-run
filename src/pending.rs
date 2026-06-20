use crate::*;

pub(crate) fn apply_pending_audit(
    run: &mut PendingCleanupRun,
    people_count: u32,
    note: Option<String>,
) {
    let audit = AuditEvent {
        at: Local::now(),
        people_count,
        note,
    };
    if run.start_audit.is_none() {
        run.start_audit = Some(people_count);
        run.state = PendingCleanupState::Audited;
    } else if matches!(run.state, PendingCleanupState::NeedsReaudit) {
        run.state = PendingCleanupState::Withdrawing;
    }
    run.latest_audit = Some(people_count);
    run.audits.push(audit);
    run.mark_updated();
}

#[derive(Debug, Deserialize)]
pub(crate) struct PendingCapture {
    #[serde(rename = "capturedAt")]
    pub(crate) captured_at: Option<String>,
    pub(crate) rows: Vec<PendingCaptureRow>,
}

impl PendingCapture {
    pub(crate) fn from_path(path: &PathBuf) -> Result<Self> {
        let raw = fs::read_to_string(path)
            .with_context(|| format!("reading pending capture {}", path.display()))?;
        serde_json::from_str(&raw)
            .with_context(|| format!("parsing pending capture {}", path.display()))
    }
}

#[derive(Debug, Deserialize)]
pub(crate) struct PendingCaptureRow {
    pub(crate) index: u32,
    pub(crate) name: Option<String>,
    #[serde(rename = "profileUrl")]
    pub(crate) profile_url: Option<String>,
    #[serde(rename = "ageText")]
    pub(crate) age_text: Option<String>,
    #[serde(rename = "ageMonths")]
    pub(crate) age_months: Option<u32>,
    pub(crate) eligible: Option<bool>,
    #[serde(rename = "rowText")]
    pub(crate) row_text: Option<String>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct PendingWithdrawResult {
    pub(crate) candidate: PendingWithdrawCandidate,
    pub(crate) status: String,
    #[serde(default)]
    pub(crate) detail: Option<serde_json::Value>,
}

impl PendingWithdrawResult {
    pub(crate) fn from_path(path: &PathBuf) -> Result<Self> {
        let raw = fs::read_to_string(path)
            .with_context(|| format!("reading withdraw result {}", path.display()))?;
        serde_json::from_str(&raw)
            .with_context(|| format!("parsing withdraw result {}", path.display()))
    }

    pub(crate) fn to_withdraw_status(&self) -> (PendingWithdrawStatus, String) {
        match self.status.as_str() {
            "withdrawn-verified" => (
                PendingWithdrawStatus::Withdrawn,
                "salesnav-pending-withdraw-one verified row removed or count decreased".to_string(),
            ),
            "dry-run-withdrawable" => (
                PendingWithdrawStatus::Skipped,
                "dry run found eligible stale invitation".to_string(),
            ),
            "not-eligible" | "row-not-found" => (
                PendingWithdrawStatus::Skipped,
                format!("salesnav-pending-withdraw-one status {}", self.status),
            ),
            other => (
                PendingWithdrawStatus::Failed,
                format!(
                    "salesnav-pending-withdraw-one status {other}; {}",
                    self.detail
                        .as_ref()
                        .map(|value| value.to_string())
                        .unwrap_or_else(|| "no detail".to_string())
                ),
            ),
        }
    }
}

#[derive(Debug, Deserialize)]
pub(crate) struct PendingWithdrawCandidate {
    pub(crate) name: String,
    #[serde(alias = "profileUrl")]
    pub(crate) profile_url: Option<String>,
    #[serde(rename = "age_text", alias = "ageText")]
    pub(crate) age_text: String,
}

pub(crate) fn import_pending_capture(
    run: &mut PendingCleanupRun,
    capture: PendingCapture,
) -> Result<usize> {
    let mut imported = 0;
    for row in capture.rows {
        let Some(name) = row.name.filter(|name| !name.trim().is_empty()) else {
            continue;
        };
        let age_text = row.age_text.unwrap_or_default();
        let age_months = row.age_months.or_else(|| parse_sent_age_months(&age_text));
        let eligible = row
            .eligible
            .unwrap_or_else(|| age_months.is_some_and(|months| months >= run.threshold_months));
        let observation = PendingCandidateObservation {
            imported_at: Local::now(),
            captured_at: capture.captured_at.clone(),
            index: row.index,
            name,
            profile_url: row.profile_url,
            age_text,
            age_months,
            eligible,
            row_text: row.row_text.unwrap_or_default(),
        };
        let existing_index = run.observations.iter().position(|existing| {
            if let (Some(existing_url), Some(new_url)) =
                (&existing.profile_url, &observation.profile_url)
            {
                existing_url == new_url
            } else {
                existing.name == observation.name && existing.age_text == observation.age_text
            }
        });
        if let Some(index) = existing_index {
            run.observations[index] = observation;
        } else {
            run.observations.push(observation);
            imported += 1;
        }
    }
    Ok(imported)
}

pub(crate) fn parse_sent_age_months(age_text: &str) -> Option<u32> {
    let lower = age_text.to_lowercase();
    if lower.contains("year") {
        let count = first_number(&lower).unwrap_or(1);
        return Some(count.saturating_mul(12));
    }
    if lower.contains("month") {
        return Some(first_number(&lower).unwrap_or(1));
    }
    Some(0).filter(|_| {
        lower.contains("today")
            || lower.contains("minute")
            || lower.contains("hour")
            || lower.contains("day")
            || lower.contains("week")
    })
}

pub(crate) fn first_number(value: &str) -> Option<u32> {
    value
        .split(|character: char| !character.is_ascii_digit())
        .find(|part| !part.is_empty())
        .and_then(|part| part.parse().ok())
}

pub(crate) fn record_pending_withdraw_result(
    run: &mut PendingCleanupRun,
    result: PendingWithdrawResult,
    path: PathBuf,
) -> Result<PendingWithdrawEvent> {
    let (status, note) = result.to_withdraw_status();
    let event = PendingWithdrawEvent {
        at: Local::now(),
        name: result.candidate.name,
        profile_url: result.candidate.profile_url,
        age_text: result.candidate.age_text,
        status,
        note: Some(format!("{}; result={}", note, path.display())),
    };
    if status == PendingWithdrawStatus::Withdrawn
        && run.withdrawals.iter().any(|withdrawal| {
            withdrawal.status == PendingWithdrawStatus::Withdrawn
                && withdrawal.name == event.name
                && withdrawal.profile_url == event.profile_url
        })
    {
        bail!("candidate already recorded as withdrawn: {}", event.name);
    }
    run.withdrawals.push(event.clone());
    if !matches!(
        run.state,
        PendingCleanupState::Done | PendingCleanupState::Blocked
    ) {
        run.state = if run.withdraw_capacity_remaining() == 0 {
            PendingCleanupState::FinalReconcile
        } else {
            PendingCleanupState::Withdrawing
        };
    }
    run.mark_updated();
    Ok(event)
}

pub(crate) fn print_pending_plan(plan: &PendingCleanupPlan) {
    match plan {
        PendingCleanupPlan::CaptureMore { reason } => println!("capture more: {reason}"),
        PendingCleanupPlan::WithdrawCandidate {
            name,
            profile_url,
            age_text,
            withdraw_capacity_remaining,
        } => {
            println!("withdraw next stale invitation: {name}");
            println!("age: {age_text}");
            println!(
                "profile_url: {}",
                profile_url.as_deref().unwrap_or("not captured")
            );
            println!("withdraw capacity remaining: {withdraw_capacity_remaining}");
        }
        PendingCleanupPlan::Reaudit { reason } => println!("re-audit: {reason}"),
        PendingCleanupPlan::FinalAudit => println!("final audit"),
    }
}

pub(crate) fn print_pending_status(run: &PendingCleanupRun) {
    println!("run: {}", run.id);
    println!("date: {}", run.date);
    println!("state: {:?}", run.state);
    println!("threshold months: {}", run.threshold_months);
    println!(
        "withdrawn: {}/{}",
        run.withdrawn_count(),
        run.max_withdrawals
    );
    println!(
        "audit: start {:?}, latest {:?}, delta {:?}",
        run.start_audit,
        run.latest_audit,
        run.audited_delta()
    );
    println!("imported observations: {}", run.observations.len());
}

pub(crate) fn render_pending_report(run: &PendingCleanupRun) -> String {
    let mut lines = Vec::new();
    lines.push(format!("# LinkedIn Pending Cleanup {}", run.date));
    lines.push(String::new());
    lines.push(format!("- Run id: `{}`", run.id));
    lines.push(format!("- State: `{:?}`", run.state));
    lines.push(format!("- Threshold: {} months", run.threshold_months));
    lines.push(format!("- Safety cap: {}", run.max_withdrawals));
    lines.push(format!("- Start audit: {:?}", run.start_audit));
    lines.push(format!("- Final/latest audit: {:?}", run.latest_audit));
    lines.push(format!("- Audited delta: {:?}", run.audited_delta()));
    lines.push(format!("- Withdrawn: {}", run.withdrawn_count()));
    lines.push(format!(
        "- Imported pending observations: {}",
        run.observations.len()
    ));
    lines.push(String::new());
    lines.push("## Withdrawn Names".to_string());
    let names = run
        .withdrawals
        .iter()
        .filter(|event| event.status == PendingWithdrawStatus::Withdrawn)
        .map(|event| format!("{} ({})", event.name, event.age_text))
        .collect::<BTreeSet<_>>();
    if names.is_empty() {
        lines.push("- None recorded".to_string());
    } else {
        for name in names {
            lines.push(format!("- {name}"));
        }
    }
    lines.join("\n")
}
