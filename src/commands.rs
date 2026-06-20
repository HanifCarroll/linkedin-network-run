use crate::*;

pub(crate) fn dispatch(store: &Store, command: Command) -> Result<()> {
    match command {
        Command::Start {
            target,
            date,
            force,
            max_real_sends,
        } => {
            let date = date.unwrap_or_else(|| Local::now().date_naive());
            if store.active_path().exists() && !force {
                bail!("an active run already exists; use --force to replace it");
            }
            let run = Run::new_with_max_real_sends(target, date, max_real_sends.unwrap_or(target));
            store.save(&run)?;
            store.append_event(&run, "start", &serde_json::json!({ "target": target }))?;
            println!("started run {} for {} with target {}", run.id, date, target);
            print_next(&run)?;
        }
        Command::Audit { people_count, note } => {
            let mut run = store.load()?;
            apply_audit(&mut run, people_count, note);
            store.save(&run)?;
            store.append_event(
                &run,
                "audit",
                &serde_json::json!({ "people_count": people_count, "delta": run.audited_delta() }),
            )?;
            println!(
                "audit recorded: People ({}){}",
                people_count,
                run.audited_delta()
                    .map(|delta| format!(", audited delta {delta}"))
                    .unwrap_or_default()
            );
        }
        Command::ImportAudit { path } => {
            let started = Instant::now();
            let mut run = store.load()?;
            let audit = SalesNavAudit::from_path(&path)?;
            apply_audit(
                &mut run,
                audit.people_count,
                Some(format!(
                    "imported audit; recent_names={}",
                    audit.recent_names.join(", ")
                )),
            );
            push_timing(
                &mut run,
                "import-audit",
                None,
                started,
                Some(format!(
                    "people_count={}; path={}",
                    audit.people_count,
                    path.display()
                )),
            );
            store.save(&run)?;
            store.append_event(
                &run,
                "import-audit",
                &serde_json::json!({ "path": path, "people_count": audit.people_count }),
            )?;
            println!(
                "audit imported: People ({}){}",
                audit.people_count,
                run.audited_delta()
                    .map(|delta| format!(", audited delta {delta}"))
                    .unwrap_or_default()
            );
        }
        Command::Next => {
            let run = store.load()?;
            if run.state == RunState::NeedsReaudit {
                bail!("run is in NEEDS_REAUDIT; record a fresh sent-page audit before continuing");
            }
            print_next(&run)?;
        }
        Command::Record {
            source,
            name,
            profile_url,
            status,
            note,
        } => {
            let mut run = store.load()?;
            if run.state == RunState::NeedsReaudit {
                bail!(
                    "run is in NEEDS_REAUDIT; record a fresh sent-page audit before recording more sends"
                );
            }
            ensure_known_source(&run, &source)?;
            let duplicate_pending = status == CandidateStatus::Pending
                && run.candidates.iter().any(|candidate| {
                    candidate.status == CandidateStatus::Pending
                        && candidate.name == name
                        && candidate.profile_url == profile_url
                });
            if duplicate_pending {
                bail!("candidate already recorded as pending: {name}");
            }
            let event = CandidateEvent {
                at: Local::now(),
                source,
                name,
                profile_url,
                status,
                note,
            };
            run.candidates.push(event.clone());
            if !matches!(run.state, RunState::Done | RunState::Blocked) {
                run.state = if run.verified_count() >= run.target {
                    RunState::FinalReconcile
                } else {
                    RunState::Sending
                };
            }
            let drained = drain_stale_connectable_candidates(&mut run, None)?;
            let drained_count = drained.len();
            run.mark_updated();
            store.save(&run)?;
            store.append_event(&run, "record", &event)?;
            if drained_count > 0 {
                store.append_event(
                    &run,
                    "drain-stale-candidates",
                    &serde_json::json!({ "events": drained }),
                )?;
            }
            println!(
                "recorded {:?}; verified {}/{}",
                status,
                run.verified_count(),
                run.target
            );
            if drained_count > 0 {
                println!("auto-skipped {drained_count} stale queued candidates");
            }
            if let Some(next) = run.next_source() {
                println!(
                    "next: {} (source {}/{}, run remaining {})",
                    next.name, next.verified, next.quota, next.remaining_for_run
                );
            } else if run.verified_count() >= run.target {
                println!(
                    "target row-level verification reached; run final sent-page audit before finish"
                );
            }
        }
        Command::RecordSendResult { path } => {
            let mut run = store.load()?;
            if run.state == RunState::NeedsReaudit {
                bail!(
                    "run is in NEEDS_REAUDIT; record a fresh sent-page audit before recording send results"
                );
            }
            let result = SalesNavSendResult::from_path(&path)?;
            let event = record_send_result(&mut run, result, path.clone())?;
            let drained = drain_stale_connectable_candidates(&mut run, None)?;
            let drained_count = drained.len();
            store.save(&run)?;
            store.append_event(
                &run,
                "record-send-result",
                &serde_json::json!({ "path": path, "event": event }),
            )?;
            if drained_count > 0 {
                store.append_event(
                    &run,
                    "drain-stale-candidates",
                    &serde_json::json!({ "events": drained }),
                )?;
            }
            println!(
                "recorded send result as {:?}; verified {}/{}",
                event.status,
                run.verified_count(),
                run.target
            );
            if drained_count > 0 {
                println!("auto-skipped {drained_count} stale queued candidates");
            }
        }
        Command::RecordTopUpResult { path, note } => {
            let mut run = store.load()?;
            let result = SalesNavSendResult::from_path(&path)?;
            let event = record_top_up_send_result(&mut run, result, path.clone(), note)?;
            store.save(&run)?;
            store.append_event(
                &run,
                "record-top-up-result",
                &serde_json::json!({ "path": path, "event": event }),
            )?;
            println!(
                "recorded top-up result as {:?}; row-level verified remains {}/{}",
                event.status,
                run.verified_count(),
                run.target
            );
        }
        Command::SendNext {
            session,
            playwriter,
            script,
            out_dir,
            dry_run,
            allow_send,
            no_record,
        } => {
            let run = store.load()?;
            if run.state == RunState::NeedsReaudit {
                bail!("run is in NEEDS_REAUDIT; record a fresh sent-page audit before sending");
            }
            if allow_send && run.real_send_capacity_remaining() == 0 {
                bail!(
                    "real-send cap reached: {}/{} verified sends",
                    run.verified_count(),
                    run.max_real_sends
                );
            }
            let candidate = run
                .next_connectable_observation()
                .ok_or_else(|| anyhow!("no unrecorded connectable candidate available"))?;
            let session =
                session.ok_or_else(|| anyhow!("--session is required to execute Playwriter"))?;
            let started = Instant::now();
            let result_path = run_playwriter_send(
                &playwriter,
                &session,
                &script,
                &out_dir,
                candidate,
                dry_run,
                allow_send,
            )?;
            println!("send result: {}", result_path.display());
            if allow_send && !dry_run && !no_record {
                let mut run = store.load()?;
                let result = SalesNavSendResult::from_path(&result_path)?;
                let event = record_send_result(&mut run, result, result_path.clone())?;
                let drained = drain_stale_connectable_candidates(&mut run, None)?;
                let drained_count = drained.len();
                push_timing(
                    &mut run,
                    "send-next",
                    Some(event.source.clone()),
                    started,
                    Some(format!(
                        "status={:?}; path={}",
                        event.status,
                        result_path.display()
                    )),
                );
                store.save(&run)?;
                store.append_event(
                    &run,
                    "record-send-result",
                    &serde_json::json!({ "path": result_path, "event": event }),
                )?;
                if drained_count > 0 {
                    store.append_event(
                        &run,
                        "drain-stale-candidates",
                        &serde_json::json!({ "events": drained }),
                    )?;
                }
                println!(
                    "recorded send result; verified {}/{}",
                    run.verified_count(),
                    run.target
                );
                if drained_count > 0 {
                    println!("auto-skipped {drained_count} stale queued candidates");
                }
            }
        }
        Command::SendGuarded {
            session,
            playwriter,
            script,
            out_dir,
            max_attempts,
            dry_run,
            single_pass,
            allow_send,
            no_record,
        } => handle_send_guarded(
            &store,
            session,
            playwriter,
            script,
            out_dir,
            max_attempts,
            dry_run,
            single_pass,
            allow_send,
            no_record,
        )?,
        Command::DrainStaleCandidates { source } => {
            let mut run = store.load()?;
            let drained = drain_stale_connectable_candidates(&mut run, source.as_deref())?;
            let drained_count = drained.len();
            store.save(&run)?;
            store.append_event(
                &run,
                "drain-stale-candidates",
                &serde_json::json!({ "source": source, "events": drained }),
            )?;
            println!("auto-skipped {drained_count} stale queued candidates");
            print_next(&run)?;
        }
        Command::ReconcileAudit {
            session,
            playwriter,
            script,
            out_dir,
            attempts,
            delay_ms,
            finish,
        } => handle_reconcile_audit(
            &store, session, playwriter, script, out_dir, attempts, delay_ms, finish,
        )?,
        Command::TopUpReconcile {
            session,
            playwriter,
            send_script,
            audit_script,
            capture_script,
            saved_searches,
            fallback_source,
            fallback_url,
            fallback_pages,
            fallback_stop_after_connectable,
            fallback_limit,
            fallback_row_scroll_delay_ms,
            no_fallback_capture,
            out_dir,
            max_attempts,
            delay_ms,
            allow_send,
            finish,
        } => handle_top_up_reconcile(
            &store,
            session,
            playwriter,
            send_script,
            audit_script,
            TopUpFallbackOptions {
                capture_script,
                saved_searches,
                source: fallback_source,
                url: fallback_url,
                pages: fallback_pages,
                stop_after_connectable: fallback_stop_after_connectable,
                limit: fallback_limit,
                row_scroll_delay_ms: fallback_row_scroll_delay_ms,
                capture_enabled: !no_fallback_capture,
            },
            out_dir,
            max_attempts,
            delay_ms,
            allow_send,
            finish,
        )?,
        Command::SourceExhausted { source, note } => {
            let mut run = store.load()?;
            let source_plan = run
                .sources
                .iter_mut()
                .find(|candidate| candidate.name == source)
                .ok_or_else(|| anyhow!("unknown source: {source}"))?;
            source_plan.exhausted = true;
            if let Some(note) = note {
                run.notes
                    .push(format!("source exhausted: {source}: {note}"));
            }
            run.mark_updated();
            store.save(&run)?;
            store.append_event(
                &run,
                "source-exhausted",
                &serde_json::json!({ "source": source }),
            )?;
            println!("marked source exhausted");
            print_next(&run)?;
        }
        Command::NeedsReaudit { reason } => {
            let mut run = store.load()?;
            run.state = RunState::NeedsReaudit;
            run.notes.push(format!("needs re-audit: {reason}"));
            run.mark_updated();
            store.save(&run)?;
            store.append_event(
                &run,
                "needs-reaudit",
                &serde_json::json!({ "reason": reason }),
            )?;
            println!("run paused in NEEDS_REAUDIT; record a fresh People (N) audit before sending");
        }
        Command::ImportCapture {
            path,
            only_connectable,
        } => {
            let started = Instant::now();
            let mut run = store.load()?;
            let capture = SalesNavCapture::from_path(&path)?;
            let capture_source = capture.source.clone();
            let imported =
                import_capture(&mut run, capture, ImportCaptureOptions { only_connectable })?;
            let drained = drain_stale_connectable_candidates(&mut run, None)?;
            let drained_count = drained.len();
            push_timing(
                &mut run,
                "import-capture",
                capture_source,
                started,
                Some(format!(
                    "imported={imported}; drained={drained_count}; only_connectable={only_connectable}; path={}",
                    path.display()
                )),
            );
            run.mark_updated();
            store.save(&run)?;
            store.append_event(
                &run,
                "import-capture",
                &serde_json::json!({
                    "path": path,
                    "imported": imported,
                    "only_connectable": only_connectable,
                }),
            )?;
            if drained_count > 0 {
                store.append_event(
                    &run,
                    "drain-stale-candidates",
                    &serde_json::json!({ "events": drained }),
                )?;
            }
            println!("imported {imported} candidate observations");
            if drained_count > 0 {
                println!("auto-skipped {drained_count} stale queued candidates");
            }
            if let Some(candidate) = run.next_connectable_observation() {
                println!(
                    "next connectable: {} ({})",
                    candidate.name,
                    candidate
                        .profile_url
                        .as_deref()
                        .unwrap_or("no profile url captured")
                );
            } else if let Some(candidate) = run.next_top_up_observation() {
                println!(
                    "next top-up connectable: {} ({})",
                    candidate.name,
                    candidate
                        .profile_url
                        .as_deref()
                        .unwrap_or("no profile url captured")
                );
            } else {
                println!("no unrecorded connectable candidate in imported captures");
            }
        }
        Command::NextCandidate { json } => {
            let run = store.load()?;
            let candidate = run.next_connectable_observation();
            if json {
                println!("{}", serde_json::to_string_pretty(&candidate)?);
            } else if let Some(candidate) = candidate {
                println!("source: {}", candidate.source);
                println!("name: {}", candidate.name);
                println!("profile_url: {:?}", candidate.profile_url);
                println!("menu_state: {}", candidate.menu_state);
                println!("menu_labels: {}", candidate.menu_labels.join(", "));
            } else {
                println!("no unrecorded connectable candidate available");
            }
        }
        Command::Candidates { json, status } => {
            let run = store.load()?;
            let observations = run
                .observations
                .iter()
                .filter(|observation| {
                    status
                        .as_ref()
                        .is_none_or(|status| observation.menu_state == *status)
                })
                .collect::<Vec<_>>();
            if json {
                println!("{}", serde_json::to_string_pretty(&observations)?);
            } else {
                for observation in observations {
                    println!(
                        "{}\t{}\t{}\t{}",
                        observation.menu_state,
                        observation.source,
                        observation.name,
                        observation.profile_url.as_deref().unwrap_or("")
                    );
                }
            }
        }
        Command::Plan { json } => {
            let run = store.load()?;
            let reservoir = store.load_reservoir()?;
            let plan = run.operator_plan_with_reservoir(Some(&reservoir));
            if json {
                println!("{}", serde_json::to_string_pretty(&plan)?);
            } else {
                match plan {
                    OperatorPlan::UseReservoir {
                        source,
                        remaining,
                        available,
                    } => {
                        println!(
                            "use reservoir: {source} ({available} available, {remaining} needed)"
                        );
                        println!(
                            "run: linkedin-network-run reservoir fill-run --source \"{source}\""
                        );
                    }
                    OperatorPlan::CaptureSource {
                        source,
                        remaining,
                        capture,
                        resume_url,
                        cursor,
                    } => {
                        println!("capture source: {source} ({remaining} needed)");
                        println!(
                            "recommended capture: pages={}, stopAfterConnectable={}, playwriterTimeoutMs={} (buffer={}, reason={})",
                            capture.pages,
                            capture.stop_after_connectable,
                            capture.playwriter_timeout_ms,
                            capture.buffer,
                            capture.reason
                        );
                        if let Some(resume_url) = resume_url {
                            println!("resume_url: {resume_url}");
                        }
                        if let Some(cursor) = cursor {
                            println!(
                                "last capture: {} rows, {} connectable, page {}",
                                cursor.raw_row_count,
                                cursor.connectable_count,
                                cursor.page_label.as_deref().unwrap_or("unknown")
                            );
                        }
                    }
                    OperatorPlan::SendCandidate {
                        source,
                        name,
                        profile_url,
                        real_send_capacity_remaining,
                    } => {
                        println!("send next candidate: {name}");
                        println!("source: {source}");
                        println!(
                            "profile_url: {}",
                            profile_url.as_deref().unwrap_or("not captured")
                        );
                        println!("real-send capacity remaining: {real_send_capacity_remaining}");
                    }
                    OperatorPlan::Reaudit { reason } => println!("re-audit: {reason}"),
                    OperatorPlan::FinalAudit => println!("final audit"),
                    OperatorPlan::Blocked { reason } => println!("blocked: {reason}"),
                }
            }
        }
        Command::Status { json } => {
            let run = store.load()?;
            if json {
                println!("{}", serde_json::to_string_pretty(&run)?);
            } else {
                print_status(&run);
            }
        }
        Command::Report => {
            let run = store.load()?;
            println!("{}", render_report(&run));
        }
        Command::Finish { force } => {
            let mut run = store.load()?;
            let delta = run.audited_delta();
            if !force && delta != Some(i64::from(run.target)) {
                bail!(
                    "final audit delta is {:?}, expected {}; run audit <people-count> or use --force",
                    delta,
                    run.target
                );
            }
            run.state = RunState::Done;
            run.mark_updated();
            store.save(&run)?;
            let mut ledger = store.load_acceptance_ledger()?;
            let seeded = ledger.upsert_from_run(&run);
            store.save_acceptance_ledger(&ledger)?;
            store.append_event(
                &run,
                "finish",
                &serde_json::json!({ "audited_delta": delta, "acceptance_seeded": seeded }),
            )?;
            store.append_acceptance_event(
                "seed-from-finish",
                &serde_json::json!({ "run_id": run.id, "seeded": seeded }),
            )?;
            println!("{}", render_report(&run));
            println!("acceptance ledger seeded: {seeded} new invitations");
        }
        Command::Acceptance { command } => handle_acceptance_command(&store, command)?,
        Command::Reservoir { command } => handle_reservoir_command(&store, command)?,
        Command::TuneSources {
            min_raw_rows,
            max_connectable_yield,
            apply,
        } => handle_tune_sources(&store, min_raw_rows, max_connectable_yield, apply)?,
        Command::PendingCleanup { command } => handle_pending_cleanup_command(&store, command)?,
    }

    Ok(())
}

