use super::*;

fn connectable_observation(source: &str, name: &str, url: &str) -> CandidateObservation {
    CandidateObservation {
        imported_at: Local::now(),
        captured_at: None,
        source: source.to_string(),
        index: 0,
        name: name.to_string(),
        profile_url: Some(url.to_string()),
        sales_profile_urn: None,
        visible_state: serde_json::Value::Null,
        menu_state: "connectable".to_string(),
        menu_labels: vec!["Connect".to_string()],
        row_html_path: None,
    }
}

fn completed_short_audit_run() -> Run {
    let mut run = Run::new(1, NaiveDate::from_ymd_opt(2026, 6, 13).unwrap());
    run.start_audit = Some(100);
    run.latest_audit = Some(100);
    run.state = RunState::FinalReconcile;
    run.candidates.push(CandidateEvent {
        at: Local::now(),
        source: "ASAP - Startup CTO Eng Leaders".to_string(),
        name: "Primary Sent".to_string(),
        profile_url: Some("https://www.linkedin.com/sales/lead/primary".to_string()),
        status: CandidateStatus::Pending,
        note: None,
    });
    run
}

#[test]
fn default_mix_matches_current_30_request_contract() {
    let sources = default_sources(30);
    let primary_total: u32 = sources
        .iter()
        .filter(|source| !source.fallback)
        .map(|source| source.target)
        .sum();
    assert_eq!(primary_total, 30);
    assert_eq!(sources[0].name, "ASAP - Agency Owners Delivery");
    assert_eq!(sources[0].target, 9);
    assert_eq!(sources[1].name, "ASAP - Contract Recruiters Staffing");
    assert_eq!(sources[1].target, 7);
    assert_eq!(sources[2].name, "ASAP - Startup CTO Eng Leaders");
    assert_eq!(sources[2].target, 6);
    assert_eq!(sources[3].name, "ASAP - High-Intent SaaS AI Founders");
    assert_eq!(sources[3].target, 5);
    assert_eq!(sources[4].name, "ASAP - Vertical Proof Buyers");
    assert_eq!(sources[4].target, 3);
    assert_eq!(sources[5].name, "FO - Founders - Urgent");
}

#[test]
fn exhausted_source_carries_remaining_into_next_source() {
    let mut run = Run::new(30, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    run.candidates.push(CandidateEvent {
        at: Local::now(),
        source: "ASAP - Agency Owners Delivery".to_string(),
        name: "A".to_string(),
        profile_url: None,
        status: CandidateStatus::Pending,
        note: None,
    });
    run.sources[0].exhausted = true;
    let next = run.next_source().unwrap();
    assert_eq!(next.name, "ASAP - Contract Recruiters Staffing");
    assert_eq!(next.quota, 15);
}

#[test]
fn audited_delta_uses_sent_people_count() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    run.start_audit = Some(913);
    run.latest_audit = Some(936);
    assert_eq!(run.audited_delta(), Some(23));
}

#[test]
fn needs_reaudit_blocks_next_source() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    run.state = RunState::NeedsReaudit;
    assert!(run.next_source().is_none());
}

#[test]
fn import_capture_exposes_next_connectable_candidate() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    let capture = SalesNavCapture {
        captured_at: Some("2026-05-26T12:00:00Z".to_string()),
        source: Some("ASAP - Agency Owners Delivery".to_string()),
        url: None,
        resume_url: None,
        page: None,
        pages: Vec::new(),
        state_counts: BTreeMap::new(),
        raw_row_count: None,
        output_row_count: None,
        rows: vec![
            SalesNavCaptureRow {
                index: 0,
                name: Some("Already Pending".to_string()),
                profile_url: Some("https://www.linkedin.com/sales/lead/a".to_string()),
                scroll_urn: None,
                visible_state: None,
                menu_state: Some("already-pending".to_string()),
                menu_labels: Some(vec![SalesNavCaptureMenuLabel {
                    text: Some("Connect - Pending".to_string()),
                    aria: None,
                }]),
                row_html_path: None,
            },
            SalesNavCaptureRow {
                index: 1,
                name: Some("Connectable Founder".to_string()),
                profile_url: Some("https://www.linkedin.com/sales/lead/b".to_string()),
                scroll_urn: None,
                visible_state: None,
                menu_state: Some("connectable".to_string()),
                menu_labels: Some(vec![SalesNavCaptureMenuLabel {
                    text: Some("Connect".to_string()),
                    aria: None,
                }]),
                row_html_path: None,
            },
        ],
    };

    let imported = import_capture(&mut run, capture, ImportCaptureOptions::default()).unwrap();

    assert_eq!(imported, 2);
    assert_eq!(
        run.next_connectable_observation().unwrap().name,
        "Connectable Founder"
    );
}

