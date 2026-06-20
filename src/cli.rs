use crate::*;

#[derive(Debug, Parser)]
#[command(name = "linkedin-network-run")]
#[command(about = "Durable run controller for LinkedIn Sales Navigator networking runs")]
pub(crate) struct Cli {
    #[arg(long, global = true, value_name = "DIR")]
    pub(crate) state_dir: Option<PathBuf>,

    #[command(subcommand)]
    pub(crate) command: Command,
}

#[derive(Debug, Subcommand)]
pub(crate) enum Command {
    Start {
        #[arg(long, default_value_t = 30)]
        target: u32,
        #[arg(long)]
        date: Option<NaiveDate>,
        #[arg(long)]
        force: bool,
        #[arg(long)]
        max_real_sends: Option<u32>,
    },
    Audit {
        people_count: u32,
        #[arg(long)]
        note: Option<String>,
    },
    ImportAudit {
        path: PathBuf,
    },
    Next,
    Record {
        #[arg(long)]
        source: String,
        #[arg(long)]
        name: String,
        #[arg(long)]
        profile_url: Option<String>,
        #[arg(long, value_enum)]
        status: CandidateStatus,
        #[arg(long)]
        note: Option<String>,
    },
    RecordSendResult {
        path: PathBuf,
    },
    SendNext {
        #[arg(long)]
        session: Option<String>,
        #[arg(
            long,
            visible_alias = "bunx",
            default_value = "/Users/hanifcarroll/.bun/bin/playwriter"
        )]
        playwriter: PathBuf,
        #[arg(
            long,
            default_value = "/Users/hanifcarroll/projects/tool/scripts/salesnav-send-one.js"
        )]
        script: PathBuf,
        #[arg(long, default_value = "/tmp/linkedin-network-run-send-next")]
        out_dir: PathBuf,
        #[arg(long)]
        dry_run: bool,
        #[arg(long)]
        allow_send: bool,
        #[arg(long)]
        no_record: bool,
    },
    SendGuarded {
        #[arg(long)]
        session: Option<String>,
        #[arg(
            long,
            visible_alias = "bunx",
            default_value = "/Users/hanifcarroll/.bun/bin/playwriter"
        )]
        playwriter: PathBuf,
        #[arg(
            long,
            default_value = "/Users/hanifcarroll/projects/tool/scripts/salesnav-send-one.js"
        )]
        script: PathBuf,
        #[arg(long, default_value = "/tmp/linkedin-network-run-send-guarded")]
        out_dir: PathBuf,
        #[arg(long, default_value_t = 30)]
        max_attempts: u32,
        #[arg(long)]
        dry_run: bool,
        #[arg(long)]
        single_pass: bool,
        #[arg(long)]
        allow_send: bool,
        #[arg(long)]
        no_record: bool,
    },
    DrainStaleCandidates {
        #[arg(long)]
        source: Option<String>,
    },
    ReconcileAudit {
        #[arg(long)]
        session: Option<String>,
        #[arg(
            long,
            visible_alias = "bunx",
            default_value = "/Users/hanifcarroll/.bun/bin/playwriter"
        )]
        playwriter: PathBuf,
        #[arg(
            long,
            default_value = "/Users/hanifcarroll/projects/tool/scripts/salesnav-audit.js"
        )]
        script: PathBuf,
        #[arg(long, default_value = "/tmp/linkedin-network-run-reconcile-audit")]
        out_dir: PathBuf,
        #[arg(long, default_value_t = 3)]
        attempts: u32,
        #[arg(long, default_value_t = 5000)]
        delay_ms: u64,
        #[arg(long)]
        finish: bool,
    },
    TopUpReconcile {
        #[arg(long)]
        session: Option<String>,
        #[arg(
            long,
            visible_alias = "bunx",
            default_value = "/Users/hanifcarroll/.bun/bin/playwriter"
        )]
        playwriter: PathBuf,
        #[arg(
            long,
            default_value = "/Users/hanifcarroll/projects/tool/scripts/salesnav-send-one.js"
        )]
        send_script: PathBuf,
        #[arg(
            long,
            default_value = "/Users/hanifcarroll/projects/tool/scripts/salesnav-audit.js"
        )]
        audit_script: PathBuf,
        #[arg(
            long,
            default_value = "/Users/hanifcarroll/projects/tool/scripts/salesnav-capture.js"
        )]
        capture_script: PathBuf,
        #[arg(long, default_value = "/tmp/linkedin-network-run-saved-searches.json")]
        saved_searches: PathBuf,
        #[arg(long, default_value = "FO - Founders - Urgent")]
        fallback_source: String,
        #[arg(long)]
        fallback_url: Option<String>,
        #[arg(long, default_value_t = 5)]
        fallback_pages: u32,
        #[arg(long, default_value_t = 10)]
        fallback_stop_after_connectable: u32,
        #[arg(long, default_value_t = 18)]
        fallback_limit: u32,
        #[arg(long, default_value_t = 250)]
        fallback_row_scroll_delay_ms: u32,
        #[arg(long)]
        no_fallback_capture: bool,
        #[arg(long, default_value = "/tmp/linkedin-network-run-top-up-reconcile")]
        out_dir: PathBuf,
        #[arg(long, default_value_t = 20)]
        max_attempts: u32,
        #[arg(long, default_value_t = 1000)]
        delay_ms: u64,
        #[arg(long)]
        allow_send: bool,
        #[arg(long)]
        finish: bool,
    },
    SourceExhausted {
        #[arg(long)]
        source: String,
        #[arg(long)]
        note: Option<String>,
    },
    NeedsReaudit {
        #[arg(long)]
        reason: String,
    },
    ImportCapture {
        path: PathBuf,
        #[arg(long)]
        only_connectable: bool,
    },
    RecordTopUpResult {
        path: PathBuf,
        #[arg(long)]
        note: Option<String>,
    },
    NextCandidate {
        #[arg(long)]
        json: bool,
    },
    Candidates {
        #[arg(long)]
        json: bool,
        #[arg(long)]
        status: Option<String>,
    },
    Plan {
        #[arg(long)]
        json: bool,
    },
    Status {
        #[arg(long)]
        json: bool,
    },
    Report,
    Finish {
        #[arg(long)]
        force: bool,
    },
    Acceptance {
        #[command(subcommand)]
        command: AcceptanceCommand,
    },
    Reservoir {
        #[command(subcommand)]
        command: ReservoirCommand,
    },
    TuneSources {
        #[arg(long, default_value_t = 50)]
        min_raw_rows: u32,
        #[arg(long, default_value_t = 0.05)]
        max_connectable_yield: f64,
        #[arg(long)]
        apply: bool,
    },
    PendingCleanup {
        #[command(subcommand)]
        command: PendingCleanupCommand,
    },
}