pub(crate) fn handle_acceptance_command(store: &Store, command: AcceptanceCommand) -> Result<()> {
    match command {
        AcceptanceCommand::Seed { include_unfinished } => {
            let run = store.load()?;
            if !include_unfinished && run.state != RunState::Done {
                bail!(
                    "active run is not Done; pass --include-unfinished to seed provisional sends"
                );
            }
            let mut ledger = store.load_acceptance_ledger()?;
            let seeded = ledger.upsert_from_run(&run);
            store.save_acceptance_ledger(&ledger)?;
            store.append_acceptance_event(
                "seed",
                &serde_json::json!({ "run_id": run.id, "seeded": seeded, "include_unfinished": include_unfinished }),
            )?;
            println!("acceptance ledger seeded: {seeded} new invitations");
        }
        AcceptanceCommand::SeedHistory => {
            let mut ledger = store.load_acceptance_ledger()?;
            let summary = store.seed_acceptance_from_history(&mut ledger)?;
            store.save_acceptance_ledger(&ledger)?;
            store.append_acceptance_event("seed-history", &summary)?;
            println!(
                "acceptance ledger history seeded: {} new invitations from {} run logs ({} sent events scanned)",
                summary.seeded, summary.run_logs, summary.sent_events
            );
        }
        AcceptanceCommand::Export {
            min_age_days,
            max_age_days,
            out,
        } => {
            let ledger = store.load_acceptance_ledger()?;
            let candidates = ledger
                .eligible_for_check(min_age_days, max_age_days)
                .into_iter()
                .map(AcceptanceCheckCandidate::from)
                .collect::<Vec<_>>();
            if let Some(parent) = out.parent() {
                fs::create_dir_all(parent)
                    .with_context(|| format!("creating {}", parent.display()))?;
            }
            fs::write(&out, serde_json::to_string_pretty(&candidates)?)
                .with_context(|| format!("writing {}", out.display()))?;
            store.append_acceptance_event(
                "export",
                &serde_json::json!({
                    "path": out,
                    "min_age_days": min_age_days,
                    "max_age_days": max_age_days,
                    "count": candidates.len(),
                }),
            )?;
            println!(
                "exported {} acceptance-check candidates to {}",
                candidates.len(),
                out.display()
            );
        }
        AcceptanceCommand::Import { path } => {
            let artifact = AcceptanceOutcomeArtifact::from_path(&path)?;
            let mut ledger = store.load_acceptance_ledger()?;
            let summary = ledger.import_outcomes(artifact);
            store.save_acceptance_ledger(&ledger)?;
            store.append_acceptance_event(
                "import",
                &serde_json::json!({ "path": path, "summary": summary }),
            )?;
            println!(
                "imported acceptance outcomes: {} rows, {} matched, {} unmatched",
                summary.rows, summary.matched, summary.unmatched
            );
        }
        AcceptanceCommand::Report {
            min_age_days,
            max_age_days,
            json,
        } => {
            let ledger = store.load_acceptance_ledger()?;
            let report = ledger.report(min_age_days, max_age_days);
            if json {
                println!("{}", serde_json::to_string_pretty(&report)?);
            } else {
                println!("{}", render_acceptance_report(&report));
            }
        }
        AcceptanceCommand::DraftFollowups {
            session,
            playwriter,
            research_script,
            research,
            out,
            out_dir,
            strategy,
            include_drafted,
            no_public_web,
            max_web_results,
            delay_ms,
            playwriter_timeout_ms,
        } => handle_acceptance_draft_followups(
            store,
            session,
            playwriter,
            research_script,
            research,
            out,
            out_dir,
            strategy,
            include_drafted,
            !no_public_web,
            max_web_results,
            delay_ms,
            playwriter_timeout_ms,
        )?,
    }
    Ok(())
}

