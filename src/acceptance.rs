use crate::*;

#[derive(Debug, Serialize)]
pub(crate) struct AcceptanceCheckCandidate {
    pub(crate) run_id: Uuid,
    pub(crate) run_date: NaiveDate,
    pub(crate) source: String,
    pub(crate) name: String,
    pub(crate) profile_url: Option<String>,
    pub(crate) sent_at: DateTime<Local>,
    pub(crate) latest_status: AcceptanceStatus,
    pub(crate) latest_checked_at: Option<DateTime<Local>>,
}

impl From<&AcceptanceInvitation> for AcceptanceCheckCandidate {
    fn from(invitation: &AcceptanceInvitation) -> Self {
        Self {
            run_id: invitation.run_id,
            run_date: invitation.run_date,
            source: invitation.source.clone(),
            name: invitation.name.clone(),
            profile_url: invitation.profile_url.clone(),
            sent_at: invitation.sent_at,
            latest_status: invitation.latest_status,
            latest_checked_at: invitation.latest_checked_at,
        }
    }
}

#[derive(Debug, Deserialize)]
pub(crate) struct AcceptanceOutcomeArtifact {
    #[serde(default)]
    pub(crate) rows: Vec<AcceptanceOutcomeRow>,
}

impl AcceptanceOutcomeArtifact {
    pub(crate) fn from_path(path: &PathBuf) -> Result<Self> {
        let raw = fs::read_to_string(path)
            .with_context(|| format!("reading acceptance outcome {}", path.display()))?;
        serde_json::from_str(&raw)
            .with_context(|| format!("parsing acceptance outcome {}", path.display()))
    }
}

pub(crate) fn sent_events_from_controller_log(
    path: &Path,
    run_id: Uuid,
) -> Result<Option<(NaiveDate, Vec<CandidateEvent>)>> {
    let file = fs::File::open(path).with_context(|| format!("opening {}", path.display()))?;
    let reader = BufReader::new(file);
    let mut run_date = None;
    let mut events = Vec::new();

    for (line_index, line_result) in reader.lines().enumerate() {
        let line = line_result.with_context(|| format!("reading {}", path.display()))?;
        if line.trim().is_empty() {
            continue;
        }
        let entry: ControllerEventLogEntry = serde_json::from_str(&line)
            .with_context(|| format!("parsing {} line {}", path.display(), line_index + 1))?;
        if entry.run_id != run_id {
            continue;
        }
        run_date.get_or_insert(entry.at.date_naive());
        if !matches!(
            entry.kind.as_str(),
            "record-send-result" | "record-top-up-result"
        ) {
            continue;
        }
        let Some(event_value) = entry.payload.get("event") else {
            continue;
        };
        let event: CandidateEvent =
            serde_json::from_value(event_value.clone()).with_context(|| {
                format!(
                    "parsing candidate event from {} line {}",
                    path.display(),
                    line_index + 1
                )
            })?;
        if matches!(
            event.status,
            CandidateStatus::Pending | CandidateStatus::AuditTopUp
        ) {
            run_date.get_or_insert(event.at.date_naive());
            events.push(event);
        }
    }

    let Some(run_date) = run_date else {
        return Ok(None);
    };
    if events.is_empty() {
        return Ok(None);
    }
    Ok(Some((run_date, events)))
}

#[derive(Debug, Deserialize)]
pub(crate) struct AcceptanceOutcomeRow {
    pub(crate) source: String,
    pub(crate) name: String,
    #[serde(alias = "profileUrl")]
    pub(crate) profile_url: Option<String>,
    pub(crate) status: AcceptanceStatus,
    #[serde(default, alias = "checkedAt")]
    pub(crate) checked_at: Option<DateTime<Local>>,
    #[serde(default)]
    pub(crate) relationship: Option<String>,
    #[serde(default)]
    pub(crate) evidence: Option<String>,
    #[serde(default)]
    pub(crate) note: Option<String>,
}

pub(crate) fn render_acceptance_report(report: &AcceptanceReport) -> String {
    let mut lines = Vec::new();
    lines.push("# LinkedIn Acceptance Report".to_string());
    lines.push(String::new());
    lines.push(format!("- Min age days: {}", report.min_age_days));
    lines.push(format!("- Max age days: {:?}", report.max_age_days));
    lines.push(format!("- Total sent in window: {}", report.total_sent));
    lines.push(format!("- Checked: {}", report.checked));
    lines.push(format!("- Unchecked: {}", report.unchecked));
    lines.push(format!(
        "- Accepted: {}{}",
        report.accepted,
        percentage_suffix(report.accepted, report.checked)
    ));
    lines.push(format!("- Pending: {}", report.pending));
    lines.push(format!("- Connectable/not pending: {}", report.connectable));
    lines.push(format!("- Unknown: {}", report.unknown));
    lines.push(format!("- Blocked: {}", report.blocked));
    lines.push(format!("- Failed: {}", report.failed));
    lines.push(format!("- Withdrawn: {}", report.withdrawn));
    lines.push(String::new());
    lines.push("## By Source".to_string());
    if report.by_source.is_empty() {
        lines.push("- No invitations in window".to_string());
    } else {
        for (source, source_report) in &report.by_source {
            lines.push(format!(
                "- {}: accepted {}{} / checked {}, pending {}, connectable {}, unknown {}, unchecked {}",
                source,
                source_report.accepted,
                percentage_suffix(source_report.accepted, source_report.checked),
                source_report.checked,
                source_report.pending,
                source_report.connectable,
                source_report.unknown,
                source_report.unchecked
            ));
        }
    }
    lines.join("\n")
}
