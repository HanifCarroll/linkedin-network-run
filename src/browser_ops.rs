use crate::*;

pub(crate) fn run_playwriter_send(
    playwriter: &PathBuf,
    session: &str,
    script: &PathBuf,
    out_dir: &PathBuf,
    candidate: &CandidateObservation,
    dry_run: bool,
    allow_send: bool,
) -> Result<PathBuf> {
    fs::create_dir_all(out_dir).with_context(|| format!("creating {}", out_dir.display()))?;
    let candidate_path = out_dir.join("next-candidate.json");
    let result_path = out_dir.join("send-result.json");
    fs::write(&candidate_path, serde_json::to_string_pretty(candidate)?)
        .with_context(|| format!("writing {}", candidate_path.display()))?;
    let config_js = format!(
        "state.salesNavSendConfig = {{ out: {}, dryRun: {}, allowSend: {}, candidate: JSON.parse(require('node:fs').readFileSync({}, 'utf8')) }}; console.log(JSON.stringify(state.salesNavSendConfig));",
        serde_json::to_string(result_path.to_str().unwrap_or_default())?,
        dry_run || !allow_send,
        allow_send,
        serde_json::to_string(candidate_path.to_str().unwrap_or_default())?
    );
    run_playwriter_config(playwriter, session, &config_js)?;
    run_playwriter_file_with_timeout(playwriter, session, script, 90_000)?;
    Ok(result_path)
}