pub(crate) fn handle_acceptance_draft_followups(
    store: &Store,
    session: Option<String>,
    playwriter: PathBuf,
    research_script: PathBuf,
    research: Option<PathBuf>,
    out: Option<PathBuf>,
    out_dir: PathBuf,
    strategy: DraftStrategy,
    include_drafted: bool,
    public_web: bool,
    max_web_results: u32,
    delay_ms: u64,
    playwriter_timeout_ms: u32,
) -> Result<()> {
    let ledger = store.load_acceptance_ledger()?;
    let mut followups = store.load_acceptance_followup_ledger()?;
    let candidates = ledger.accepted_for_followup(&followups, include_drafted);
    let report_path = out.unwrap_or_else(|| store.default_acceptance_followup_report_path());

    if let Some(parent) = report_path.parent() {
        fs::create_dir_all(parent).with_context(|| format!("creating {}", parent.display()))?;
    }

    let research_path = if candidates.is_empty() {
        research
    } else if let Some(path) = research {
        Some(path)
    } else {
        let session = session
            .ok_or_else(|| anyhow!("--session is required when --research is not provided"))?;
        fs::create_dir_all(&out_dir).with_context(|| format!("creating {}", out_dir.display()))?;
        let candidates_path = out_dir.join("accepted-candidates.json");
        let research_path = out_dir.join("accepted-research.json");
        fs::write(&candidates_path, serde_json::to_string_pretty(&candidates)?)
            .with_context(|| format!("writing {}", candidates_path.display()))?;
        run_playwriter_accepted_research(
            &playwriter,
            &session,
            &research_script,
            &candidates_path,
            &research_path,
            public_web,
            max_web_results,
            delay_ms,
            playwriter_timeout_ms,
        )?;
        Some(research_path)
    };

    let research_artifact = if let Some(path) = &research_path {
        Some(AcceptedResearchArtifact::from_path(path)?)
    } else {
        None
    };
    let report = build_draft_report(
        candidates,
        research_artifact,
        strategy,
        research_path.clone(),
    );
    fs::write(&report_path, render_markdown(&report))
        .with_context(|| format!("writing {}", report_path.display()))?;
    let recorded = followups.record_report(&report, &report_path, research_path.as_deref());
    store.save_acceptance_followup_ledger(&followups)?;
    store.append_acceptance_event(
        "draft-followups",
        &serde_json::json!({
            "report_path": report_path,
            "research_path": research_path,
            "draft_count": report.items.len(),
            "recorded": recorded,
            "strategy": strategy,
            "include_drafted": include_drafted,
            "public_web": public_web,
            "max_web_results": max_web_results,
        }),
    )?;
    println!(
        "accepted follow-up drafts: {} written to {}",
        report.items.len(),
        report_path.display()
    );
    if let Some(path) = research_path {
        println!("research artifact: {}", path.display());
    }
    Ok(())
}

