use crate::*;

pub(crate) fn print_next(run: &Run) -> Result<()> {
    if let Some(next) = run.next_source() {
        println!("next source: {}", next.name);
        println!("source verified: {}/{}", next.verified, next.quota);
        println!("source remaining: {}", next.remaining_for_source);
        println!("run remaining: {}", next.remaining_for_run);
        if next.fallback {
            println!("fallback: true");
        }
    } else if run.state == RunState::NeedsReaudit {
        println!("next action: re-audit sent invitations People (N)");
    } else if run.verified_count() >= run.target {
        println!("next action: final sent-page audit");
    } else {
        println!("next action: no available source; inspect sources or finish with blocker");
    }
    Ok(())
}

pub(crate) fn print_status(run: &Run) {
    println!("run: {}", run.id);
    println!("date: {}", run.date);
    println!("state: {:?}", run.state);
    println!("target: {}", run.target);
    println!("row-level verified: {}", run.verified_count());
    println!(
        "audit: start {:?}, latest {:?}, delta {:?}",
        run.start_audit,
        run.latest_audit,
        run.audited_delta()
    );
    if let Some(next) = run.next_source() {
        println!(
            "next: {} ({}/{}, run remaining {})",
            next.name, next.verified, next.quota, next.remaining_for_run
        );
    }
}

pub(crate) fn render_report(run: &Run) -> String {
    let mut lines = Vec::new();
    lines.push(format!("# LinkedIn Network Run {}", run.date));
    lines.push(String::new());
    lines.push(format!("- Run id: `{}`", run.id));
    lines.push(format!("- State: `{:?}`", run.state));
    lines.push(format!("- Target: {}", run.target));
    lines.push(format!("- Start audit: {:?}", run.start_audit));
    lines.push(format!("- Final/latest audit: {:?}", run.latest_audit));
    lines.push(format!("- Audited delta: {:?}", run.audited_delta()));
    lines.push(format!(
        "- Row-level verified pending: {}",
        run.verified_count()
    ));
    lines.push(format!(
        "- Imported candidate observations: {}",
        run.observations.len()
    ));
    lines.push(String::new());
    lines.push("## Source Counts".to_string());
    for source in &run.sources {
        let verified = run.source_verified_count(&source.name);
        lines.push(format!(
            "- {}: {} verified{}{}",
            source.name,
            verified,
            if source.target > 0 {
                format!(" / target {}", source.target)
            } else {
                String::new()
            },
            if source.exhausted { " (exhausted)" } else { "" }
        ));
    }
    lines.push(String::new());
    lines.push("## Source Yield".to_string());
    for stats in source_yield_report(run) {
        let yield_text = stats
            .connectable_yield
            .map(|value| format!("{:.1}%", value * 100.0))
            .unwrap_or_else(|| "n/a".to_string());
        lines.push(format!(
            "- {}: {} connectable / {} rows ({yield_text}); already pending {}; email-required skips {}; {}",
            stats.source,
            stats.connectable_count,
            stats.raw_row_count,
            stats.already_pending_count,
            stats.email_required_skips,
            stats.recommendation
        ));
    }
    if !run.timings.is_empty() {
        lines.push(String::new());
        lines.push("## Phase Timing".to_string());
        let total_ms: u64 = run.timings.iter().map(|event| event.duration_ms).sum();
        lines.push(format!(
            "- Total recorded: {}",
            format_duration_ms(total_ms)
        ));
        let mut by_phase: BTreeMap<String, u64> = BTreeMap::new();
        for event in &run.timings {
            *by_phase.entry(event.phase.clone()).or_default() += event.duration_ms;
        }
        for (phase, duration_ms) in by_phase {
            lines.push(format!("- {phase}: {}", format_duration_ms(duration_ms)));
        }
    }
    if !run.notes.is_empty() {
        lines.push(String::new());
        lines.push("## Notes".to_string());
        for note in &run.notes {
            lines.push(format!("- {note}"));
        }
    }
    lines.push(String::new());
    lines.push("## Verified Names".to_string());
    let names = run
        .candidates
        .iter()
        .filter(|candidate| candidate.status == CandidateStatus::Pending)
        .map(|candidate| candidate.name.clone())
        .collect::<BTreeSet<_>>();
    if names.is_empty() {
        lines.push("- None recorded".to_string());
    } else {
        for name in names {
            lines.push(format!("- {name}"));
        }
    }
    let top_up_names = run
        .candidates
        .iter()
        .filter(|candidate| candidate.status == CandidateStatus::AuditTopUp)
        .map(|candidate| candidate.name.clone())
        .collect::<BTreeSet<_>>();
    if !top_up_names.is_empty() {
        lines.push(String::new());
        lines.push("## Audit Top-Up Names".to_string());
        for name in top_up_names {
            lines.push(format!("- {name}"));
        }
    }
    lines.join("\n")
}
