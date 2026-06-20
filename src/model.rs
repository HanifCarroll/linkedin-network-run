use crate::*;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) enum RunState {
    Started,
    StartAudited,
    Sending,
    NeedsReaudit,
    FinalReconcile,
    Done,
    Blocked,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub(crate) enum CandidateStatus {
    Pending,
    AlreadyPending,
    AuditTopUp,
    Skipped,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, ValueEnum, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub(crate) enum AcceptanceStatus {
    Sent,
    Pending,
    Accepted,
    Connectable,
    Withdrawn,
    Unknown,
    Blocked,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct SourcePlan {
    pub(crate) name: String,
    pub(crate) target: u32,
    pub(crate) fallback: bool,
    pub(crate) exhausted: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct CandidateEvent {
    pub(crate) at: DateTime<Local>,
    pub(crate) source: String,
    pub(crate) name: String,
    pub(crate) profile_url: Option<String>,
    pub(crate) status: CandidateStatus,
    pub(crate) note: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct CandidateObservation {
    pub(crate) imported_at: DateTime<Local>,
    pub(crate) captured_at: Option<String>,
    pub(crate) source: String,
    pub(crate) index: u32,
    pub(crate) name: String,
    pub(crate) profile_url: Option<String>,
    pub(crate) sales_profile_urn: Option<String>,
    pub(crate) visible_state: serde_json::Value,
    pub(crate) menu_state: String,
    pub(crate) menu_labels: Vec<String>,
    pub(crate) row_html_path: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct SourceCaptureCursor {
    pub(crate) source: String,
    pub(crate) updated_at: DateTime<Local>,
    pub(crate) captured_at: Option<String>,
    pub(crate) resume_url: Option<String>,
    pub(crate) page_label: Option<String>,
    pub(crate) captured_pages: u32,
    pub(crate) raw_row_count: u32,
    pub(crate) output_row_count: u32,
    pub(crate) connectable_count: u32,
    pub(crate) already_pending_count: u32,
    pub(crate) missing_trigger_count: u32,
    pub(crate) state_counts: BTreeMap<String, u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct RunTimingEvent {
    pub(crate) at: DateTime<Local>,
    pub(crate) phase: String,
    pub(crate) source: Option<String>,
    pub(crate) duration_ms: u64,
    pub(crate) detail: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub(crate) struct CandidateReservoir {
    #[serde(default)]
    pub(crate) observations: Vec<CandidateObservation>,
    pub(crate) updated_at: Option<DateTime<Local>>,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct SourceYieldStats {
    pub(crate) source: String,
    pub(crate) raw_row_count: u32,
    pub(crate) connectable_count: u32,
    pub(crate) already_pending_count: u32,
    pub(crate) email_required_skips: u32,
    pub(crate) pending_sends: u32,
    pub(crate) connectable_yield: Option<f64>,
    pub(crate) recommendation: String,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct CaptureRecommendation {
    pub(crate) pages: u32,
    pub(crate) stop_after_connectable: u32,
    pub(crate) buffer: u32,
    pub(crate) reason: String,
    pub(crate) playwriter_timeout_ms: u32,
}

#[derive(Debug, Clone)]
pub(crate) struct CaptureRunOptions {
    pub(crate) pages: u32,
    pub(crate) stop_after_connectable: u32,
    pub(crate) limit: u32,
    pub(crate) row_scroll_delay_ms: u32,
    pub(crate) only_connectable: bool,
}

#[derive(Debug, Clone)]
pub(crate) struct TopUpFallbackOptions {
    pub(crate) capture_script: PathBuf,
    pub(crate) saved_searches: PathBuf,
    pub(crate) source: String,
    pub(crate) url: Option<String>,
    pub(crate) pages: u32,
    pub(crate) stop_after_connectable: u32,
    pub(crate) limit: u32,
    pub(crate) row_scroll_delay_ms: u32,
    pub(crate) capture_enabled: bool,
}

impl CaptureRecommendation {
    const DEFAULT_PLAYWRITER_TIMEOUT_MS: u32 = 45_000;
    const EXTENDED_PLAYWRITER_TIMEOUT_MS: u32 = 90_000;

    pub(crate) fn standard(remaining: u32) -> Self {
        let buffer = if remaining == 0 { 0 } else { 3 };
        Self {
            pages: if remaining.saturating_add(buffer) > 10 {
                5
            } else {
                3
            },
            stop_after_connectable: remaining.saturating_add(buffer).min(25),
            buffer,
            reason: "standard-buffer".to_string(),
            playwriter_timeout_ms: Self::DEFAULT_PLAYWRITER_TIMEOUT_MS,
        }
    }

    pub(crate) fn expanded(remaining: u32, reason: &str) -> Self {
        let buffer = remaining.max(5);
        Self {
            pages: 5,
            stop_after_connectable: remaining.saturating_add(buffer).min(25),
            buffer,
            reason: reason.to_string(),
            playwriter_timeout_ms: Self::EXTENDED_PLAYWRITER_TIMEOUT_MS,
        }
    }

    pub(crate) fn with_extended_timeout(mut self) -> Self {
        self.playwriter_timeout_ms = Self::EXTENDED_PLAYWRITER_TIMEOUT_MS;
        self
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct AuditEvent {
    pub(crate) at: DateTime<Local>,
    pub(crate) people_count: u32,
    pub(crate) note: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct Run {
    pub(crate) id: Uuid,
    pub(crate) date: NaiveDate,
    pub(crate) target: u32,
    #[serde(default)]
    pub(crate) max_real_sends: u32,
    pub(crate) state: RunState,
    pub(crate) sources: Vec<SourcePlan>,
    pub(crate) start_audit: Option<u32>,
    pub(crate) latest_audit: Option<u32>,
    pub(crate) audits: Vec<AuditEvent>,
    pub(crate) candidates: Vec<CandidateEvent>,
    #[serde(default)]
    pub(crate) observations: Vec<CandidateObservation>,
    #[serde(default)]
    pub(crate) capture_cursors: BTreeMap<String, SourceCaptureCursor>,
    #[serde(default)]
    pub(crate) timings: Vec<RunTimingEvent>,
    pub(crate) notes: Vec<String>,
    pub(crate) created_at: DateTime<Local>,
    pub(crate) updated_at: DateTime<Local>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) enum PendingCleanupState {
    Started,
    Audited,
    Capturing,
    Withdrawing,
    NeedsReaudit,
    FinalReconcile,
    Done,
    Blocked,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) enum PendingWithdrawStatus {
    Withdrawn,
    Skipped,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct PendingCandidateObservation {
    pub(crate) imported_at: DateTime<Local>,
    pub(crate) captured_at: Option<String>,
    pub(crate) index: u32,
    pub(crate) name: String,
    pub(crate) profile_url: Option<String>,
    pub(crate) age_text: String,
    pub(crate) age_months: Option<u32>,
    pub(crate) eligible: bool,
    pub(crate) row_text: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct PendingWithdrawEvent {
    pub(crate) at: DateTime<Local>,
    pub(crate) name: String,
    pub(crate) profile_url: Option<String>,
    pub(crate) age_text: String,
    pub(crate) status: PendingWithdrawStatus,
    pub(crate) note: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct PendingCleanupRun {
    pub(crate) id: Uuid,
    pub(crate) date: NaiveDate,
    pub(crate) max_withdrawals: u32,
    pub(crate) threshold_months: u32,
    pub(crate) state: PendingCleanupState,
    pub(crate) start_audit: Option<u32>,
    pub(crate) latest_audit: Option<u32>,
    pub(crate) audits: Vec<AuditEvent>,
    pub(crate) observations: Vec<PendingCandidateObservation>,
    pub(crate) withdrawals: Vec<PendingWithdrawEvent>,
    pub(crate) notes: Vec<String>,
    pub(crate) created_at: DateTime<Local>,
    pub(crate) updated_at: DateTime<Local>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub(crate) struct AcceptanceLedger {
    #[serde(default)]
    pub(crate) invitations: Vec<AcceptanceInvitation>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct AcceptanceInvitation {
    pub(crate) run_id: Uuid,
    pub(crate) run_date: NaiveDate,
    pub(crate) source: String,
    pub(crate) name: String,
    pub(crate) profile_url: Option<String>,
    pub(crate) sent_at: DateTime<Local>,
    pub(crate) latest_status: AcceptanceStatus,
    pub(crate) latest_checked_at: Option<DateTime<Local>>,
    #[serde(default)]
    pub(crate) history: Vec<AcceptanceOutcomeEvent>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct AcceptanceOutcomeEvent {
    pub(crate) at: DateTime<Local>,
    pub(crate) status: AcceptanceStatus,
    pub(crate) note: Option<String>,
    pub(crate) relationship: Option<String>,
    pub(crate) evidence: Option<String>,
}

impl Run {
    #[cfg(test)]
    pub(crate) fn new(target: u32, date: NaiveDate) -> Self {
        Self::new_with_max_real_sends(target, date, target)
    }

    pub(crate) fn new_with_max_real_sends(
        target: u32,
        date: NaiveDate,
        max_real_sends: u32,
    ) -> Self {
        let now = Local::now();
        Self {
            id: Uuid::new_v4(),
            date,
            target,
            max_real_sends,
            state: RunState::Started,
            sources: default_sources(target),
            start_audit: None,
            latest_audit: None,
            audits: Vec::new(),
            candidates: Vec::new(),
            observations: Vec::new(),
            capture_cursors: BTreeMap::new(),
            timings: Vec::new(),
            notes: Vec::new(),
            created_at: now,
            updated_at: now,
        }
    }

    pub(crate) fn verified_count(&self) -> u32 {
        self.candidates
            .iter()
            .filter(|candidate| candidate.status == CandidateStatus::Pending)
            .count() as u32
    }

    pub(crate) fn audited_delta(&self) -> Option<i64> {
        Some(i64::from(self.latest_audit?) - i64::from(self.start_audit?))
    }

    pub(crate) fn source_verified_count(&self, source: &str) -> u32 {
        self.candidates
            .iter()
            .filter(|candidate| {
                candidate.source == source && candidate.status == CandidateStatus::Pending
            })
            .count() as u32
    }

    pub(crate) fn source_index(&self, source: &str) -> Option<usize> {
        self.sources
            .iter()
            .position(|candidate| candidate.name == source)
    }

    pub(crate) fn source_quota(&self, source: &str) -> Option<u32> {
        self.source_index(source)
            .map(|index| self.source_quota_with_carryover(index))
    }

    pub(crate) fn source_is_filled_or_closed(&self, source: &str) -> bool {
        if self.verified_count() >= self.target {
            return true;
        }
        let Some(index) = self.source_index(source) else {
            return false;
        };
        let source_plan = &self.sources[index];
        source_plan.exhausted
            || self.source_verified_count(source) >= self.source_quota_with_carryover(index)
    }

    pub(crate) fn primary_shortfall_before(&self, source_index: usize) -> u32 {
        self.sources
            .iter()
            .take(source_index)
            .filter(|source| !source.fallback)
            .map(|source| {
                source
                    .target
                    .saturating_sub(self.source_verified_count(&source.name))
            })
            .sum()
    }

    pub(crate) fn source_quota_with_carryover(&self, source_index: usize) -> u32 {
        let source = &self.sources[source_index];
        if source.fallback {
            return self
                .target
                .saturating_sub(self.verified_count())
                .max(source.target);
        }
        source
            .target
            .saturating_add(self.primary_shortfall_before(source_index))
    }

    pub(crate) fn next_source(&self) -> Option<NextSource> {
        if matches!(
            self.state,
            RunState::NeedsReaudit | RunState::Done | RunState::Blocked
        ) {
            return None;
        }

        let total_remaining = self.target.saturating_sub(self.verified_count());
        if total_remaining == 0 {
            return None;
        }

        for (index, source) in self.sources.iter().enumerate() {
            if source.exhausted {
                continue;
            }
            let quota = self.source_quota_with_carryover(index);
            let verified = self.source_verified_count(&source.name);
            if source.fallback || verified < quota {
                return Some(NextSource {
                    name: source.name.clone(),
                    quota,
                    verified,
                    remaining_for_source: quota.saturating_sub(verified).min(total_remaining),
                    remaining_for_run: total_remaining,
                    fallback: source.fallback,
                });
            }
        }

        None
    }

    pub(crate) fn mark_updated(&mut self) {
        self.updated_at = Local::now();
    }

    pub(crate) fn has_candidate_event_for_observation(
        &self,
        observation: &CandidateObservation,
    ) -> bool {
        self.candidates
            .iter()
            .any(|candidate| candidate_matches_observation(candidate, observation))
    }

    pub(crate) fn next_connectable_observation(&self) -> Option<&CandidateObservation> {
        let next_source = self.next_source()?;
        self.next_connectable_observation_for_source(&next_source.name)
    }

    pub(crate) fn next_connectable_observation_for_source(
        &self,
        source: &str,
    ) -> Option<&CandidateObservation> {
        if self.source_is_filled_or_closed(source) {
            return None;
        }
        self.observations.iter().find(|observation| {
            observation.source == source
                && observation.menu_state == "connectable"
                && !self.has_candidate_event_for_observation(observation)
        })
    }

    pub(crate) fn next_top_up_observation(&self) -> Option<&CandidateObservation> {
        self.observations
            .iter()
            .find(|observation| {
                self.source_is_fallback(&observation.source)
                    && observation.menu_state == "connectable"
                    && !self.has_top_up_blocking_event_for_observation(observation)
            })
            .or_else(|| {
                self.observations.iter().find(|observation| {
                    observation.menu_state == "connectable"
                        && !self.has_top_up_blocking_event_for_observation(observation)
                })
            })
    }

    pub(crate) fn real_send_capacity_remaining(&self) -> u32 {
        self.max_real_sends.saturating_sub(self.verified_count())
    }

    pub(crate) fn source_is_fallback(&self, source: &str) -> bool {
        self.sources
            .iter()
            .any(|plan| plan.name == source && plan.fallback)
    }

    pub(crate) fn final_audit_is_short(&self) -> bool {
        self.verified_count() >= self.target
            && self
                .audited_delta()
                .is_none_or(|delta| delta < i64::from(self.target))
            && !matches!(self.state, RunState::Done | RunState::Blocked)
    }

    pub(crate) fn preserve_for_audit_top_up(&self, observation: &CandidateObservation) -> bool {
        self.final_audit_is_short()
            && self.source_is_fallback(&observation.source)
            && observation.menu_state == "connectable"
    }

    pub(crate) fn has_top_up_blocking_event_for_observation(
        &self,
        observation: &CandidateObservation,
    ) -> bool {
        self.candidates.iter().any(|candidate| {
            candidate_matches_observation(candidate, observation) && !is_auto_stale_skip(candidate)
        })
    }

    pub(crate) fn capture_recommendation(
        &self,
        source: &str,
        remaining: u32,
    ) -> CaptureRecommendation {
        let Some(source_plan) = self.sources.iter().find(|plan| plan.name == source) else {
            return CaptureRecommendation::standard(remaining);
        };
        let stats = source_yield_stats(self, source_plan);
        let attempted = stats.pending_sends + stats.email_required_skips;
        let high_email_required =
            attempted >= 3 && f64::from(stats.email_required_skips) / f64::from(attempted) >= 0.30;
        let thin_capture_yield = stats.raw_row_count >= 25
            && stats
                .connectable_yield
                .is_some_and(|yield_rate| yield_rate <= 0.10);

        let has_resume_url = self
            .capture_cursors
            .get(source)
            .and_then(|cursor| cursor.resume_url.as_deref())
            .is_some();

        let recommendation = if high_email_required {
            CaptureRecommendation::expanded(remaining, "high-email-required")
        } else if thin_capture_yield {
            CaptureRecommendation::expanded(remaining, "thin-capture-yield")
        } else {
            CaptureRecommendation::standard(remaining)
        };

        if has_resume_url || recommendation.pages >= 5 {
            recommendation.with_extended_timeout()
        } else {
            recommendation
        }
    }

    #[cfg(test)]
    pub(crate) fn operator_plan(&self) -> OperatorPlan {
        self.operator_plan_with_reservoir(None)
    }

    pub(crate) fn operator_plan_with_reservoir(
        &self,
        reservoir: Option<&CandidateReservoir>,
    ) -> OperatorPlan {
        if self.state == RunState::NeedsReaudit {
            return OperatorPlan::Reaudit {
                reason: "run is paused in NEEDS_REAUDIT".to_string(),
            };
        }
        if self.verified_count() >= self.target {
            return OperatorPlan::FinalAudit;
        }
        if let Some(candidate) = self.next_connectable_observation() {
            if self.real_send_capacity_remaining() == 0 {
                return OperatorPlan::Blocked {
                    reason: format!(
                        "real-send cap reached: {}/{} verified sends",
                        self.verified_count(),
                        self.max_real_sends
                    ),
                };
            }
            return OperatorPlan::SendCandidate {
                source: candidate.source.clone(),
                name: candidate.name.clone(),
                profile_url: candidate.profile_url.clone(),
                real_send_capacity_remaining: self.real_send_capacity_remaining(),
            };
        }
        if let Some(next) = self.next_source() {
            let source = next.name;
            if let Some(reservoir) = reservoir {
                let available = reservoir.available_for_run_source(self, &source).len();
                if available > 0 {
                    return OperatorPlan::UseReservoir {
                        source,
                        remaining: next.remaining_for_source,
                        available,
                    };
                }
            }
            return OperatorPlan::CaptureSource {
                capture: self.capture_recommendation(&source, next.remaining_for_source),
                resume_url: self
                    .capture_cursors
                    .get(&source)
                    .and_then(|cursor| cursor.resume_url.clone()),
                cursor: self.capture_cursors.get(&source).cloned(),
                source,
                remaining: next.remaining_for_source,
            };
        }
        OperatorPlan::Blocked {
            reason: "no connectable candidate and no available source".to_string(),
        }
    }

    pub(crate) fn sent_invitation_events(&self) -> impl Iterator<Item = &CandidateEvent> {
        self.candidates.iter().filter(|candidate| {
            matches!(
                candidate.status,
                CandidateStatus::Pending | CandidateStatus::AuditTopUp
            )
        })
    }
}

impl CandidateReservoir {
    pub(crate) fn available_for_run_source<'a>(
        &'a self,
        run: &Run,
        source: &str,
    ) -> Vec<&'a CandidateObservation> {
        self.observations
            .iter()
            .filter(|observation| {
                observation.source == source
                    && observation.menu_state == "connectable"
                    && !run.has_candidate_event_for_observation(observation)
                    && !run
                        .observations
                        .iter()
                        .any(|existing| same_observation_identity(existing, observation))
            })
            .collect()
    }
}

impl AcceptanceLedger {
    pub(crate) fn upsert_from_run(&mut self, run: &Run) -> usize {
        let mut inserted = 0;
        for event in run.sent_invitation_events() {
            if self.upsert_invitation(run.id, run.date, event) {
                inserted += 1;
            }
        }
        inserted
    }

    pub(crate) fn upsert_from_events(
        &mut self,
        run_id: Uuid,
        run_date: NaiveDate,
        events: &[CandidateEvent],
    ) -> usize {
        events
            .iter()
            .filter(|event| {
                matches!(
                    event.status,
                    CandidateStatus::Pending | CandidateStatus::AuditTopUp
                )
            })
            .filter(|event| self.upsert_invitation(run_id, run_date, event))
            .count()
    }

    pub(crate) fn upsert_invitation(
        &mut self,
        run_id: Uuid,
        run_date: NaiveDate,
        event: &CandidateEvent,
    ) -> bool {
        let key =
            AcceptanceKey::from_parts(&event.source, &event.name, event.profile_url.as_deref());
        if let Some(existing) = self
            .invitations
            .iter_mut()
            .find(|invitation| invitation.key() == key)
        {
            if existing.run_id != run_id && existing.sent_at > event.at {
                existing.run_id = run_id;
                existing.run_date = run_date;
                existing.sent_at = event.at;
            }
            return false;
        }
        self.invitations.push(AcceptanceInvitation {
            run_id,
            run_date,
            source: event.source.clone(),
            name: event.name.clone(),
            profile_url: event.profile_url.clone(),
            sent_at: event.at,
            latest_status: AcceptanceStatus::Sent,
            latest_checked_at: None,
            history: Vec::new(),
        });
        true
    }

    pub(crate) fn import_outcomes(
        &mut self,
        artifact: AcceptanceOutcomeArtifact,
    ) -> AcceptanceImportSummary {
        let mut summary = AcceptanceImportSummary::default();
        for row in artifact.rows {
            summary.rows += 1;
            let key = AcceptanceKey::from_parts(&row.source, &row.name, row.profile_url.as_deref());
            let Some(invitation) = self
                .invitations
                .iter_mut()
                .find(|invitation| invitation.key() == key)
            else {
                summary.unmatched += 1;
                continue;
            };
            let checked_at = row.checked_at.unwrap_or_else(Local::now);
            let event = AcceptanceOutcomeEvent {
                at: checked_at,
                status: row.status,
                note: row.note,
                relationship: row.relationship,
                evidence: row.evidence,
            };
            invitation.latest_status = row.status;
            invitation.latest_checked_at = Some(checked_at);
            invitation.history.push(event);
            summary.matched += 1;
        }
        summary
    }

    pub(crate) fn eligible_for_check(
        &self,
        min_age_days: i64,
        max_age_days: Option<i64>,
    ) -> Vec<&AcceptanceInvitation> {
        let now = Local::now();
        self.invitations
            .iter()
            .filter(|invitation| {
                !matches!(
                    invitation.latest_status,
                    AcceptanceStatus::Accepted | AcceptanceStatus::Withdrawn
                )
            })
            .filter(|invitation| invitation.profile_url.is_some())
            .filter(|invitation| {
                let age_days = now.signed_duration_since(invitation.sent_at).num_days();
                age_days >= min_age_days && max_age_days.is_none_or(|max| age_days <= max)
            })
            .collect()
    }

    pub(crate) fn report(&self, min_age_days: i64, max_age_days: Option<i64>) -> AcceptanceReport {
        let now = Local::now();
        let mut report = AcceptanceReport {
            min_age_days,
            max_age_days,
            total_sent: 0,
            checked: 0,
            accepted: 0,
            pending: 0,
            connectable: 0,
            unknown: 0,
            blocked: 0,
            failed: 0,
            withdrawn: 0,
            unchecked: 0,
            by_source: BTreeMap::new(),
        };
        for invitation in &self.invitations {
            let age_days = now.signed_duration_since(invitation.sent_at).num_days();
            if age_days < min_age_days || max_age_days.is_some_and(|max| age_days > max) {
                continue;
            }
            report.add(
                &invitation.source,
                invitation.latest_status,
                invitation.latest_checked_at.is_some(),
            );
        }
        report
    }

    pub(crate) fn accepted_for_followup(
        &self,
        followups: &AcceptanceFollowupLedger,
        include_drafted: bool,
    ) -> Vec<AcceptedDraftCandidate> {
        self.invitations
            .iter()
            .filter(|invitation| invitation.latest_status == AcceptanceStatus::Accepted)
            .filter_map(|invitation| {
                let accepted_event = invitation
                    .history
                    .iter()
                    .rev()
                    .find(|event| event.status == AcceptanceStatus::Accepted);
                let accepted_at = accepted_event
                    .map(|event| event.at)
                    .or(invitation.latest_checked_at)?;
                let candidate = AcceptedDraftCandidate {
                    run_id: invitation.run_id,
                    run_date: invitation.run_date,
                    source: invitation.source.clone(),
                    name: invitation.name.clone(),
                    profile_url: invitation.profile_url.clone(),
                    sent_at: invitation.sent_at,
                    accepted_at,
                    relationship: accepted_event.and_then(|event| event.relationship.clone()),
                    acceptance_note: accepted_event.and_then(|event| event.note.clone()),
                    acceptance_evidence: accepted_event.and_then(|event| event.evidence.clone()),
                };
                if include_drafted || !followups.has_draft_for(&candidate) {
                    Some(candidate)
                } else {
                    None
                }
            })
            .collect()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AcceptanceKey {
    pub(crate) source: String,
    pub(crate) name: String,
    pub(crate) profile_url: Option<String>,
}

impl AcceptanceKey {
    pub(crate) fn from_parts(source: &str, name: &str, profile_url: Option<&str>) -> Self {
        Self {
            source: source.to_string(),
            name: name.to_string(),
            profile_url: profile_url.map(normalize_linkedin_url),
        }
    }
}

impl AcceptanceInvitation {
    pub(crate) fn key(&self) -> AcceptanceKey {
        AcceptanceKey::from_parts(&self.source, &self.name, self.profile_url.as_deref())
    }
}

#[derive(Debug, Default, Serialize)]
pub(crate) struct AcceptanceImportSummary {
    pub(crate) rows: u32,
    pub(crate) matched: u32,
    pub(crate) unmatched: u32,
}

#[derive(Debug, Default, Serialize)]
pub(crate) struct AcceptanceHistorySeedSummary {
    pub(crate) run_logs: u32,
    pub(crate) sent_events: u32,
    pub(crate) seeded: usize,
}

#[derive(Debug, Deserialize)]
pub(crate) struct ControllerEventLogEntry {
    pub(crate) at: DateTime<Local>,
    pub(crate) run_id: Uuid,
    pub(crate) kind: String,
    #[serde(default)]
    pub(crate) payload: serde_json::Value,
}

#[derive(Debug, Default, Serialize)]
pub(crate) struct AcceptanceReport {
    pub(crate) min_age_days: i64,
    pub(crate) max_age_days: Option<i64>,
    pub(crate) total_sent: u32,
    pub(crate) checked: u32,
    pub(crate) accepted: u32,
    pub(crate) pending: u32,
    pub(crate) connectable: u32,
    pub(crate) unknown: u32,
    pub(crate) blocked: u32,
    pub(crate) failed: u32,
    pub(crate) withdrawn: u32,
    pub(crate) unchecked: u32,
    pub(crate) by_source: BTreeMap<String, AcceptanceSourceReport>,
}

#[derive(Debug, Default, Clone, Serialize)]
pub(crate) struct AcceptanceSourceReport {
    pub(crate) total_sent: u32,
    pub(crate) checked: u32,
    pub(crate) accepted: u32,
    pub(crate) pending: u32,
    pub(crate) connectable: u32,
    pub(crate) unknown: u32,
    pub(crate) blocked: u32,
    pub(crate) failed: u32,
    pub(crate) withdrawn: u32,
    pub(crate) unchecked: u32,
}

impl AcceptanceReport {
    pub(crate) fn add(&mut self, source: &str, status: AcceptanceStatus, checked: bool) {
        self.total_sent += 1;
        let source_report = self.by_source.entry(source.to_string()).or_default();
        source_report.total_sent += 1;
        if checked {
            self.checked += 1;
            source_report.checked += 1;
        } else {
            self.unchecked += 1;
            source_report.unchecked += 1;
        }
        match status {
            AcceptanceStatus::Sent => {}
            AcceptanceStatus::Pending => {
                self.pending += 1;
                source_report.pending += 1;
            }
            AcceptanceStatus::Accepted => {
                self.accepted += 1;
                source_report.accepted += 1;
            }
            AcceptanceStatus::Connectable => {
                self.connectable += 1;
                source_report.connectable += 1;
            }
            AcceptanceStatus::Withdrawn => {
                self.withdrawn += 1;
                source_report.withdrawn += 1;
            }
            AcceptanceStatus::Unknown => {
                self.unknown += 1;
                source_report.unknown += 1;
            }
            AcceptanceStatus::Blocked => {
                self.blocked += 1;
                source_report.blocked += 1;
            }
            AcceptanceStatus::Failed => {
                self.failed += 1;
                source_report.failed += 1;
            }
        }
    }
}

impl PendingCleanupRun {
    pub(crate) fn new(max_withdrawals: u32, threshold_months: u32, date: NaiveDate) -> Self {
        let now = Local::now();
        Self {
            id: Uuid::new_v4(),
            date,
            max_withdrawals,
            threshold_months,
            state: PendingCleanupState::Started,
            start_audit: None,
            latest_audit: None,
            audits: Vec::new(),
            observations: Vec::new(),
            withdrawals: Vec::new(),
            notes: Vec::new(),
            created_at: now,
            updated_at: now,
        }
    }

    pub(crate) fn mark_updated(&mut self) {
        self.updated_at = Local::now();
    }

    pub(crate) fn withdrawn_count(&self) -> u32 {
        self.withdrawals
            .iter()
            .filter(|event| event.status == PendingWithdrawStatus::Withdrawn)
            .count() as u32
    }

    pub(crate) fn audited_delta(&self) -> Option<i64> {
        Some(i64::from(self.latest_audit?) - i64::from(self.start_audit?))
    }

    pub(crate) fn has_withdraw_event_for_observation(
        &self,
        observation: &PendingCandidateObservation,
    ) -> bool {
        self.withdrawals.iter().any(|event| {
            if let (Some(event_url), Some(observation_url)) =
                (&event.profile_url, &observation.profile_url)
            {
                event_url == observation_url
            } else {
                event.name == observation.name && event.age_text == observation.age_text
            }
        })
    }

    pub(crate) fn next_eligible_observation(&self) -> Option<&PendingCandidateObservation> {
        self.observations.iter().find(|observation| {
            observation.eligible && !self.has_withdraw_event_for_observation(observation)
        })
    }

    pub(crate) fn withdraw_capacity_remaining(&self) -> u32 {
        self.max_withdrawals.saturating_sub(self.withdrawn_count())
    }

    pub(crate) fn operator_plan(&self) -> PendingCleanupPlan {
        if self.state == PendingCleanupState::NeedsReaudit {
            return PendingCleanupPlan::Reaudit {
                reason: "cleanup is paused in NEEDS_REAUDIT".to_string(),
            };
        }
        if self.withdraw_capacity_remaining() == 0 {
            return PendingCleanupPlan::FinalAudit;
        }
        if let Some(candidate) = self.next_eligible_observation() {
            return PendingCleanupPlan::WithdrawCandidate {
                name: candidate.name.clone(),
                profile_url: candidate.profile_url.clone(),
                age_text: candidate.age_text.clone(),
                withdraw_capacity_remaining: self.withdraw_capacity_remaining(),
            };
        }
        PendingCleanupPlan::CaptureMore {
            reason: "no unrecorded eligible stale invitation is imported".to_string(),
        }
    }
}

#[derive(Debug, Serialize)]
#[serde(tag = "action", rename_all = "kebab-case")]
pub(crate) enum PendingCleanupPlan {
    CaptureMore {
        reason: String,
    },
    WithdrawCandidate {
        name: String,
        profile_url: Option<String>,
        age_text: String,
        withdraw_capacity_remaining: u32,
    },
    Reaudit {
        reason: String,
    },
    FinalAudit,
}

#[derive(Debug, Serialize)]
#[serde(tag = "action", rename_all = "kebab-case")]
pub(crate) enum OperatorPlan {
    UseReservoir {
        source: String,
        remaining: u32,
        available: usize,
    },
    CaptureSource {
        source: String,
        remaining: u32,
        capture: CaptureRecommendation,
        resume_url: Option<String>,
        cursor: Option<SourceCaptureCursor>,
    },
    SendCandidate {
        source: String,
        name: String,
        profile_url: Option<String>,
        real_send_capacity_remaining: u32,
    },
    Reaudit {
        reason: String,
    },
    FinalAudit,
    Blocked {
        reason: String,
    },
}

#[derive(Debug, Serialize)]
pub(crate) struct NextSource {
    pub(crate) name: String,
    pub(crate) quota: u32,
    pub(crate) verified: u32,
    pub(crate) remaining_for_source: u32,
    pub(crate) remaining_for_run: u32,
    pub(crate) fallback: bool,
}

pub(crate) const DEFAULT_SOURCE_MIX: [(&str, u32); 5] = [
    ("ASAP - Agency Owners Delivery", 9),
    ("ASAP - Contract Recruiters Staffing", 7),
    ("ASAP - Startup CTO Eng Leaders", 6),
    ("ASAP - High-Intent SaaS AI Founders", 5),
    ("ASAP - Vertical Proof Buyers", 3),
];

pub(crate) fn default_sources(target: u32) -> Vec<SourcePlan> {
    let default_target: u32 = DEFAULT_SOURCE_MIX.iter().map(|(_, weight)| *weight).sum();
    let primary = if target == default_target {
        DEFAULT_SOURCE_MIX.to_vec()
    } else {
        let mut allocated = Vec::new();
        let mut total = 0;
        for (name, weight) in DEFAULT_SOURCE_MIX {
            let count =
                ((target as f64) * (weight as f64) / (default_target as f64)).floor() as u32;
            allocated.push((name, count));
            total += count;
        }
        let mut remaining = target.saturating_sub(total);
        for item in allocated.iter_mut() {
            if remaining == 0 {
                break;
            }
            item.1 += 1;
            remaining -= 1;
        }
        allocated
    };

    let mut sources = primary
        .into_iter()
        .map(|(name, target)| SourcePlan {
            name: name.to_string(),
            target,
            fallback: false,
            exhausted: false,
        })
        .collect::<Vec<_>>();

    sources.push(SourcePlan {
        name: "FO - Founders - Urgent".to_string(),
        target: 0,
        fallback: true,
        exhausted: false,
    });

    sources
}