pub(crate) fn handle_reservoir_command(store: &Store, command: ReservoirCommand) -> Result<()> {
    match command {
        ReservoirCommand::Capture {
            session,
            playwriter,
            script,
            saved_searches,
            source,
            url,
            out_dir,
            pages,
            stop_after_connectable,
            limit,
            row_scroll_delay_ms,
            only_connectable,
        } => {
            let session =
                session.ok_or_else(|| anyhow!("--session is required to execute Playwriter"))?;
            let url = resolve_capture_url(url.as_deref(), &saved_searches, &source)?;
            let capture_path = run_playwriter_capture(
                &playwriter,
                &session,
                &script,
                &out_dir,
                &source,
                &url,
                &CaptureRunOptions {
                    pages,
                    stop_after_connectable,
                    limit,
                    row_scroll_delay_ms,
                    only_connectable,
                },
            )?;
            let capture = SalesNavCapture::from_path(&capture_path)?;
            let mut reservoir = store.load_reservoir()?;
            let imported = import_capture_into_reservoir(
                &mut reservoir,
                capture,
                ImportCaptureOptions { only_connectable },
            )?;
            store.save_reservoir(&reservoir)?;
            println!(
                "reservoir captured {imported} candidate observations from {source}; total {}",
                reservoir.observations.len()
            );
        }
        ReservoirCommand::ImportCapture {
            path,
            only_connectable,
        } => {
            let capture = SalesNavCapture::from_path(&path)?;
            let mut reservoir = store.load_reservoir()?;
            let imported = import_capture_into_reservoir(
                &mut reservoir,
                capture,
                ImportCaptureOptions { only_connectable },
            )?;
            store.save_reservoir(&reservoir)?;
            println!(
                "reservoir imported {imported} candidate observations; total {}",
                reservoir.observations.len()
            );
        }
        ReservoirCommand::FillRun { source, limit } => {
            let mut run = store.load()?;
            let mut reservoir = store.load_reservoir()?;
            let source = source
                .or_else(|| run.next_source().map(|next| next.name))
                .ok_or_else(|| anyhow!("no source provided and no active run source available"))?;
            let remaining = run
                .source_quota(&source)
                .unwrap_or_default()
                .saturating_sub(run.source_verified_count(&source));
            let limit = limit.unwrap_or_else(|| remaining.saturating_add(3) as usize);
            let imported = fill_run_from_reservoir(&mut run, &mut reservoir, &source, limit)?;
            store.save(&run)?;
            store.save_reservoir(&reservoir)?;
            store.append_event(
                &run,
                "reservoir-fill-run",
                &serde_json::json!({ "source": source, "imported": imported }),
            )?;
            println!("filled active run with {imported} reservoir candidates");
            if let Some(candidate) = run.next_connectable_observation() {
                println!(
                    "next connectable: {} ({})",
                    candidate.name,
                    candidate
                        .profile_url
                        .as_deref()
                        .unwrap_or("no profile url captured")
                );
            }
        }
        ReservoirCommand::Report { json } => {
            let reservoir = store.load_reservoir()?;
            if json {
                println!("{}", serde_json::to_string_pretty(&reservoir)?);
            } else {
                println!("# LinkedIn Candidate Reservoir");
                println!("- Total candidates: {}", reservoir.observations.len());
                println!("- Updated at: {:?}", reservoir.updated_at);
                let mut by_source: BTreeMap<String, u32> = BTreeMap::new();
                for observation in &reservoir.observations {
                    *by_source.entry(observation.source.clone()).or_default() += 1;
                }
                println!();
                println!("## Source Counts");
                for (source, count) in by_source {
                    println!("- {source}: {count}");
                }
            }
        }
        ReservoirCommand::Clear { source } => {
            let mut reservoir = store.load_reservoir()?;
            let before = reservoir.observations.len();
            if let Some(source) = source {
                reservoir
                    .observations
                    .retain(|observation| observation.source != source);
            } else {
                reservoir.observations.clear();
            }
            reservoir.updated_at = Some(Local::now());
            store.save_reservoir(&reservoir)?;
            println!(
                "removed {} reservoir candidates",
                before.saturating_sub(reservoir.observations.len())
            );
        }
    }
    Ok(())
}

