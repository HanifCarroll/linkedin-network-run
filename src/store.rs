use crate::*;

pub(crate) struct Store {
    pub(crate) dir: PathBuf,
}

impl Store {
    pub(crate) fn new(state_dir: Option<PathBuf>) -> Result<Self> {
        let dir = match state_dir {
            Some(path) => path,
            None => dirs::data_local_dir()
                .context("could not resolve local data directory")?
                .join(APP_DIR),
        };
        fs::create_dir_all(&dir).with_context(|| format!("creating {}", dir.display()))?;
        Ok(Self { dir })
    }

    pub(crate) fn active_path(&self) -> PathBuf {
        self.dir.join("active.json")
    }

    pub(crate) fn pending_active_path(&self) -> PathBuf {
        self.dir.join("pending-cleanup-active.json")
    }

    pub(crate) fn acceptance_ledger_path(&self) -> PathBuf {
        self.dir.join("acceptance-ledger.json")
    }

    pub(crate) fn acceptance_followup_ledger_path(&self) -> PathBuf {
        self.dir.join("acceptance-followups.json")
    }

    pub(crate) fn acceptance_followup_reports_dir(&self) -> PathBuf {
        self.dir.join("acceptance-followups")
    }

    pub(crate) fn default_acceptance_followup_report_path(&self) -> PathBuf {
        self.acceptance_followup_reports_dir()
            .join(format!("{}.md", Local::now().date_naive()))
    }

    pub(crate) fn acceptance_event_path(&self) -> PathBuf {
        self.dir.join("acceptance-events.jsonl")
    }

    pub(crate) fn reservoir_path(&self) -> PathBuf {
        self.dir.join("candidate-reservoir.json")
    }

    pub(crate) fn event_path(&self, run: &Run) -> PathBuf {
        self.dir.join(format!("{}.jsonl", run.id))
    }

    pub(crate) fn pending_event_path(&self, run: &PendingCleanupRun) -> PathBuf {
        self.dir.join(format!("pending-cleanup-{}.jsonl", run.id))
    }

    pub(crate) fn load(&self) -> Result<Run> {
        let raw = fs::read_to_string(self.active_path()).context("loading active run")?;
        let mut run: Run = serde_json::from_str(&raw).context("parsing active run")?;
        if run.max_real_sends == 0 {
            run.max_real_sends = run.target;
        }
        Ok(run)
    }

    pub(crate) fn save(&self, run: &Run) -> Result<()> {
        let path = self.active_path();
        let tmp = path.with_extension("json.tmp");
        fs::write(&tmp, serde_json::to_string_pretty(run)?)
            .with_context(|| format!("writing {}", tmp.display()))?;
        fs::rename(&tmp, &path).with_context(|| format!("replacing {}", path.display()))?;
        Ok(())
    }

    pub(crate) fn load_pending(&self) -> Result<PendingCleanupRun> {
        let raw = fs::read_to_string(self.pending_active_path())
            .context("loading active pending-cleanup run")?;
        serde_json::from_str(&raw).context("parsing active pending-cleanup run")
    }

    pub(crate) fn save_pending(&self, run: &PendingCleanupRun) -> Result<()> {
        let path = self.pending_active_path();
        let tmp = path.with_extension("json.tmp");
        fs::write(&tmp, serde_json::to_string_pretty(run)?)
            .with_context(|| format!("writing {}", tmp.display()))?;
        fs::rename(&tmp, &path).with_context(|| format!("replacing {}", path.display()))?;
        Ok(())
    }

    pub(crate) fn load_acceptance_ledger(&self) -> Result<AcceptanceLedger> {
        let path = self.acceptance_ledger_path();
        if !path.exists() {
            return Ok(AcceptanceLedger::default());
        }
        let raw =
            fs::read_to_string(&path).with_context(|| format!("loading {}", path.display()))?;
        serde_json::from_str(&raw).with_context(|| format!("parsing {}", path.display()))
    }

    pub(crate) fn save_acceptance_ledger(&self, ledger: &AcceptanceLedger) -> Result<()> {
        let path = self.acceptance_ledger_path();
        let tmp = path.with_extension("json.tmp");
        fs::write(&tmp, serde_json::to_string_pretty(ledger)?)
            .with_context(|| format!("writing {}", tmp.display()))?;
        fs::rename(&tmp, &path).with_context(|| format!("replacing {}", path.display()))?;
        Ok(())
    }