#[test]
fn import_capture_can_filter_to_connectable_rows() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    let capture = SalesNavCapture {
        captured_at: None,
        source: Some("ASAP - Agency Owners Delivery".to_string()),
        url: None,
        resume_url: None,
        page: None,
        pages: Vec::new(),
        state_counts: BTreeMap::new(),
        raw_row_count: None,
        output_row_count: None,
        rows: vec![
            SalesNavCaptureRow {
                index: 0,
                name: Some("Already Pending".to_string()),
                profile_url: Some("https://www.linkedin.com/sales/lead/a".to_string()),
                scroll_urn: None,
                visible_state: None,
                menu_state: Some("already-pending".to_string()),
                menu_labels: None,
                row_html_path: None,
            },
            SalesNavCaptureRow {
                index: 1,
                name: Some("Connectable Founder".to_string()),
                profile_url: Some("https://www.linkedin.com/sales/lead/b".to_string()),
                scroll_urn: None,
                visible_state: None,
                menu_state: Some("connectable".to_string()),
                menu_labels: None,
                row_html_path: None,
            },
        ],
    };

    let imported = import_capture(
        &mut run,
        capture,
        ImportCaptureOptions {
            only_connectable: true,
        },
    )
    .unwrap();

    assert_eq!(imported, 1);
    assert_eq!(run.observations.len(), 1);
    assert_eq!(run.observations[0].name, "Connectable Founder");
}

#[test]
fn import_capture_derives_profile_url_from_sales_profile_urn() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    let capture = SalesNavCapture {
        captured_at: None,
        source: Some("ASAP - Agency Owners Delivery".to_string()),
        url: None,
        resume_url: None,
        page: None,
        pages: Vec::new(),
        state_counts: BTreeMap::new(),
        raw_row_count: None,
        output_row_count: None,
        rows: vec![SalesNavCaptureRow {
            index: 0,
            name: Some("Connectable Founder".to_string()),
            profile_url: None,
            scroll_urn: Some(
                "urn:li:fs_salesProfile:(ACwAAACZuNoBDnWZnoEzJVGp-uptyWQSfIw87UM,NAME_SEARCH,HDgt)"
                    .to_string(),
            ),
            visible_state: None,
            menu_state: Some("connectable".to_string()),
            menu_labels: None,
            row_html_path: None,
        }],
    };

    import_capture(
        &mut run,
        capture,
        ImportCaptureOptions {
            only_connectable: true,
        },
    )
    .unwrap();

    assert_eq!(
        run.observations[0].profile_url.as_deref(),
        Some(
            "https://www.linkedin.com/sales/lead/ACwAAACZuNoBDnWZnoEzJVGp-uptyWQSfIw87UM,NAME_SEARCH,HDgt"
        )
    );
}

#[test]
fn import_capture_dedupes_sales_nav_urls_with_tracking_params() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    let capture = SalesNavCapture {
        captured_at: None,
        source: Some("ASAP - Contract Recruiters Staffing".to_string()),
        url: None,
        resume_url: None,
        page: None,
        pages: Vec::new(),
        state_counts: BTreeMap::new(),
        raw_row_count: None,
        output_row_count: None,
        rows: vec![
            SalesNavCaptureRow {
                index: 0,
                name: Some("Duplicate Lead".to_string()),
                profile_url: Some(
                    "https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token?_ntb=session"
                        .to_string(),
                ),
                scroll_urn: None,
                visible_state: None,
                menu_state: Some("connectable".to_string()),
                menu_labels: None,
                row_html_path: None,
            },
            SalesNavCaptureRow {
                index: 1,
                name: Some("Duplicate Lead".to_string()),
                profile_url: Some(
                    "https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token".to_string(),
                ),
                scroll_urn: None,
                visible_state: None,
                menu_state: Some("connectable".to_string()),
                menu_labels: None,
                row_html_path: None,
            },
        ],
    };

    let imported = import_capture(
        &mut run,
        capture,
        ImportCaptureOptions {
            only_connectable: true,
        },
    )
    .unwrap();

    assert_eq!(imported, 1);
    assert_eq!(run.observations.len(), 1);
}

#[test]
fn candidate_matching_ignores_sales_nav_tracking_params() {
    let candidate = CandidateEvent {
        at: Local::now(),
        source: "ASAP - Contract Recruiters Staffing".to_string(),
        name: "Tracked Lead".to_string(),
        profile_url: Some(
            "https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token?_ntb=session".to_string(),
        ),
        status: CandidateStatus::Pending,
        note: None,
    };
    let observation = connectable_observation(
        "ASAP - Contract Recruiters Staffing",
        "Tracked Lead",
        "https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token",
    );

    assert!(candidate_matches_observation(&candidate, &observation));
}