#[derive(Debug, Subcommand)]
pub(crate) enum AcceptanceCommand {
    Seed {
        #[arg(long)]
        include_unfinished: bool,
    },
    SeedHistory,
    Export {
        #[arg(long, default_value_t = 7)]
        min_age_days: i64,
        #[arg(long)]
        max_age_days: Option<i64>,
        #[arg(long, default_value = "/tmp/linkedin-acceptance-candidates.json")]
        out: PathBuf,
    },
    Import {
        path: PathBuf,
    },
    Report {
        #[arg(long, default_value_t = 0)]
        min_age_days: i64,
        #[arg(long)]
        max_age_days: Option<i64>,
        #[arg(long)]
        json: bool,
    },
    DraftFollowups {
        #[arg(long)]
        session: Option<String>,
        #[arg(
            long,
            visible_alias = "bunx",
            default_value = "/Users/hanifcarroll/.bun/bin/playwriter"
        )]
        playwriter: PathBuf,
        #[arg(
            long,
            default_value = "/Users/hanifcarroll/projects/tool/scripts/salesnav-accepted-research.js"
        )]
        research_script: PathBuf,
        #[arg(long)]
        research: Option<PathBuf>,
        #[arg(long)]
        out: Option<PathBuf>,
        #[arg(long, default_value = "/tmp/linkedin-accepted-followups")]
        out_dir: PathBuf,
        #[arg(long, value_enum, default_value_t = DraftStrategy::AsapContractV1)]
        strategy: DraftStrategy,
        #[arg(long)]
        include_drafted: bool,
        #[arg(long)]
        no_public_web: bool,
        #[arg(long, default_value_t = 5)]
        max_web_results: u32,
        #[arg(long, default_value_t = 500)]
        delay_ms: u64,
        #[arg(long, default_value_t = 120000)]
        playwriter_timeout_ms: u32,
    },
}