    pub(crate) fn load_acceptance_followup_ledger(&self) -> Result<AcceptanceFollowupLedger> {
        let path = self.acceptance_followup_ledger_path();
        if !path.exists() {
            return Ok(AcceptanceFollowupLedger::default());
        }
        let raw =
            fs::read_to_string(&path).with_context(|| format!("loading {}", path.display()))?;
        serde_json::from_str(&raw).with_context(|| format!("parsing {}", path.display()))
    }

    pub(crate) fn save_acceptance_followup_ledger(
        &self,
        ledger: &AcceptanceFollowupLedger,
    ) -> Result<()> {
        let path = self.acceptance_followup_ledger_path();
        let tmp = path.with_extension("json.tmp");
        fs::write(&tmp, serde_json::to_string_pretty(ledger)?)
            .with_context(|| format!("writing {}", tmp.display()))?;
        fs::rename(&tmp, &path).with_context(|| format!("replacing {}", path.display()))?;
        Ok(())
    }

    pub(crate) fn load_reservoir(&self) -> Result<CandidateReservoir> {
        let path = self.reservoir_path();
        if !path.exists() {
            return Ok(CandidateReservoir::default());
        }
        let raw =
            fs::read_to_string(&path).with_context(|| format!("loading {}", path.display()))?;
        serde_json::from_str(&raw).with_context(|| format!("parsing {}", path.display()))
    }

    pub(crate) fn save_reservoir(&self, reservoir: &CandidateReservoir) -> Result<()> {
        let path = self.reservoir_path();
        let tmp = path.with_extension("json.tmp");
        fs::write(&tmp, serde_json::to_string_pretty(reservoir)?)
            .with_context(|| format!("writing {}", tmp.display()))?;
        fs::rename(&tmp, &path).with_context(|| format!("replacing {}", path.display()))?;
        Ok(())
    }

    pub(crate) fn seed_acceptance_from_history(
        &self,
        ledger: &mut AcceptanceLedger,
    ) -> Result<AcceptanceHistorySeedSummary> {
        let mut summary = AcceptanceHistorySeedSummary::default();
        for entry in
            fs::read_dir(&self.dir).with_context(|| format!("reading {}", self.dir.display()))?
        {
            let path = entry
                .with_context(|| format!("reading entry in {}", self.dir.display()))?
                .path();
            if path.extension().and_then(|extension| extension.to_str()) != Some("jsonl") {
                continue;
            }
            let Some(stem) = path.file_stem().and_then(|stem| stem.to_str()) else {
                continue;
            };
            let Ok(run_id) = Uuid::parse_str(stem) else {
                continue;
            };
            let Some((run_date, events)) = sent_events_from_controller_log(&path, run_id)? else {
                continue;
            };
            summary.run_logs += 1;
            summary.sent_events += events.len() as u32;
            summary.seeded += ledger.upsert_from_events(run_id, run_date, &events);
        }
        Ok(summary)
    }

    pub(crate) fn append_event<T: Serialize>(
        &self,
        run: &Run,
        kind: &str,
        payload: &T,
    ) -> Result<()> {
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.event_path(run))
            .context("opening event log")?;
        let event = serde_json::json!({
            "at": Local::now(),
            "run_id": run.id,
            "kind": kind,
            "payload": payload,
        });
        writeln!(file, "{}", serde_json::to_string(&event)?).context("writing event log")?;
        Ok(())
    }

    pub(crate) fn append_acceptance_event<T: Serialize>(
        &self,
        kind: &str,
        payload: &T,
    ) -> Result<()> {
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.acceptance_event_path())
            .context("opening acceptance event log")?;
        let event = serde_json::json!({
            "at": Local::now(),
            "kind": kind,
            "payload": payload,
        });
        writeln!(file, "{}", serde_json::to_string(&event)?)
            .context("writing acceptance event log")?;
        Ok(())
    }

    pub(crate) fn append_pending_event<T: Serialize>(
        &self,
        run: &PendingCleanupRun,
        kind: &str,
        payload: &T,
    ) -> Result<()> {
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.pending_event_path(run))
            .context("opening pending-cleanup event log")?;
        let event = serde_json::json!({
            "at": Local::now(),
            "run_id": run.id,
            "kind": kind,
            "payload": payload,
        });
        writeln!(file, "{}", serde_json::to_string(&event)?)
            .context("writing pending-cleanup event log")?;
        Ok(())
    }
}