#[test]
fn reservoir_plan_and_fill_reuses_precaptured_candidates() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    let mut reservoir = CandidateReservoir::default();
    reservoir.observations.push(connectable_observation(
        "ASAP - Agency Owners Delivery",
        "Reservoir Founder",
        "https://www.linkedin.com/sales/lead/reservoir",
    ));

    match run.operator_plan_with_reservoir(Some(&reservoir)) {
        OperatorPlan::UseReservoir {
            source,
            remaining,
            available,
        } => {
            assert_eq!(source, "ASAP - Agency Owners Delivery");
            assert_eq!(remaining, 7);
            assert_eq!(available, 1);
        }
        other => panic!("expected reservoir plan, got {other:?}"),
    }

    let imported = fill_run_from_reservoir(
        &mut run,
        &mut reservoir,
        "ASAP - Agency Owners Delivery",
        10,
    )
    .unwrap();

    assert_eq!(imported, 1);
    assert_eq!(reservoir.observations.len(), 0);
    assert_eq!(
        run.next_connectable_observation().unwrap().name,
        "Reservoir Founder"
    );
}

#[test]
fn final_audit_short_preserves_fallback_candidates_for_top_up() {
    let mut run = completed_short_audit_run();
    let fallback = connectable_observation(
        "FO - Founders - Urgent",
        "Fallback Top Up",
        "https://www.linkedin.com/sales/lead/fallback",
    );
    run.observations.push(fallback.clone());
    run.candidates.push(CandidateEvent {
        at: Local::now(),
        source: fallback.source.clone(),
        name: fallback.name.clone(),
        profile_url: fallback.profile_url.clone(),
        status: CandidateStatus::Skipped,
        note: Some(
            "auto-skipped stale imported candidate after source closed or filled".to_string(),
        ),
    });

    let drained = drain_stale_connectable_candidates(&mut run, None).unwrap();

    assert!(drained.is_empty());
    assert_eq!(
        run.next_top_up_observation().unwrap().name,
        "Fallback Top Up"
    );
}

#[test]
fn top_up_reservoir_fill_ignores_old_auto_stale_skip() {
    let mut run = completed_short_audit_run();
    let fallback = connectable_observation(
        "FO - Founders - Urgent",
        "Reservoir Top Up",
        "https://www.linkedin.com/sales/lead/reservoir-top-up",
    );
    run.candidates.push(CandidateEvent {
        at: Local::now(),
        source: fallback.source.clone(),
        name: fallback.name.clone(),
        profile_url: fallback.profile_url.clone(),
        status: CandidateStatus::Skipped,
        note: Some(
            "auto-skipped stale imported candidate after source closed or filled".to_string(),
        ),
    });
    let mut reservoir = CandidateReservoir {
        observations: vec![fallback],
        updated_at: Some(Local::now()),
    };

    let imported =
        fill_run_from_reservoir_for_top_up(&mut run, &mut reservoir, "FO - Founders - Urgent", 5)
            .unwrap();

    assert_eq!(imported, 1);
    assert!(reservoir.observations.is_empty());
    assert_eq!(
        run.next_top_up_observation().unwrap().name,
        "Reservoir Top Up"
    );
}

#[test]
fn saved_search_url_resolves_from_searches_artifact() {
    let path = std::env::temp_dir().join(format!(
        "linkedin-network-run-saved-searches-{}.json",
        Uuid::new_v4()
    ));
    fs::write(
        &path,
        serde_json::json!({
            "searches": [
                {
                    "name": "FO - Founders - Urgent",
                    "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=1"
                }
            ]
        })
        .to_string(),
    )
    .unwrap();

    let url = resolve_capture_url(None, &path, "FO - Founders - Urgent").unwrap();

    assert_eq!(
        url,
        "https://www.linkedin.com/sales/search/people?savedSearchId=1"
    );
    let _ = fs::remove_file(path);
}

