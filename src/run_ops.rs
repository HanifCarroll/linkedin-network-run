use crate::*;

pub(crate) fn ensure_known_source(run: &Run, source: &str) -> Result<()> {
    if run.sources.iter().any(|candidate| candidate.name == source) {
        Ok(())
    } else {
        bail!("unknown source: {source}");
    }
}

pub(crate) fn apply_audit(run: &mut Run, people_count: u32, note: Option<String>) {
    let audit = AuditEvent {
        at: Local::now(),
        people_count,
        note,
    };
    if run.start_audit.is_none() {
        run.start_audit = Some(people_count);
        run.state = RunState::StartAudited;
    } else if matches!(run.state, RunState::NeedsReaudit) {
        run.state = RunState::Sending;
    }
    run.latest_audit = Some(people_count);
    run.audits.push(audit);
    run.mark_updated();
}

pub(crate) fn record_send_result(
    run: &mut Run,
    result: SalesNavSendResult,
    path: PathBuf,
) -> Result<CandidateEvent> {
    let (status, note) = result.to_candidate_status();
    let event = CandidateEvent {
        at: Local::now(),
        source: result.candidate.source,
        name: result.candidate.name,
        profile_url: result.candidate.profile_url,
        status,
        note: Some(format!("{}; result={}", note, path.display())),
    };
    ensure_known_source(run, &event.source)?;
    if status == CandidateStatus::Pending
        && run.candidates.iter().any(|candidate| {
            candidate.status == CandidateStatus::Pending
                && candidate.name == event.name
                && candidate.profile_url == event.profile_url
        })
    {
        bail!("candidate already recorded as pending: {}", event.name);
    }
    run.candidates.push(event.clone());
    if !matches!(run.state, RunState::Done | RunState::Blocked) {
        run.state = if run.verified_count() >= run.target {
            RunState::FinalReconcile
        } else {
            RunState::Sending
        };
    }
    run.mark_updated();
    Ok(event)
}

pub(crate) fn record_top_up_send_result(
    run: &mut Run,
    result: SalesNavSendResult,
    path: PathBuf,
    note: Option<String>,
) -> Result<CandidateEvent> {
    let (status, status_note) = result.to_candidate_status();
    let status = if status == CandidateStatus::Pending {
        CandidateStatus::AuditTopUp
    } else {
        status
    };
    let note = [
        Some(status_note),
        note,
        Some(format!("result={}", path.display())),
    ]
    .into_iter()
    .flatten()
    .collect::<Vec<_>>()
    .join("; ");
    let event = CandidateEvent {
        at: Local::now(),
        source: result.candidate.source,
        name: result.candidate.name,
        profile_url: result.candidate.profile_url,
        status,
        note: Some(note),
    };
    ensure_known_source(run, &event.source)?;
    run.candidates.push(event.clone());
    run.mark_updated();
    Ok(event)
}

pub(crate) fn drain_stale_connectable_candidates(
    run: &mut Run,
    source_filter: Option<&str>,
) -> Result<Vec<CandidateEvent>> {
    let stale = run
        .observations
        .iter()
        .filter(|observation| observation.menu_state == "connectable")
        .filter(|observation| {
            source_filter.is_none_or(|source| observation.source == source)
                && !run.preserve_for_audit_top_up(observation)
                && run.source_is_filled_or_closed(&observation.source)
                && !run.has_candidate_event_for_observation(observation)
        })
        .cloned()
        .collect::<Vec<_>>();

    let mut events = Vec::new();
    for observation in stale {
        ensure_known_source(run, &observation.source)?;
        let quota = run.source_quota(&observation.source).unwrap_or_default();
        let verified = run.source_verified_count(&observation.source);
        let event = CandidateEvent {
            at: Local::now(),
            source: observation.source,
            name: observation.name,
            profile_url: observation.profile_url,
            status: CandidateStatus::Skipped,
            note: Some(format!(
                "auto-skipped stale imported candidate after source closed or filled; source {verified}/{quota}, run {}/{}",
                run.verified_count(),
                run.target
            )),
        };
        run.candidates.push(event.clone());
        events.push(event);
    }
    if !events.is_empty() {
        run.mark_updated();
    }
    Ok(events)
}

pub(crate) fn is_uncertain_send_status(status: &str) -> bool {
    status.starts_with("unverified:") || status == "blocked"
}
