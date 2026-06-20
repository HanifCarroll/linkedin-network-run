use crate::*;

#[derive(Debug, Deserialize)]
pub(crate) struct SalesNavCapture {
    #[serde(rename = "capturedAt")]
    pub(crate) captured_at: Option<String>,
    pub(crate) source: Option<String>,
    #[serde(default)]
    pub(crate) url: Option<String>,
    #[serde(default, rename = "resumeUrl")]
    pub(crate) resume_url: Option<String>,
    #[serde(default)]
    pub(crate) page: Option<SalesNavCapturePage>,
    #[serde(default)]
    pub(crate) pages: Vec<SalesNavCapturePage>,
    #[serde(default, rename = "stateCounts")]
    pub(crate) state_counts: BTreeMap<String, u32>,
    #[serde(default, rename = "rawRowCount")]
    pub(crate) raw_row_count: Option<u32>,
    #[serde(default, rename = "outputRowCount")]
    pub(crate) output_row_count: Option<u32>,
    pub(crate) rows: Vec<SalesNavCaptureRow>,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct SalesNavCapturePage {
    #[serde(default)]
    pub(crate) url: Option<String>,
    #[serde(default, rename = "pageLabel")]
    pub(crate) page_label: Option<String>,
}

#[derive(Debug, Clone, Copy, Default)]
pub(crate) struct ImportCaptureOptions {
    pub(crate) only_connectable: bool,
}

#[derive(Debug, Deserialize)]
pub(crate) struct SalesNavAudit {
    #[serde(alias = "peopleCount")]
    pub(crate) people_count: u32,
    #[serde(default, alias = "recentNames")]
    pub(crate) recent_names: Vec<String>,
}

impl SalesNavAudit {
    pub(crate) fn from_path(path: &PathBuf) -> Result<Self> {
        let raw = fs::read_to_string(path)
            .with_context(|| format!("reading audit {}", path.display()))?;
        serde_json::from_str(&raw).with_context(|| format!("parsing audit {}", path.display()))
    }
}

#[derive(Debug, Deserialize)]
pub(crate) struct SalesNavSendResult {
    pub(crate) candidate: SalesNavSendCandidate,
    pub(crate) status: String,
    #[serde(default)]
    pub(crate) send: Option<serde_json::Value>,
}

impl SalesNavSendResult {
    pub(crate) fn from_path(path: &PathBuf) -> Result<Self> {
        let raw =
            fs::read_to_string(path).with_context(|| format!("reading send {}", path.display()))?;
        serde_json::from_str(&raw).with_context(|| format!("parsing send {}", path.display()))
    }

    pub(crate) fn to_candidate_status(&self) -> (CandidateStatus, String) {
        match self.status.as_str() {
            "pending-verified" => (
                CandidateStatus::Pending,
                "salesnav-send-one verified Connect - Pending".to_string(),
            ),
            "already-pending" => (
                CandidateStatus::AlreadyPending,
                "salesnav-send-one found already pending".to_string(),
            ),
            "email-required" => (
                CandidateStatus::Skipped,
                "salesnav-send-one stopped on email-required invite flow".to_string(),
            ),
            other => {
                let send = self
                    .send
                    .as_ref()
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| "no send detail".to_string());
                (
                    CandidateStatus::Failed,
                    format!("salesnav-send-one status {other}; {send}"),
                )
            }
        }
    }
}

#[derive(Debug, Deserialize)]
pub(crate) struct SalesNavSendCandidate {
    pub(crate) source: String,
    pub(crate) name: String,
    #[serde(alias = "profileUrl")]
    pub(crate) profile_url: Option<String>,
}