#[test]
fn capture_plan_expands_buffer_after_email_required_skips() {
    let mut run = Run::new(30, NaiveDate::from_ymd_opt(2026, 6, 12).unwrap());
    for index in 0..2 {
        run.candidates.push(CandidateEvent {
            at: Local::now(),
            source: "ASAP - Agency Owners Delivery".to_string(),
            name: format!("Verified {index}"),
            profile_url: Some(format!(
                "https://www.linkedin.com/sales/lead/verified-{index}"
            )),
            status: CandidateStatus::Pending,
            note: None,
        });
    }
    for index in 0..3 {
        run.candidates.push(CandidateEvent {
            at: Local::now(),
            source: "ASAP - Agency Owners Delivery".to_string(),
            name: format!("Email Required {index}"),
            profile_url: Some(format!(
                "https://www.linkedin.com/sales/lead/skipped-{index}"
            )),
            status: CandidateStatus::Skipped,
            note: Some("salesnav-send-one stopped on email-required invite flow".to_string()),
        });
    }

    match run.operator_plan() {
        OperatorPlan::CaptureSource {
            remaining, capture, ..
        } => {
            assert_eq!(remaining, 7);
            assert_eq!(capture.pages, 5);
            assert_eq!(capture.stop_after_connectable, 14);
            assert_eq!(capture.reason, "high-email-required");
            assert_eq!(capture.playwriter_timeout_ms, 90_000);
        }
        other => panic!("expected capture plan, got {other:?}"),
    }
}

#[test]
fn large_capture_plan_uses_extended_playwriter_timeout() {
    let run = Run::new(30, NaiveDate::from_ymd_opt(2026, 6, 14).unwrap());
    let capture = run.capture_recommendation("ASAP - Agency Owners Delivery", 9);

    assert_eq!(capture.reason, "standard-buffer");
    assert_eq!(capture.pages, 5);
    assert_eq!(capture.stop_after_connectable, 12);
    assert_eq!(capture.playwriter_timeout_ms, 90_000);
}

#[test]
fn source_yield_marks_saturated_capture_as_low_yield() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    let mut state_counts = BTreeMap::new();
    state_counts.insert("already-pending".to_string(), 50);
    run.capture_cursors.insert(
        "ASAP - Agency Owners Delivery".to_string(),
        SourceCaptureCursor {
            source: "ASAP - Agency Owners Delivery".to_string(),
            updated_at: Local::now(),
            captured_at: None,
            resume_url: None,
            page_label: Some("Page 3 of 10".to_string()),
            captured_pages: 2,
            raw_row_count: 50,
            output_row_count: 0,
            connectable_count: 0,
            already_pending_count: 50,
            missing_trigger_count: 0,
            state_counts,
        },
    );

    let low_yield = low_yield_source_names(&run, 50, 0.05);
    let stats = source_yield_report(&run);

    assert_eq!(low_yield, vec!["ASAP - Agency Owners Delivery".to_string()]);
    assert_eq!(stats[0].connectable_yield, Some(0.0));
    assert!(stats[0].recommendation.contains("low-yield"));
}

#[test]
fn import_capture_updates_resume_cursor_for_plan() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    let mut state_counts = BTreeMap::new();
    state_counts.insert("already-pending".to_string(), 25);
    state_counts.insert("connectable".to_string(), 0);
    let capture = SalesNavCapture {
        captured_at: Some("2026-06-06T12:00:00Z".to_string()),
        source: Some("ASAP - Agency Owners Delivery".to_string()),
        url: Some("https://www.linkedin.com/sales/search/people?page=20".to_string()),
        resume_url: Some("https://www.linkedin.com/sales/search/people?page=20".to_string()),
        page: Some(SalesNavCapturePage {
            url: Some("https://www.linkedin.com/sales/search/people?page=20".to_string()),
            page_label: Some("Page 20 of 40".to_string()),
        }),
        pages: Vec::new(),
        state_counts,
        raw_row_count: Some(25),
        output_row_count: Some(0),
        rows: vec![SalesNavCaptureRow {
            index: 0,
            name: Some("Already Pending".to_string()),
            profile_url: Some("https://www.linkedin.com/sales/lead/a".to_string()),
            scroll_urn: None,
            visible_state: None,
            menu_state: Some("already-pending".to_string()),
            menu_labels: None,
            row_html_path: None,
        }],
    };

    let imported = import_capture(
        &mut run,
        capture,
        ImportCaptureOptions {
            only_connectable: true,
        },
    )
    .unwrap();

    assert_eq!(imported, 0);
    match run.operator_plan() {
        OperatorPlan::CaptureSource {
            source,
            resume_url,
            cursor,
            ..
        } => {
            assert_eq!(source, "ASAP - Agency Owners Delivery");
            assert_eq!(
                resume_url.as_deref(),
                Some("https://www.linkedin.com/sales/search/people?page=20")
            );
            let cursor = cursor.unwrap();
            assert_eq!(cursor.page_label.as_deref(), Some("Page 20 of 40"));
            assert_eq!(cursor.raw_row_count, 25);
            assert_eq!(cursor.connectable_count, 0);
            assert_eq!(cursor.already_pending_count, 25);
        }
        other => panic!("expected capture plan with resume cursor, got {other:?}"),
    }
}