pub(crate) fn handle_send_guarded(
    store: &Store,
    session: Option<String>,
    playwriter: PathBuf,
    script: PathBuf,
    out_dir: PathBuf,
    max_attempts: u32,
    dry_run: bool,
    single_pass: bool,
    allow_send: bool,
    no_record: bool,
) -> Result<()> {
    if !dry_run && !allow_send {
        bail!("real guarded sends require --allow-send");
    }
    let session = session.ok_or_else(|| anyhow!("--session is required to execute Playwriter"))?;
    let mut run = store.load()?;
    if run.state == RunState::NeedsReaudit {
        bail!("run is in NEEDS_REAUDIT; record a fresh sent-page audit before sending");
    }
    let source = run
        .next_source()
        .ok_or_else(|| anyhow!("no active source available for guarded send"))?
        .name;

    let mut attempts = 0_u32;
    loop {
        run = store.load()?;
        if run.state == RunState::NeedsReaudit {
            bail!("run entered NEEDS_REAUDIT; import a fresh audit before continuing");
        }
        let drained = drain_stale_connectable_candidates(&mut run, None)?;
        let drained_count = drained.len();
        if drained_count > 0 {
            store.save(&run)?;
            store.append_event(
                &run,
                "drain-stale-candidates",
                &serde_json::json!({ "events": drained }),
            )?;
            println!("auto-skipped {drained_count} stale queued candidates");
        }

        let Some(next_source) = run.next_source() else {
            println!("no active source remains; run final audit or inspect plan");
            break;
        };
        if next_source.name != source {
            println!(
                "guarded source complete: {}; next source is {}",
                source, next_source.name
            );
            break;
        }
        if run.real_send_capacity_remaining() == 0 {
            bail!(
                "real-send cap reached: {}/{} verified sends",
                run.verified_count(),
                run.max_real_sends
            );
        }
        if attempts >= max_attempts {
            println!("guarded send stopped after {attempts} attempts");
            break;
        }
        let Some(candidate) = run
            .next_connectable_observation_for_source(&source)
            .cloned()
        else {
            println!("no unrecorded connectable candidate available for {source}; capture more");
            break;
        };
        attempts += 1;
        println!("guarded attempt {attempts}: {} ({source})", candidate.name);

        if dry_run || !single_pass {
            let attempt_dir = out_dir.join(format!("attempt-{attempts:02}-dry-run"));
            let dry_started = Instant::now();
            let dry_result_path = run_playwriter_send(
                &playwriter,
                &session,
                &script,
                &attempt_dir,
                &candidate,
                true,
                false,
            )?;
            let dry_result = SalesNavSendResult::from_path(&dry_result_path)?;
            println!("dry-run status: {}", dry_result.status);
            if dry_result.status != "dry-run-connectable" {
                if !no_record {
                    run = store.load()?;
                    let event = record_send_result(&mut run, dry_result, dry_result_path.clone())?;
                    push_timing(
                        &mut run,
                        "send-guarded-dry-run",
                        Some(event.source.clone()),
                        dry_started,
                        Some(format!(
                            "attempt={attempts}; status={:?}; path={}",
                            event.status,
                            dry_result_path.display()
                        )),
                    );
                    store.save(&run)?;
                    store.append_event(
                        &run,
                        "record-send-result",
                        &serde_json::json!({ "path": dry_result_path, "event": event }),
                    )?;
                    println!("recorded dry-run result as {:?}", event.status);
                }
                continue;
            }
            if dry_run {
                println!("dry run confirmed next guarded candidate; no real send performed");
                break;
            }
        } else {
            println!(
                "single-pass Playwriter send: sender validates Connect before clicking and Pending after sending"
            );
        }

        run = store.load()?;
        let still_active = run.next_source().is_some_and(|next| next.name == source);
        if !still_active {
            println!("source reached target before real send; stopped before candidate");
            break;
        }
        let attempt_dir = out_dir.join(format!("attempt-{attempts:02}-send"));
        let send_started = Instant::now();
        let result_path = run_playwriter_send(
            &playwriter,
            &session,
            &script,
            &attempt_dir,
            &candidate,
            false,
            true,
        )?;
        let result = SalesNavSendResult::from_path(&result_path)?;
        let status = result.status.clone();
        println!("send status: {status}");
        if !no_record {
            run = store.load()?;
            let event = record_send_result(&mut run, result, result_path.clone())?;
            let drained = drain_stale_connectable_candidates(&mut run, None)?;
            let drained_count = drained.len();
            if is_uncertain_send_status(&status) {
                run.state = RunState::NeedsReaudit;
                run.notes.push(format!(
                    "guarded send stopped after uncertain status for {}: {status}",
                    event.name
                ));
            }
            push_timing(
                &mut run,
                "send-guarded",
                Some(event.source.clone()),
                send_started,
                Some(format!(
                    "attempt={attempts}; status={}; path={}",
                    status,
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
                println!("auto-skipped {drained_count} stale queued candidates");
            }
            if is_uncertain_send_status(&status) {
                bail!(
                    "guarded send stopped on uncertain status {status}; import a fresh sent-page audit before continuing"
                );
            }
        } else {
            println!("--no-record set; stopped after one real guarded send");
            break;
        }
    }

    let run = store.load()?;
    println!("{}", render_report(&run));
    Ok(())
}

pub(crate) fn run_playwriter_audit(
    playwriter: &PathBuf,
    session: &str,
    script: &PathBuf,
    out_path: &PathBuf,
) -> Result<()> {
    if let Some(parent) = out_path.parent() {
        fs::create_dir_all(parent).with_context(|| format!("creating {}", parent.display()))?;
    }
    let config_js = format!(
        "state.salesNavAuditConfig = {{ out: {}, loadMore: 0 }}; console.log(JSON.stringify(state.salesNavAuditConfig));",
        serde_json::to_string(out_path.to_str().unwrap_or_default())?
    );
    run_playwriter_config(playwriter, session, &config_js)?;
    run_playwriter_file(playwriter, session, script)?;
    Ok(())
}

pub(crate) fn run_playwriter_capture(
    playwriter: &PathBuf,
    session: &str,
    script: &PathBuf,
    out_dir: &PathBuf,
    source: &str,
    url: &str,
    options: &CaptureRunOptions,
) -> Result<PathBuf> {
    fs::create_dir_all(out_dir).with_context(|| format!("creating {}", out_dir.display()))?;
    let config_js = format!(
        concat!(
            "state.salesNavCaptureConfig = {{ ",
            "out: {}, source: {}, url: {}, limit: {}, pages: {}, ",
            "stopAfterConnectable: {}, rowScrollDelayMs: {}, ",
            "openMenus: true, onlyConnectable: {}, saveHtml: false ",
            "}}; console.log(JSON.stringify(state.salesNavCaptureConfig));"
        ),
        serde_json::to_string(out_dir.to_str().unwrap_or_default())?,
        serde_json::to_string(source)?,
        serde_json::to_string(url)?,
        options.limit,
        options.pages,
        options.stop_after_connectable,
        options.row_scroll_delay_ms,
        options.only_connectable
    );
    run_playwriter_config(playwriter, session, &config_js)?;
    run_playwriter_file_with_timeout(playwriter, session, script, 90_000)?;
    Ok(out_dir.join("page.json"))
}

pub(crate) fn run_playwriter_accepted_research(
    playwriter: &PathBuf,
    session: &str,
    script: &PathBuf,
    candidates_path: &Path,
    out_path: &Path,
    public_web: bool,
    max_web_results: u32,
    delay_ms: u64,
    timeout_ms: u32,
) -> Result<()> {
    if let Some(parent) = out_path.parent() {
        fs::create_dir_all(parent).with_context(|| format!("creating {}", parent.display()))?;
    }
    let config_js = format!(
        concat!(
            "state.salesNavAcceptedResearchConfig = {{ ",
            "in: {}, out: {}, publicWeb: {}, maxWebResults: {}, delayMs: {} ",
            "}}; console.log(JSON.stringify(state.salesNavAcceptedResearchConfig));"
        ),
        serde_json::to_string(candidates_path.to_str().unwrap_or_default())?,
        serde_json::to_string(out_path.to_str().unwrap_or_default())?,
        public_web,
        max_web_results,
        delay_ms
    );
    run_playwriter_config(playwriter, session, &config_js)?;
    run_playwriter_file_with_timeout(playwriter, session, script, timeout_ms)?;
    Ok(())
}

pub(crate) fn resolve_saved_search_url(path: &Path, source: &str) -> Result<Option<String>> {
    if !path.exists() {
        return Ok(None);
    }
    let raw = fs::read_to_string(path)
        .with_context(|| format!("reading saved searches {}", path.display()))?;
    let value: serde_json::Value = serde_json::from_str(&raw)
        .with_context(|| format!("parsing saved searches {}", path.display()))?;
    let searches = value
        .get("searches")
        .or_else(|| value.get("savedSearches"))
        .and_then(|value| value.as_array())
        .ok_or_else(|| {
            anyhow!(
                "saved searches artifact has no searches array: {}",
                path.display()
            )
        })?;
    Ok(searches.iter().find_map(|row| {
        let name = row.get("name").and_then(|value| value.as_str())?;
        if name != source {
            return None;
        }
        row.get("viewUrl")
            .or_else(|| row.get("view_url"))
            .and_then(|value| value.as_str())
            .map(ToString::to_string)
    }))
}

pub(crate) fn resolve_capture_url(
    explicit_url: Option<&str>,
    saved_searches: &Path,
    source: &str,
) -> Result<String> {
    if let Some(url) = explicit_url.filter(|url| !url.trim().is_empty()) {
        return Ok(url.to_string());
    }
    resolve_saved_search_url(saved_searches, source)?
        .ok_or_else(|| anyhow!("no URL for source {source}; pass --url/--fallback-url or resolve saved searches into {}", saved_searches.display()))
}

pub(crate) fn handle_reconcile_audit(
    store: &Store,
    session: Option<String>,
    playwriter: PathBuf,
    script: PathBuf,
    out_dir: PathBuf,
    attempts: u32,
    delay_ms: u64,
    finish: bool,
) -> Result<()> {
    let session = session.ok_or_else(|| anyhow!("--session is required to execute Playwriter"))?;
    let attempts = attempts.max(1);
    let mut latest_delta = None;
    for attempt in 1..=attempts {
        let started = Instant::now();
        let out_path = out_dir.join(format!("audit-{attempt:02}.json"));
        run_playwriter_audit(&playwriter, &session, &script, &out_path)?;
        let audit = SalesNavAudit::from_path(&out_path)?;
        let mut run = store.load()?;
        apply_audit(
            &mut run,
            audit.people_count,
            Some(format!("reconcile audit attempt {attempt}/{attempts}")),
        );
        latest_delta = run.audited_delta();
        let should_finish = finish && latest_delta == Some(i64::from(run.target));
        if should_finish {
            run.state = RunState::Done;
        }
        push_timing(
            &mut run,
            "reconcile-audit",
            None,
            started,
            Some(format!(
                "attempt {attempt}/{attempts}; people {}",
                audit.people_count
            )),
        );
        store.save(&run)?;
        store.append_event(
            &run,
            "reconcile-audit",
            &serde_json::json!({
                "attempt": attempt,
                "path": out_path,
                "people_count": audit.people_count,
                "delta": latest_delta,
                "finished": should_finish,
            }),
        )?;
        if should_finish {
            store.append_event(
                &run,
                "finish",
                &serde_json::json!({ "audited_delta": latest_delta }),
            )?;
        }
        println!(
            "reconcile audit {attempt}/{attempts}: People ({}), delta {:?}",
            audit.people_count, latest_delta
        );
        if latest_delta == Some(i64::from(run.target)) {
            break;
        }
        if attempt < attempts {
            sleep(Duration::from_millis(delay_ms));
        }
    }
    if finish && latest_delta.is_some() {
        let run = store.load()?;
        if run.state != RunState::Done && latest_delta != Some(i64::from(run.target)) {
            bail!(
                "final audit delta is {:?}, expected {}; top up or re-run reconcile-audit",
                latest_delta,
                run.target
            );
        }
    }
    let run = store.load()?;
    println!("{}", render_report(&run));
    Ok(())
}

pub(crate) fn handle_top_up_reconcile(
    store: &Store,
    session: Option<String>,
    playwriter: PathBuf,
    send_script: PathBuf,
    audit_script: PathBuf,
    fallback: TopUpFallbackOptions,
    out_dir: PathBuf,
    max_attempts: u32,
    delay_ms: u64,
    allow_send: bool,
    finish: bool,
) -> Result<()> {
    if !allow_send {
        bail!("top-up reconciliation can send real invites; pass --allow-send to continue");
    }
    let session = session.ok_or_else(|| anyhow!("--session is required to execute Playwriter"))?;
    fs::create_dir_all(&out_dir).with_context(|| format!("creating {}", out_dir.display()))?;
    let max_attempts = max_attempts.max(1);

    for attempt in 1..=max_attempts {
        let run = store.load()?;
        if run.audited_delta() == Some(i64::from(run.target)) {
            if finish && run.state != RunState::Done {
                let mut run = run;
                run.state = RunState::Done;
                run.mark_updated();
                store.save(&run)?;
                store.append_event(
                    &run,
                    "finish",
                    &serde_json::json!({ "audited_delta": run.audited_delta(), "via": "top-up-reconcile" }),
                )?;
            }
            println!("audited delta already matches target; no top-up needed");
            break;
        }
        if run
            .audited_delta()
            .is_some_and(|delta| delta > i64::from(run.target))
        {
            bail!(
                "audited delta {:?} already exceeds target {}; stopping",
                run.audited_delta(),
                run.target
            );
        }
        if run.verified_count() < run.target {
            bail!(
                "row-level verified sends are {}/{}; continue normal guarded sends before audit top-up",
                run.verified_count(),
                run.target
            );
        }
        let candidate = match run.next_top_up_observation().cloned() {
            Some(candidate) => candidate,
            None => prepare_top_up_candidate(
                store,
                &playwriter,
                &session,
                &out_dir,
                &fallback,
                attempt,
            )?
            .ok_or_else(|| anyhow!("no distinct connectable candidate available for top-up"))?,
        };
        println!(
            "top-up attempt {attempt}/{max_attempts}: {} ({})",
            candidate.name, candidate.source
        );

        let send_started = Instant::now();
        let send_dir = out_dir.join(format!("attempt-{attempt:02}-send"));
        let result_path = run_playwriter_send(
            &playwriter,
            &session,
            &send_script,
            &send_dir,
            &candidate,
            false,
            true,
        )?;
        let result = SalesNavSendResult::from_path(&result_path)?;
        let status = result.status.clone();
        let mut run = store.load()?;
        let event = record_top_up_send_result(
            &mut run,
            result,
            result_path.clone(),
            Some("controller top-up reconciliation".to_string()),
        )?;
        push_timing(
            &mut run,
            "top-up-send",
            Some(candidate.source.clone()),
            send_started,
            Some(format!("attempt {attempt}; status {status}")),
        );
        store.save(&run)?;
        store.append_event(
            &run,
            "record-top-up-result",
            &serde_json::json!({ "path": result_path, "event": event, "via": "top-up-reconcile" }),
        )?;
        println!("top-up send status: {status}");

        if event.status != CandidateStatus::AuditTopUp {
            println!("top-up did not send a verified invite; trying next distinct candidate");
            continue;
        }

        if delay_ms > 0 {
            sleep(Duration::from_millis(delay_ms));
        }
        let audit_started = Instant::now();
        let audit_path = out_dir.join(format!("attempt-{attempt:02}-audit.json"));
        run_playwriter_audit(&playwriter, &session, &audit_script, &audit_path)?;
        let audit = SalesNavAudit::from_path(&audit_path)?;
        let mut run = store.load()?;
        apply_audit(
            &mut run,
            audit.people_count,
            Some(format!(
                "top-up reconcile audit attempt {attempt}/{max_attempts}"
            )),
        );
        push_timing(
            &mut run,
            "top-up-audit",
            None,
            audit_started,
            Some(format!("attempt {attempt}; people {}", audit.people_count)),
        );
        let latest_delta = run.audited_delta();
        let should_finish = finish && latest_delta == Some(i64::from(run.target));
        if should_finish {
            run.state = RunState::Done;
        }
        store.save(&run)?;
        store.append_event(
            &run,
            "top-up-reconcile-audit",
            &serde_json::json!({
                "attempt": attempt,
                "path": audit_path,
                "people_count": audit.people_count,
                "delta": latest_delta,
                "finished": should_finish,
            }),
        )?;
        if should_finish {
            store.append_event(
                &run,
                "finish",
                &serde_json::json!({ "audited_delta": latest_delta, "via": "top-up-reconcile" }),
            )?;
        }
        println!(
            "top-up audit {attempt}/{max_attempts}: People ({}), delta {:?}",
            audit.people_count, latest_delta
        );
        if latest_delta == Some(i64::from(run.target)) {
            break;
        }
    }

    let run = store.load()?;
    if finish && run.state != RunState::Done {
        bail!(
            "final audit delta is {:?}, expected {}; top-up reconciliation did not finish",
            run.audited_delta(),
            run.target
        );
    }
    println!("{}", render_report(&run));
    Ok(())
}

pub(crate) fn prepare_top_up_candidate(
    store: &Store,
    playwriter: &PathBuf,
    session: &str,
    out_dir: &Path,
    fallback: &TopUpFallbackOptions,
    attempt: u32,
) -> Result<Option<CandidateObservation>> {
    let run = store.load()?;
    if !run.final_audit_is_short() {
        return Ok(None);
    }
    ensure_known_source(&run, &fallback.source)?;
    if !run.source_is_fallback(&fallback.source) {
        bail!(
            "top-up fallback source is not marked fallback: {}",
            fallback.source
        );
    }

    let mut reservoir = store.load_reservoir()?;
    let mut run = run;
    let reservoir_imported = fill_run_from_reservoir_for_top_up(
        &mut run,
        &mut reservoir,
        &fallback.source,
        fallback.limit as usize,
    )?;
    if reservoir_imported > 0 {
        store.save(&run)?;
        store.save_reservoir(&reservoir)?;
        store.append_event(
            &run,
            "top-up-reservoir-fill",
            &serde_json::json!({
                "source": fallback.source,
                "imported": reservoir_imported,
            }),
        )?;
        println!(
            "filled final top-up queue from reservoir: {} candidates from {}",
            reservoir_imported, fallback.source
        );
        return Ok(run.next_top_up_observation().cloned());
    }

    if !fallback.capture_enabled {
        return Ok(None);
    }

    let url = resolve_capture_url(
        fallback.url.as_deref(),
        &fallback.saved_searches,
        &fallback.source,
    )?;
    let capture_dir = out_dir.join(format!("attempt-{attempt:02}-fallback-capture"));
    let capture_started = Instant::now();
    let capture_path = run_playwriter_capture(
        playwriter,
        session,
        &fallback.capture_script,
        &capture_dir,
        &fallback.source,
        &url,
        &CaptureRunOptions {
            pages: fallback.pages,
            stop_after_connectable: fallback.stop_after_connectable,
            limit: fallback.limit,
            row_scroll_delay_ms: fallback.row_scroll_delay_ms,
            only_connectable: true,
        },
    )?;
    let capture = SalesNavCapture::from_path(&capture_path)?;
    let mut run = store.load()?;
    let imported = import_capture(
        &mut run,
        capture,
        ImportCaptureOptions {
            only_connectable: true,
        },
    )?;
    push_timing(
        &mut run,
        "top-up-fallback-capture",
        Some(fallback.source.clone()),
        capture_started,
        Some(format!(
            "imported={imported}; path={}",
            capture_path.display()
        )),
    );
    run.mark_updated();
    store.save(&run)?;
    store.append_event(
        &run,
        "top-up-fallback-capture",
        &serde_json::json!({
            "source": fallback.source,
            "path": capture_path,
            "imported": imported,
        }),
    )?;
    println!(
        "captured fallback top-up queue: {} candidates from {}",
        imported, fallback.source
    );
    Ok(run.next_top_up_observation().cloned())
}