#[derive(Debug, Subcommand)]
pub(crate) enum ReservoirCommand {
    Capture {
        #[arg(long)]
        session: Option<String>,
        #[arg(
            long,
            visible_alias = "bunx",
            default_value = "/Users/hanifcarroll/.bun/bin/playwriter"
        )]
        playwriter: PathBuf,
        #[arg(
            long,
            default_value = "/Users/hanifcarroll/projects/tool/scripts/salesnav-capture.js"
        )]
        script: PathBuf,
        #[arg(long, default_value = "/tmp/linkedin-network-run-saved-searches.json")]
        saved_searches: PathBuf,
        #[arg(long)]
        source: String,
        #[arg(long)]
        url: Option<String>,
        #[arg(long, default_value = "/tmp/linkedin-network-run-reservoir-capture")]
        out_dir: PathBuf,
        #[arg(long, default_value_t = 5)]
        pages: u32,
        #[arg(long, default_value_t = 10)]
        stop_after_connectable: u32,
        #[arg(long, default_value_t = 18)]
        limit: u32,
        #[arg(long, default_value_t = 250)]
        row_scroll_delay_ms: u32,
        #[arg(long)]
        only_connectable: bool,
    },
    ImportCapture {
        path: PathBuf,
        #[arg(long)]
        only_connectable: bool,
    },
    FillRun {
        #[arg(long)]
        source: Option<String>,
        #[arg(long)]
        limit: Option<usize>,
    },
    Report {
        #[arg(long)]
        json: bool,
    },
    Clear {
        #[arg(long)]
        source: Option<String>,
    },
}

#[derive(Debug, Subcommand)]
pub(crate) enum PendingCleanupCommand {
    Start {
        #[arg(long, default_value_t = 75)]
        max_withdrawals: u32,
        #[arg(long, default_value_t = 2)]
        threshold_months: u32,
        #[arg(long)]
        date: Option<NaiveDate>,
        #[arg(long)]
        force: bool,
    },
    ImportAudit {
        path: PathBuf,
    },
    ImportCapture {
        path: PathBuf,
    },
    Plan {
        #[arg(long)]
        json: bool,
    },
    Next {
        #[arg(long)]
        json: bool,
    },
    RecordWithdrawResult {
        path: PathBuf,
    },
    WithdrawNext {
        #[arg(long)]
        session: Option<String>,
        #[arg(
            long,
            visible_alias = "bunx",
            default_value = "/Users/hanifcarroll/.bun/bin/playwriter"
        )]
        playwriter: PathBuf,
        #[arg(
            long,
            default_value = "/Users/hanifcarroll/projects/tool/scripts/salesnav-pending-withdraw-one.js"
        )]
        script: PathBuf,
        #[arg(long, default_value = "/tmp/linkedin-pending-cleanup-withdraw-next")]
        out_dir: PathBuf,
        #[arg(long)]
        dry_run: bool,
        #[arg(long)]
        allow_withdraw: bool,
        #[arg(long)]
        no_record: bool,
    },
    Status {
        #[arg(long)]
        json: bool,
    },
    Report,
    Finish {
        #[arg(long)]
        force: bool,
    },
}