#[test]
fn next_candidate_ignores_filled_source_observations() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    for index in 0..7 {
        run.candidates.push(CandidateEvent {
            at: Local::now(),
            source: "ASAP - Agency Owners Delivery".to_string(),
            name: format!("AI Founder {index}"),
            profile_url: None,
            status: CandidateStatus::Pending,
            note: None,
        });
    }
    run.observations.push(connectable_observation(
        "ASAP - Agency Owners Delivery",
        "Stale AI Founder",
        "https://www.linkedin.com/sales/lead/stale",
    ));
    run.observations.push(connectable_observation(
        "ASAP - Contract Recruiters Staffing",
        "Active Product Leader",
        "https://www.linkedin.com/sales/lead/active",
    ));

    assert_eq!(
        run.next_connectable_observation().unwrap().name,
        "Active Product Leader"
    );
}

#[test]
fn drain_stale_candidates_skips_filled_source_queue() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    for index in 0..7 {
        run.candidates.push(CandidateEvent {
            at: Local::now(),
            source: "ASAP - Agency Owners Delivery".to_string(),
            name: format!("AI Founder {index}"),
            profile_url: None,
            status: CandidateStatus::Pending,
            note: None,
        });
    }
    run.observations.push(connectable_observation(
        "ASAP - Agency Owners Delivery",
        "Stale AI Founder",
        "https://www.linkedin.com/sales/lead/stale",
    ));

    let drained = drain_stale_connectable_candidates(&mut run, None).unwrap();

    assert_eq!(drained.len(), 1);
    assert_eq!(drained[0].status, CandidateStatus::Skipped);
    assert!(run.has_candidate_event_for_observation(&run.observations[0]));
}

#[test]
fn send_result_maps_pending_verified_to_pending_event() {
    let result = SalesNavSendResult {
        candidate: SalesNavSendCandidate {
            source: "ASAP - Startup CTO Eng Leaders".to_string(),
            name: "Verified Founder".to_string(),
            profile_url: Some("https://www.linkedin.com/sales/lead/x".to_string()),
        },
        status: "pending-verified".to_string(),
        send: None,
    };

    let (status, note) = result.to_candidate_status();

    assert_eq!(status, CandidateStatus::Pending);
    assert!(note.contains("Connect - Pending"));
}

#[test]
fn top_up_result_does_not_increment_row_level_verified_count() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    let result = SalesNavSendResult {
        candidate: SalesNavSendCandidate {
            source: "ASAP - Vertical Proof Buyers".to_string(),
            name: "Top Up Founder".to_string(),
            profile_url: Some("https://www.linkedin.com/sales/lead/top-up".to_string()),
        },
        status: "pending-verified".to_string(),
        send: None,
    };

    let event = record_top_up_send_result(
        &mut run,
        result,
        PathBuf::from("/tmp/top-up-result.json"),
        Some("audit reconciliation".to_string()),
    )
    .unwrap();

    assert_eq!(event.status, CandidateStatus::AuditTopUp);
    assert_eq!(run.verified_count(), 0);
}

#[test]
fn acceptance_ledger_seeds_pending_and_top_up_invites() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    run.candidates.push(CandidateEvent {
        at: Local::now(),
        source: "ASAP - Startup CTO Eng Leaders".to_string(),
        name: "Verified Founder".to_string(),
        profile_url: Some("https://www.linkedin.com/sales/lead/verified?_ntb=abc".to_string()),
        status: CandidateStatus::Pending,
        note: None,
    });
    run.candidates.push(CandidateEvent {
        at: Local::now(),
        source: "ASAP - Vertical Proof Buyers".to_string(),
        name: "Top Up Founder".to_string(),
        profile_url: Some("https://www.linkedin.com/sales/lead/top-up".to_string()),
        status: CandidateStatus::AuditTopUp,
        note: None,
    });
    run.candidates.push(CandidateEvent {
        at: Local::now(),
        source: "ASAP - Startup CTO Eng Leaders".to_string(),
        name: "Skipped Founder".to_string(),
        profile_url: Some("https://www.linkedin.com/sales/lead/skipped".to_string()),
        status: CandidateStatus::Skipped,
        note: None,
    });

    let mut ledger = AcceptanceLedger::default();
    let seeded = ledger.upsert_from_run(&run);
    let reseeded = ledger.upsert_from_run(&run);

    assert_eq!(seeded, 2);
    assert_eq!(reseeded, 0);
    assert_eq!(ledger.invitations.len(), 2);
    assert_eq!(
        ledger.invitations[0].profile_url.as_deref(),
        Some("https://www.linkedin.com/sales/lead/verified?_ntb=abc")
    );
}