impl SalesNavCapture {
    pub(crate) fn from_path(path: &PathBuf) -> Result<Self> {
        let raw = fs::read_to_string(path)
            .with_context(|| format!("reading capture {}", path.display()))?;
        serde_json::from_str(&raw).with_context(|| format!("parsing capture {}", path.display()))
    }
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct SalesNavCaptureRow {
    pub(crate) index: u32,
    pub(crate) name: Option<String>,
    #[serde(rename = "profileUrl")]
    pub(crate) profile_url: Option<String>,
    #[serde(rename = "scrollUrn")]
    pub(crate) scroll_urn: Option<String>,
    #[serde(rename = "visibleState")]
    pub(crate) visible_state: Option<serde_json::Value>,
    #[serde(rename = "menuState")]
    pub(crate) menu_state: Option<String>,
    #[serde(rename = "menuLabels")]
    pub(crate) menu_labels: Option<Vec<SalesNavCaptureMenuLabel>>,
    #[serde(rename = "rowHtmlPath")]
    pub(crate) row_html_path: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct SalesNavCaptureMenuLabel {
    pub(crate) text: Option<String>,
    pub(crate) aria: Option<String>,
}

pub(crate) fn capture_state_count(capture: &SalesNavCapture, state: &str) -> u32 {
    capture.state_counts.get(state).copied().unwrap_or_else(|| {
        capture
            .rows
            .iter()
            .filter(|row| row.menu_state.as_deref() == Some(state))
            .count() as u32
    })
}

pub(crate) fn same_observation_identity(
    left: &CandidateObservation,
    right: &CandidateObservation,
) -> bool {
    if let (Some(left_url), Some(right_url)) = (&left.profile_url, &right.profile_url) {
        normalize_linkedin_url(left_url) == normalize_linkedin_url(right_url)
    } else {
        left.source == right.source && left.name == right.name
    }
}

pub(crate) fn candidate_matches_observation(
    candidate: &CandidateEvent,
    observation: &CandidateObservation,
) -> bool {
    if let (Some(candidate_url), Some(observation_url)) =
        (&candidate.profile_url, &observation.profile_url)
    {
        normalize_linkedin_url(candidate_url) == normalize_linkedin_url(observation_url)
    } else {
        candidate.name == observation.name && candidate.source == observation.source
    }
}

pub(crate) fn is_auto_stale_skip(candidate: &CandidateEvent) -> bool {
    candidate.status == CandidateStatus::Skipped
        && candidate
            .note
            .as_deref()
            .is_some_and(|note| note.contains("auto-skipped stale imported candidate"))
}

pub(crate) fn sales_profile_urn_to_lead_url(urn: &str) -> Option<String> {
    let tuple = urn.split_once('(')?.1.strip_suffix(')')?;
    let mut parts = tuple.split(',').map(str::trim);
    let profile_id = parts.next()?;
    let auth_type = parts.next()?;
    let auth_token = parts.next()?;
    if profile_id.is_empty()
        || auth_type.is_empty()
        || auth_token.is_empty()
        || parts.next().is_some()
    {
        return None;
    }
    Some(format!(
        "https://www.linkedin.com/sales/lead/{profile_id},{auth_type},{auth_token}"
    ))
}

pub(crate) fn push_timing(
    run: &mut Run,
    phase: &str,
    source: Option<String>,
    started: Instant,
    detail: Option<String>,
) {
    let duration_ms = started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64;
    run.timings.push(RunTimingEvent {
        at: Local::now(),
        phase: phase.to_string(),
        source,
        duration_ms,
        detail,
    });
    run.mark_updated();
}

pub(crate) fn capture_to_observations(
    source: &str,
    capture: &SalesNavCapture,
    options: ImportCaptureOptions,
) -> Vec<CandidateObservation> {
    capture
        .rows
        .iter()
        .filter_map(|row| {
            let name = row.name.as_ref()?.trim();
            if name.is_empty() {
                return None;
            }
            let menu_state = row.menu_state.as_deref().unwrap_or("unknown").to_string();
            if options.only_connectable && menu_state != "connectable" {
                return None;
            }
            let menu_labels = row
                .menu_labels
                .clone()
                .unwrap_or_default()
                .into_iter()
                .filter_map(|label| label.text.or(label.aria))
                .map(|label| label.trim().to_string())
                .filter(|label| !label.is_empty())
                .collect::<Vec<_>>();
            let profile_url = row.profile_url.clone().or_else(|| {
                row.scroll_urn
                    .as_deref()
                    .and_then(sales_profile_urn_to_lead_url)
            });
            Some(CandidateObservation {
                imported_at: Local::now(),
                captured_at: capture.captured_at.clone(),
                source: source.to_string(),
                index: row.index,
                name: name.to_string(),
                profile_url,
                sales_profile_urn: row.scroll_urn.clone(),
                visible_state: row.visible_state.clone().unwrap_or(serde_json::Value::Null),
                menu_state,
                menu_labels,
                row_html_path: row.row_html_path.clone(),
            })
        })
        .collect()
}

pub(crate) fn update_capture_cursor(run: &mut Run, source: &str, capture: &SalesNavCapture) {
    let last_page = capture.page.as_ref().or_else(|| capture.pages.last());
    let resume_url = capture
        .resume_url
        .clone()
        .or_else(|| capture.url.clone())
        .or_else(|| last_page.and_then(|page| page.url.clone()));
    let captured_pages = if capture.pages.is_empty() {
        u32::from(capture.page.is_some())
    } else {
        capture.pages.len() as u32
    };
    let raw_row_count = capture
        .raw_row_count
        .unwrap_or_else(|| capture.rows.len() as u32);
    let output_row_count = capture
        .output_row_count
        .unwrap_or_else(|| capture.rows.len() as u32);

    run.capture_cursors.insert(
        source.to_string(),
        SourceCaptureCursor {
            source: source.to_string(),
            updated_at: Local::now(),
            captured_at: capture.captured_at.clone(),
            resume_url,
            page_label: last_page.and_then(|page| page.page_label.clone()),
            captured_pages,
            raw_row_count,
            output_row_count,
            connectable_count: capture_state_count(capture, "connectable"),
            already_pending_count: capture_state_count(capture, "already-pending"),
            missing_trigger_count: capture_state_count(capture, "missing-trigger"),
            state_counts: capture.state_counts.clone(),
        },
    );
}

pub(crate) fn import_capture(
    run: &mut Run,
    capture: SalesNavCapture,
    options: ImportCaptureOptions,
) -> Result<usize> {
    let source = capture
        .source
        .clone()
        .or_else(|| run.next_source().map(|next| next.name))
        .ok_or_else(|| anyhow!("capture did not include source and run has no next source"))?;
    ensure_known_source(run, &source)?;
    update_capture_cursor(run, &source, &capture);

    let mut imported = 0;
    for observation in capture_to_observations(&source, &capture, options) {
        let existing_index = run
            .observations
            .iter()
            .position(|existing| same_observation_identity(existing, &observation));
        if let Some(index) = existing_index {
            run.observations[index] = observation;
        } else {
            run.observations.push(observation);
            imported += 1;
        }
    }
    Ok(imported)
}

pub(crate) fn import_capture_into_reservoir(
    reservoir: &mut CandidateReservoir,
    capture: SalesNavCapture,
    options: ImportCaptureOptions,
) -> Result<usize> {
    let source = capture
        .source
        .clone()
        .ok_or_else(|| anyhow!("capture did not include source"))?;
    let mut imported = 0;
    for observation in capture_to_observations(&source, &capture, options) {
        let existing_index = reservoir
            .observations
            .iter()
            .position(|existing| same_observation_identity(existing, &observation));
        if let Some(index) = existing_index {
            reservoir.observations[index] = observation;
        } else {
            reservoir.observations.push(observation);
            imported += 1;
        }
    }
    reservoir.updated_at = Some(Local::now());
    Ok(imported)
}

pub(crate) fn fill_run_from_reservoir(
    run: &mut Run,
    reservoir: &mut CandidateReservoir,
    source: &str,
    limit: usize,
) -> Result<usize> {
    ensure_known_source(run, source)?;
    let mut selected_keys = Vec::new();
    let mut imported = 0;
    for observation in reservoir.available_for_run_source(run, source) {
        if imported >= limit {
            break;
        }
        let mut observation = observation.clone();
        observation.imported_at = Local::now();
        selected_keys.push(observation_key(&observation));
        run.observations.push(observation);
        imported += 1;
    }
    if imported > 0 {
        reservoir
            .observations
            .retain(|observation| !selected_keys.contains(&observation_key(observation)));
        reservoir.updated_at = Some(Local::now());
        run.mark_updated();
    }
    Ok(imported)
}

pub(crate) fn fill_run_from_reservoir_for_top_up(
    run: &mut Run,
    reservoir: &mut CandidateReservoir,
    source: &str,
    limit: usize,
) -> Result<usize> {
    ensure_known_source(run, source)?;
    let mut selected_keys = Vec::new();
    let mut selected = Vec::new();
    for observation in reservoir
        .observations
        .iter()
        .filter(|observation| {
            observation.source == source
                && observation.menu_state == "connectable"
                && !run.has_top_up_blocking_event_for_observation(observation)
                && !run
                    .observations
                    .iter()
                    .any(|existing| same_observation_identity(existing, observation))
        })
        .take(limit)
    {
        let mut observation = observation.clone();
        observation.imported_at = Local::now();
        selected_keys.push(observation_key(&observation));
        selected.push(observation);
    }

    let imported = selected.len();
    if imported > 0 {
        run.observations.extend(selected);
        reservoir
            .observations
            .retain(|observation| !selected_keys.contains(&observation_key(observation)));
        reservoir.updated_at = Some(Local::now());
        run.mark_updated();
    }
    Ok(imported)
}

pub(crate) fn observation_key(
    observation: &CandidateObservation,
) -> (String, String, Option<String>) {
    (
        observation.source.clone(),
        observation.name.clone(),
        observation
            .profile_url
            .as_deref()
            .map(normalize_linkedin_url),
    )
}

pub(crate) fn source_yield_stats(run: &Run, source: &SourcePlan) -> SourceYieldStats {
    let cursor = run.capture_cursors.get(&source.name);
    let raw_row_count = cursor.map(|cursor| cursor.raw_row_count).unwrap_or(0);
    let connectable_count = cursor
        .map(|cursor| cursor.connectable_count)
        .unwrap_or_else(|| {
            run.observations
                .iter()
                .filter(|observation| {
                    observation.source == source.name.as_str()
                        && observation.menu_state == "connectable"
                })
                .count() as u32
        });
    let already_pending_count = cursor
        .map(|cursor| cursor.already_pending_count)
        .unwrap_or_default();
    let email_required_skips = run
        .candidates
        .iter()
        .filter(|candidate| candidate.source == source.name.as_str())
        .filter(|candidate| candidate.status == CandidateStatus::Skipped)
        .filter(|candidate| {
            candidate
                .note
                .as_deref()
                .is_some_and(|note| note.to_ascii_lowercase().contains("email-required"))
        })
        .count() as u32;
    let pending_sends = run.source_verified_count(&source.name);
    let connectable_yield = if raw_row_count > 0 {
        Some(f64::from(connectable_count) / f64::from(raw_row_count))
    } else {
        None
    };
    let recommendation = match connectable_yield {
        Some(yield_rate) if raw_row_count >= 50 && yield_rate <= 0.05 => {
            "low-yield: consider reservoir/fallback before deeper capture".to_string()
        }
        Some(yield_rate) if raw_row_count >= 25 && yield_rate <= 0.10 => {
            "thin: capture with a small buffer and be ready to carry over".to_string()
        }
        Some(_) => "ok".to_string(),
        None => "no capture data".to_string(),
    };
    SourceYieldStats {
        source: source.name.clone(),
        raw_row_count,
        connectable_count,
        already_pending_count,
        email_required_skips,
        pending_sends,
        connectable_yield,
        recommendation,
    }
}

pub(crate) fn source_yield_report(run: &Run) -> Vec<SourceYieldStats> {
    run.sources
        .iter()
        .map(|source| source_yield_stats(run, source))
        .collect()
}

pub(crate) fn low_yield_source_names(
    run: &Run,
    min_raw_rows: u32,
    max_connectable_yield: f64,
) -> Vec<String> {
    source_yield_report(run)
        .into_iter()
        .filter(|stats| stats.raw_row_count >= min_raw_rows)
        .filter(|stats| {
            stats
                .connectable_yield
                .is_some_and(|yield_rate| yield_rate <= max_connectable_yield)
        })
        .map(|stats| stats.source)
        .collect()
}