pub(crate) fn handle_tune_sources(
    store: &Store,
    min_raw_rows: u32,
    max_connectable_yield: f64,
    apply: bool,
) -> Result<()> {
    let mut run = store.load()?;
    let stats = source_yield_report(&run);
    println!("# Source Yield");
    for item in &stats {
        let yield_text = item
            .connectable_yield
            .map(|value| format!("{:.1}%", value * 100.0))
            .unwrap_or_else(|| "n/a".to_string());
        println!(
            "- {}: {} connectable / {} rows ({yield_text}); pending {}, email-required skips {}, recommendation: {}",
            item.source,
            item.connectable_count,
            item.raw_row_count,
            item.pending_sends,
            item.email_required_skips,
            item.recommendation
        );
    }

    let low_yield = low_yield_source_names(&run, min_raw_rows, max_connectable_yield);
    if low_yield.is_empty() {
        println!("no source met the low-yield threshold");
        return Ok(());
    }
    println!("low-yield sources: {}", low_yield.join(", "));
    if apply {
        for source in &low_yield {
            if let Some(plan) = run.sources.iter_mut().find(|plan| &plan.name == source) {
                plan.exhausted = true;
            }
            run.notes.push(format!(
                "source tuned low-yield: {source}; threshold raw>={min_raw_rows}, connectable_yield<={max_connectable_yield:.3}"
            ));
        }
        run.mark_updated();
        store.save(&run)?;
        store.append_event(
            &run,
            "tune-sources",
            &serde_json::json!({
                "min_raw_rows": min_raw_rows,
                "max_connectable_yield": max_connectable_yield,
                "exhausted": low_yield,
            }),
        )?;
        println!("marked low-yield sources exhausted");
    } else {
        println!("dry run only; pass --apply to mark low-yield sources exhausted");
    }
    Ok(())
}