#[test]
fn acceptance_history_seed_reads_controller_jsonl() -> Result<()> {
    let dir = std::env::temp_dir().join(format!("linkedin-network-run-test-{}", Uuid::new_v4()));
    fs::create_dir_all(&dir)?;
    let store = Store::new(Some(dir.clone()))?;
    let run_id = Uuid::new_v4();
    let sent_at = Local::now() - chrono::Duration::days(8);
    let pending_event = CandidateEvent {
        at: sent_at,
        source: "ASAP - Startup CTO Eng Leaders".to_string(),
        name: "Historical Founder".to_string(),
        profile_url: Some("https://www.linkedin.com/sales/lead/historical?_ntb=abc".to_string()),
        status: CandidateStatus::Pending,
        note: None,
    };
    let skipped_event = CandidateEvent {
        at: sent_at,
        source: "ASAP - Startup CTO Eng Leaders".to_string(),
        name: "Skipped Founder".to_string(),
        profile_url: Some("https://www.linkedin.com/sales/lead/skipped".to_string()),
        status: CandidateStatus::Skipped,
        note: None,
    };
    let lines = [
        serde_json::json!({
            "at": sent_at,
            "run_id": run_id,
            "kind": "start",
            "payload": { "target": 25 }
        })
        .to_string(),
        serde_json::json!({
            "at": sent_at,
            "run_id": run_id,
            "kind": "record-send-result",
            "payload": { "event": pending_event }
        })
        .to_string(),
        serde_json::json!({
            "at": sent_at,
            "run_id": run_id,
            "kind": "record-send-result",
            "payload": { "event": skipped_event }
        })
        .to_string(),
    ]
    .join("\n");
    fs::write(dir.join(format!("{run_id}.jsonl")), lines)?;

    let mut ledger = AcceptanceLedger::default();
    let summary = store.seed_acceptance_from_history(&mut ledger)?;
    let reseeded = store.seed_acceptance_from_history(&mut ledger)?;

    assert_eq!(summary.run_logs, 1);
    assert_eq!(summary.sent_events, 1);
    assert_eq!(summary.seeded, 1);
    assert_eq!(reseeded.seeded, 0);
    assert_eq!(ledger.invitations.len(), 1);
    assert_eq!(ledger.invitations[0].name, "Historical Founder");

    fs::remove_dir_all(dir)?;
    Ok(())
}

#[test]
fn acceptance_import_updates_source_report() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    run.candidates.push(CandidateEvent {
        at: Local::now() - chrono::Duration::days(8),
        source: "ASAP - Startup CTO Eng Leaders".to_string(),
        name: "Accepted Founder".to_string(),
        profile_url: Some("https://www.linkedin.com/sales/lead/accepted?_ntb=abc".to_string()),
        status: CandidateStatus::Pending,
        note: None,
    });
    run.candidates.push(CandidateEvent {
        at: Local::now() - chrono::Duration::days(8),
        source: "ASAP - Startup CTO Eng Leaders".to_string(),
        name: "Pending Founder".to_string(),
        profile_url: Some("https://www.linkedin.com/sales/lead/pending".to_string()),
        status: CandidateStatus::Pending,
        note: None,
    });
    let mut ledger = AcceptanceLedger::default();
    ledger.upsert_from_run(&run);

    let summary = ledger.import_outcomes(AcceptanceOutcomeArtifact {
        rows: vec![
            AcceptanceOutcomeRow {
                source: "ASAP - Startup CTO Eng Leaders".to_string(),
                name: "Accepted Founder".to_string(),
                profile_url: Some("https://www.linkedin.com/sales/lead/accepted".to_string()),
                status: AcceptanceStatus::Accepted,
                checked_at: Some(Local::now()),
                relationship: Some("1st".to_string()),
                evidence: None,
                note: None,
            },
            AcceptanceOutcomeRow {
                source: "ASAP - Startup CTO Eng Leaders".to_string(),
                name: "Pending Founder".to_string(),
                profile_url: Some("https://www.linkedin.com/sales/lead/pending".to_string()),
                status: AcceptanceStatus::Pending,
                checked_at: Some(Local::now()),
                relationship: Some("2nd".to_string()),
                evidence: None,
                note: None,
            },
        ],
    });
    let report = ledger.report(7, None);

    assert_eq!(summary.matched, 2);
    assert_eq!(report.total_sent, 2);
    assert_eq!(report.checked, 2);
    assert_eq!(report.accepted, 1);
    assert_eq!(
        report
            .by_source
            .get("ASAP - Startup CTO Eng Leaders")
            .unwrap()
            .pending,
        1
    );
}

#[test]
fn accepted_followup_candidates_skip_already_drafted_people() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 6, 20).unwrap());
    run.candidates.push(CandidateEvent {
        at: Local::now() - chrono::Duration::days(2),
        source: "ASAP - Agency Owners Delivery".to_string(),
        name: "Accepted Agency Owner".to_string(),
        profile_url: Some("https://www.linkedin.com/sales/lead/accepted?_ntb=abc".to_string()),
        status: CandidateStatus::Pending,
        note: None,
    });
    run.candidates.push(CandidateEvent {
        at: Local::now() - chrono::Duration::days(2),
        source: "ASAP - Agency Owners Delivery".to_string(),
        name: "Still Pending".to_string(),
        profile_url: Some("https://www.linkedin.com/sales/lead/pending".to_string()),
        status: CandidateStatus::Pending,
        note: None,
    });

    let mut ledger = AcceptanceLedger::default();
    ledger.upsert_from_run(&run);
    ledger.import_outcomes(AcceptanceOutcomeArtifact {
        rows: vec![
            AcceptanceOutcomeRow {
                source: "ASAP - Agency Owners Delivery".to_string(),
                name: "Accepted Agency Owner".to_string(),
                profile_url: Some("https://www.linkedin.com/sales/lead/accepted".to_string()),
                status: AcceptanceStatus::Accepted,
                checked_at: Some(Local::now()),
                relationship: Some("1st".to_string()),
                evidence: None,
                note: Some("lead page shows 1st-degree relationship".to_string()),
            },
            AcceptanceOutcomeRow {
                source: "ASAP - Agency Owners Delivery".to_string(),
                name: "Still Pending".to_string(),
                profile_url: Some("https://www.linkedin.com/sales/lead/pending".to_string()),
                status: AcceptanceStatus::Pending,
                checked_at: Some(Local::now()),
                relationship: Some("2nd".to_string()),
                evidence: None,
                note: None,
            },
        ],
    });

    let empty_followups = AcceptanceFollowupLedger::default();
    let candidates = ledger.accepted_for_followup(&empty_followups, false);
    assert_eq!(candidates.len(), 1);
    assert_eq!(candidates[0].name, "Accepted Agency Owner");

    let report = build_draft_report(candidates, None, DraftStrategy::AsapContractV1, None);
    let mut followups = AcceptanceFollowupLedger::default();
    followups.record_report(&report, Path::new("/tmp/followups.md"), None);

    assert!(ledger.accepted_for_followup(&followups, false).is_empty());
    assert_eq!(ledger.accepted_for_followup(&followups, true).len(), 1);
}

#[test]
fn operator_plan_prefers_reaudit_then_send_then_capture() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    assert!(matches!(
        run.operator_plan(),
        OperatorPlan::CaptureSource { .. }
    ));

    run.observations.push(CandidateObservation {
        imported_at: Local::now(),
        captured_at: None,
        source: "ASAP - Agency Owners Delivery".to_string(),
        index: 0,
        name: "Connectable".to_string(),
        profile_url: Some("https://www.linkedin.com/sales/lead/c".to_string()),
        sales_profile_urn: None,
        visible_state: serde_json::Value::Null,
        menu_state: "connectable".to_string(),
        menu_labels: vec!["Connect".to_string()],
        row_html_path: None,
    });
    assert!(matches!(
        run.operator_plan(),
        OperatorPlan::SendCandidate { .. }
    ));

    run.state = RunState::NeedsReaudit;
    assert!(matches!(run.operator_plan(), OperatorPlan::Reaudit { .. }));
}

#[test]
fn run_level_real_send_cap_counts_pending_events() {
    let mut run =
        Run::new_with_max_real_sends(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap(), 1);
    assert_eq!(run.real_send_capacity_remaining(), 1);
    run.candidates.push(CandidateEvent {
        at: Local::now(),
        source: "ASAP - Startup CTO Eng Leaders".to_string(),
        name: "A".to_string(),
        profile_url: None,
        status: CandidateStatus::Pending,
        note: None,
    });
    assert_eq!(run.real_send_capacity_remaining(), 0);
}