pub(crate) fn handle_pending_cleanup_command(
    store: &Store,
    command: PendingCleanupCommand,
) -> Result<()> {
    match command {
        PendingCleanupCommand::Start {
            max_withdrawals,
            threshold_months,
            date,
            force,
        } => {
            let date = date.unwrap_or_else(|| Local::now().date_naive());
            if store.pending_active_path().exists() && !force {
                bail!("an active pending-cleanup run already exists; use --force to replace it");
            }
            let run = PendingCleanupRun::new(max_withdrawals, threshold_months, date);
            store.save_pending(&run)?;
            store.append_pending_event(
                &run,
                "start",
                &serde_json::json!({
                    "max_withdrawals": max_withdrawals,
                    "threshold_months": threshold_months,
                }),
            )?;
            println!(
                "started pending cleanup {} for {}; cap {}, threshold {} months",
                run.id, date, max_withdrawals, threshold_months
            );
        }
        PendingCleanupCommand::ImportAudit { path } => {
            let mut run = store.load_pending()?;
            let audit = SalesNavAudit::from_path(&path)?;
            apply_pending_audit(
                &mut run,
                audit.people_count,
                Some(format!(
                    "imported audit; recent_names={}",
                    audit.recent_names.join(", ")
                )),
            );
            store.save_pending(&run)?;
            store.append_pending_event(
                &run,
                "import-audit",
                &serde_json::json!({ "path": path, "people_count": audit.people_count }),
            )?;
            println!(
                "pending audit imported: People ({}){}",
                audit.people_count,
                run.audited_delta()
                    .map(|delta| format!(", audited delta {delta}"))
                    .unwrap_or_default()
            );
        }
        PendingCleanupCommand::ImportCapture { path } => {
            let mut run = store.load_pending()?;
            let capture = PendingCapture::from_path(&path)?;
            let imported = import_pending_capture(&mut run, capture)?;
            run.state = PendingCleanupState::Withdrawing;
            run.mark_updated();
            store.save_pending(&run)?;
            store.append_pending_event(
                &run,
                "import-capture",
                &serde_json::json!({ "path": path, "imported": imported }),
            )?;
            println!("imported {imported} pending invitation observations");
            if let Some(candidate) = run.next_eligible_observation() {
                println!(
                    "next stale invitation: {} ({})",
                    candidate.name, candidate.age_text
                );
            } else {
                println!("no unrecorded eligible stale invitation in imported capture");
            }
        }
        PendingCleanupCommand::Plan { json } => {
            let run = store.load_pending()?;
            let plan = run.operator_plan();
            if json {
                println!("{}", serde_json::to_string_pretty(&plan)?);
            } else {
                print_pending_plan(&plan);
            }
        }
        PendingCleanupCommand::Next { json } => {
            let run = store.load_pending()?;
            let candidate = run.next_eligible_observation();
            if json {
                println!("{}", serde_json::to_string_pretty(&candidate)?);
            } else if let Some(candidate) = candidate {
                println!("name: {}", candidate.name);
                println!("age_text: {}", candidate.age_text);
                println!("profile_url: {:?}", candidate.profile_url);
            } else {
                println!("no unrecorded eligible stale invitation available");
            }
        }
        PendingCleanupCommand::RecordWithdrawResult { path } => {
            let mut run = store.load_pending()?;
            let result = PendingWithdrawResult::from_path(&path)?;
            let event = record_pending_withdraw_result(&mut run, result, path.clone())?;
            store.save_pending(&run)?;
            store.append_pending_event(
                &run,
                "record-withdraw-result",
                &serde_json::json!({ "path": path, "event": event }),
            )?;
            println!(
                "recorded withdraw result as {:?}; withdrawn {}/{}",
                event.status,
                run.withdrawn_count(),
                run.max_withdrawals
            );
        }
        PendingCleanupCommand::WithdrawNext {
            session,
            playwriter,
            script,
            out_dir,
            dry_run,
            allow_withdraw,
            no_record,
        } => {
            let run = store.load_pending()?;
            if allow_withdraw && run.withdraw_capacity_remaining() == 0 {
                bail!(
                    "withdrawal cap reached: {}/{} withdrawals",
                    run.withdrawn_count(),
                    run.max_withdrawals
                );
            }
            let candidate = run
                .next_eligible_observation()
                .ok_or_else(|| anyhow!("no unrecorded eligible stale invitation available"))?;
            let session =
                session.ok_or_else(|| anyhow!("--session is required to execute Playwriter"))?;
            fs::create_dir_all(&out_dir)
                .with_context(|| format!("creating {}", out_dir.display()))?;
            let candidate_path = out_dir.join("pending-candidate.json");
            let result_path = out_dir.join("withdraw-result.json");
            fs::write(&candidate_path, serde_json::to_string_pretty(candidate)?)
                .with_context(|| format!("writing {}", candidate_path.display()))?;
            let config_js = format!(
                "state.salesNavPendingWithdrawConfig = {{ out: {}, dryRun: {}, allowWithdraw: {}, candidate: JSON.parse(require('node:fs').readFileSync({}, 'utf8')) }}; console.log(JSON.stringify(state.salesNavPendingWithdrawConfig));",
                serde_json::to_string(result_path.to_str().unwrap_or_default())?,
                dry_run || !allow_withdraw,
                allow_withdraw,
                serde_json::to_string(candidate_path.to_str().unwrap_or_default())?
            );
            run_playwriter_config(&playwriter, &session, &config_js)?;
            run_playwriter_file(&playwriter, &session, &script)?;
            println!("withdraw result: {}", result_path.display());
            if allow_withdraw && !dry_run && !no_record {
                let mut run = store.load_pending()?;
                let result = PendingWithdrawResult::from_path(&result_path)?;
                record_pending_withdraw_result(&mut run, result, result_path.clone())?;
                store.save_pending(&run)?;
                store.append_pending_event(
                    &run,
                    "record-withdraw-result",
                    &serde_json::json!({ "path": result_path }),
                )?;
                println!(
                    "recorded withdraw result; withdrawn {}/{}",
                    run.withdrawn_count(),
                    run.max_withdrawals
                );
            }
        }
        PendingCleanupCommand::Status { json } => {
            let run = store.load_pending()?;
            if json {
                println!("{}", serde_json::to_string_pretty(&run)?);
            } else {
                print_pending_status(&run);
            }
        }
        PendingCleanupCommand::Report => {
            let run = store.load_pending()?;
            println!("{}", render_pending_report(&run));
        }
        PendingCleanupCommand::Finish { force } => {
            let mut run = store.load_pending()?;
            let expected_delta = -i64::from(run.withdrawn_count());
            let delta = run.audited_delta();
            if !force && delta != Some(expected_delta) {
                bail!(
                    "final audit delta is {:?}, expected {}; import a fresh audit or use --force",
                    delta,
                    expected_delta
                );
            }
            run.state = PendingCleanupState::Done;
            run.mark_updated();
            store.save_pending(&run)?;
            store.append_pending_event(
                &run,
                "finish",
                &serde_json::json!({ "audited_delta": delta }),
            )?;
            println!("{}", render_pending_report(&run));
        }
    }
    Ok(())
}