#[test]
fn operator_plan_blocks_when_real_send_cap_is_reached() {
    let mut run =
        Run::new_with_max_real_sends(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap(), 1);
    run.candidates.push(CandidateEvent {
        at: Local::now(),
        source: "ASAP - Startup CTO Eng Leaders".to_string(),
        name: "Already Sent".to_string(),
        profile_url: None,
        status: CandidateStatus::Pending,
        note: None,
    });
    run.observations.push(CandidateObservation {
        imported_at: Local::now(),
        captured_at: None,
        source: "ASAP - Agency Owners Delivery".to_string(),
        index: 0,
        name: "Connectable".to_string(),
        profile_url: Some("https://www.linkedin.com/sales/lead/c".to_string()),
        sales_profile_urn: None,
        visible_state: serde_json::Value::Null,
        menu_state: "connectable".to_string(),
        menu_labels: vec!["Connect".to_string()],
        row_html_path: None,
    });

    assert!(matches!(run.operator_plan(), OperatorPlan::Blocked { .. }));
}

#[test]
fn audit_import_applies_people_count() {
    let mut run = Run::new(22, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    apply_audit(&mut run, 933, Some("start".to_string()));
    apply_audit(&mut run, 934, Some("after".to_string()));
    assert_eq!(run.audited_delta(), Some(1));
}

#[test]
fn send_result_failure_maps_to_failed_event() {
    let result = SalesNavSendResult {
        candidate: SalesNavSendCandidate {
            source: "ASAP - Startup CTO Eng Leaders".to_string(),
            name: "Unverified Founder".to_string(),
            profile_url: None,
        },
        status: "unverified:send-button-missing".to_string(),
        send: None,
    };
    let (status, note) = result.to_candidate_status();
    assert_eq!(status, CandidateStatus::Failed);
    assert!(note.contains("unverified"));
}

#[test]
fn pending_age_parser_marks_months_and_years_as_stale() {
    assert_eq!(parse_sent_age_months("Sent today"), Some(0));
    assert_eq!(parse_sent_age_months("Sent 3 weeks ago"), Some(0));
    assert_eq!(parse_sent_age_months("Sent 1 month ago"), Some(1));
    assert_eq!(parse_sent_age_months("Sent 2 months ago"), Some(2));
    assert_eq!(parse_sent_age_months("Sent 1 year ago"), Some(12));
}

#[test]
fn pending_capture_import_exposes_next_eligible_invitation() {
    let mut run = PendingCleanupRun::new(75, 2, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    let capture = PendingCapture {
        captured_at: Some("2026-05-26T12:00:00Z".to_string()),
        rows: vec![
            PendingCaptureRow {
                index: 0,
                name: Some("Fresh Invite".to_string()),
                profile_url: None,
                age_text: Some("Sent 1 month ago".to_string()),
                age_months: Some(1),
                eligible: None,
                row_text: None,
            },
            PendingCaptureRow {
                index: 1,
                name: Some("Stale Invite".to_string()),
                profile_url: Some("https://www.linkedin.com/in/stale".to_string()),
                age_text: Some("Sent 3 months ago".to_string()),
                age_months: Some(3),
                eligible: None,
                row_text: None,
            },
        ],
    };

    let imported = import_pending_capture(&mut run, capture).unwrap();

    assert_eq!(imported, 2);
    assert_eq!(
        run.next_eligible_observation().unwrap().name,
        "Stale Invite"
    );
}

#[test]
fn pending_withdraw_result_counts_only_verified_withdrawals() {
    let mut run = PendingCleanupRun::new(1, 2, NaiveDate::from_ymd_opt(2026, 5, 26).unwrap());
    let result = PendingWithdrawResult {
        candidate: PendingWithdrawCandidate {
            name: "Stale Invite".to_string(),
            profile_url: None,
            age_text: "Sent 2 months ago".to_string(),
        },
        status: "withdrawn-verified".to_string(),
        detail: None,
    };

    let event = record_pending_withdraw_result(
        &mut run,
        result,
        PathBuf::from("/tmp/withdraw-result.json"),
    )
    .unwrap();

    assert_eq!(event.status, PendingWithdrawStatus::Withdrawn);
    assert_eq!(run.withdrawn_count(), 1);
    assert!(matches!(
        run.operator_plan(),
        PendingCleanupPlan::FinalAudit
    ));
}

#[test]
fn playwriter_fallback_detects_bunx_executable_names() {
    assert!(is_bunx_path(&PathBuf::from(
        "/Users/hanifcarroll/.bun/bin/bunx"
    )));
    assert!(is_bunx_path(&PathBuf::from("bunx.exe")));
    assert!(!is_bunx_path(&PathBuf::from(
        "/Users/hanifcarroll/.bun/bin/playwriter"
    )));
}
