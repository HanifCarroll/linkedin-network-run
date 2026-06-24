package outreach

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type DashboardReport struct {
	GeneratedAt        time.Time             `json:"generated_at"`
	Mode               string                `json:"mode"`
	RunID              string                `json:"run_id,omitempty"`
	RunStartedAt       *time.Time            `json:"run_started_at,omitempty"`
	RunCompletedAt     *time.Time            `json:"run_completed_at,omitempty"`
	StatePath          string                `json:"state_path"`
	DashboardPath      string                `json:"dashboard_path,omitempty"`
	TargetAgencies     int                   `json:"target_agencies"`
	TargetRecruiters   int                   `json:"target_recruiters"`
	AllowSend          bool                  `json:"allow_send"`
	LatestRun          *RunSummary           `json:"latest_run,omitempty"`
	Recommendation     RunRecommendation     `json:"recommendation"`
	LimitingReason     string                `json:"limiting_reason,omitempty"`
	Actions            []DailyLeadAction     `json:"actions"`
	Counts             StatusCounts          `json:"counts"`
	RunCounts          DashboardRunCounts    `json:"run_counts"`
	BacklogCounts      DashboardBucketCounts `json:"backlog_counts"`
	ReadyCounts        DashboardBucketCounts `json:"ready_counts"`
	LifetimeCounts     DashboardBucketCounts `json:"lifetime_counts"`
	AgencyFunnelCounts AgencyAccountFunnel   `json:"agency_funnel_counts"`
	AgencyDrilldown    AgencyDrilldownCounts `json:"agency_drilldown"`
	AgencySourceYields []AgencySourceYield   `json:"agency_source_yields"`
	ReadyAgencies      []Lead                `json:"ready_agencies"`
	ReadyRecruiters    []Lead                `json:"ready_recruiters"`
	ApprovedAgencies   []Lead                `json:"approved_agencies"`
	ApprovedRecruiters []Lead                `json:"approved_recruiters"`
	SentAgencies       []Lead                `json:"sent_agencies"`
	SentRecruiters     []Lead                `json:"sent_recruiters"`
	SkippedAgencies    []Lead                `json:"skipped_agencies"`
	SkippedRecruiters  []Lead                `json:"skipped_recruiters"`
}

type DashboardBucketCounts struct {
	Agencies   int `json:"agencies"`
	Recruiters int `json:"recruiters"`
}

type DashboardRunCounts struct {
	Sent               DashboardBucketCounts `json:"sent"`
	DryRunReady        DashboardBucketCounts `json:"dry_run_ready"`
	ConversationExists DashboardBucketCounts `json:"conversation_exists"`
	NotMessageable     DashboardBucketCounts `json:"not_messageable"`
	Blocked            DashboardBucketCounts `json:"blocked"`
	SendFailed         DashboardBucketCounts `json:"send_failed"`
}

type AgencyAccountFunnel struct {
	Qualified                     int `json:"qualified"`
	WithContacts                  int `json:"with_contacts"`
	WithMessageableOrSentContacts int `json:"with_messageable_or_sent_contacts"`
	ExhaustedWithoutContacts      int `json:"exhausted_without_contacts"`
	ExhaustedAfterContactAttempts int `json:"exhausted_after_contact_attempts"`
}

type AgencyDrilldownCounts struct {
	NotSearchedYet          int `json:"not_searched_yet"`
	SearchedFounderRecent   int `json:"searched_founder_recent"`
	SearchedExecutiveBroad  int `json:"searched_executive_broad"`
	SearchedResourceBroad   int `json:"searched_resource_broad"`
	ContactsFound           int `json:"contacts_found"`
	NoContactsFound         int `json:"no_contacts_found"`
	BrowserErrorRetryable   int `json:"browser_error_retryable"`
	QualifiedRemaining      int `json:"qualified_remaining"`
	ExhaustedWithoutContact int `json:"exhausted_without_contact"`
}

type AgencySourceYield struct {
	Source                   string `json:"source"`
	QualifiedAccounts        int    `json:"qualified_accounts"`
	NeedsReviewAccounts      int    `json:"needs_review_accounts"`
	RejectedAccounts         int    `json:"rejected_accounts"`
	ExhaustedAccounts        int    `json:"exhausted_accounts"`
	WebsiteContactCandidates int    `json:"website_contact_candidates"`
	GenericInboxes           int    `json:"generic_inboxes"`
	ContactForms             int    `json:"contact_forms"`
}

type DailyLeadAction struct {
	At            time.Time     `json:"at"`
	RunID         string        `json:"run_id,omitempty"`
	Bucket        string        `json:"bucket"`
	LeadID        string        `json:"lead_id"`
	Name          string        `json:"name"`
	ProfileURL    *string       `json:"profile_url"`
	LeadType      LeadType      `json:"lead_type"`
	MessageStatus MessageStatus `json:"message_status"`
	Action        string        `json:"action"`
	Result        string        `json:"result"`
	Note          *string       `json:"note"`
}

type DashboardBuildOptions struct {
	Mode             string
	RunID            string
	RunStartedAt     *time.Time
	RunCompletedAt   *time.Time
	DashboardPath    string
	TargetAgencies   int
	TargetRecruiters int
	AllowSend        bool
	Actions          []DailyLeadAction
	IncludeLatestRun bool
	Recommendation   *RunRecommendation
}

func BuildDashboardReport(state OutreachState, statePath string, targetAgencies int, targetRecruiters int, allowSend bool, actions []DailyLeadAction) DashboardReport {
	return BuildDashboardReportWithOptions(state, statePath, DashboardBuildOptions{
		Mode:             "run",
		TargetAgencies:   targetAgencies,
		TargetRecruiters: targetRecruiters,
		AllowSend:        allowSend,
		Actions:          actions,
	})
}

func BuildDashboardReportWithOptions(state OutreachState, statePath string, options DashboardBuildOptions) DashboardReport {
	state.Normalize()
	mode := cleanText(options.Mode)
	if mode == "" {
		mode = "run"
	}
	var latestRun *RunSummary
	if options.IncludeLatestRun {
		if summary, ok := LatestRunSummary(state, statePath); ok {
			latestRun = &summary
		}
	}
	recommendation := RunRecommendation{}
	if options.Recommendation != nil {
		recommendation = *options.Recommendation
	} else if latestRun != nil {
		recommendation = latestRun.Recommendation
	} else {
		recommendation = RecommendNextRun(state, statePath, options.TargetAgencies, options.TargetRecruiters, options.AllowSend)
	}
	return DashboardReport{
		GeneratedAt:      time.Now(),
		Mode:             mode,
		RunID:            options.RunID,
		RunStartedAt:     options.RunStartedAt,
		RunCompletedAt:   options.RunCompletedAt,
		StatePath:        statePath,
		DashboardPath:    options.DashboardPath,
		TargetAgencies:   options.TargetAgencies,
		TargetRecruiters: options.TargetRecruiters,
		AllowSend:        options.AllowSend,
		LatestRun:        latestRun,
		Recommendation:   recommendation,
		LimitingReason:   dashboardLimitingReason(state, options.TargetAgencies, options.TargetRecruiters, options.AllowSend, options.Actions),
		Actions:          options.Actions,
		Counts:           Counts(state),
		RunCounts:        dashboardRunCounts(options.Actions),
		BacklogCounts: DashboardBucketCounts{
			Agencies:   dashboardBucketCount(state, "agency", MessageStatusDrafted),
			Recruiters: dashboardBucketCount(state, "recruiter", MessageStatusDrafted),
		},
		ReadyCounts: DashboardBucketCounts{
			Agencies:   dashboardBucketCount(state, "agency", MessageStatusDryRunReady),
			Recruiters: dashboardBucketCount(state, "recruiter", MessageStatusDryRunReady),
		},
		LifetimeCounts: DashboardBucketCounts{
			Agencies:   dashboardBucketCount(state, "agency", MessageStatusSent),
			Recruiters: dashboardBucketCount(state, "recruiter", MessageStatusSent),
		},
		AgencyFunnelCounts: agencyAccountFunnelCounts(state),
		AgencyDrilldown:    agencyDrilldownCounts(state),
		AgencySourceYields: agencySourceYields(state),
		ReadyAgencies:      dashboardLeads(state, "agency", MessageStatusDryRunReady),
		ReadyRecruiters:    dashboardLeads(state, "recruiter", MessageStatusDryRunReady),
		ApprovedAgencies:   dashboardLeads(state, "agency", MessageStatusApproved),
		ApprovedRecruiters: dashboardLeads(state, "recruiter", MessageStatusApproved),
		SentAgencies:       dashboardLeads(state, "agency", MessageStatusSent),
		SentRecruiters:     dashboardLeads(state, "recruiter", MessageStatusSent),
		SkippedAgencies:    dashboardSkippedLeads(state, "agency"),
		SkippedRecruiters:  dashboardSkippedLeads(state, "recruiter"),
	}
}

func RenderDashboardMarkdown(report DashboardReport) string {
	lines := []string{
		fmt.Sprintf("# Recruiter And Agency Outreach %s", report.GeneratedAt.Format("2006-01-02")),
		"",
		fmt.Sprintf("- Generated: `%s`", report.GeneratedAt.Format(time.RFC3339)),
		fmt.Sprintf("- Mode: `%s`", report.Mode),
	}
	if report.Mode == "render" {
		lines = append(lines, "- Dashboard render only; no send run executed.")
	}
	if report.RunID != "" {
		lines = append(lines, fmt.Sprintf("- Run ID: `%s`", report.RunID))
	}
	if report.RunStartedAt != nil {
		lines = append(lines, fmt.Sprintf("- Run started: `%s`", report.RunStartedAt.Format(time.RFC3339)))
	}
	if report.RunCompletedAt != nil {
		lines = append(lines, fmt.Sprintf("- Run completed: `%s`", report.RunCompletedAt.Format(time.RFC3339)))
	}
	lines = append(lines,
		fmt.Sprintf("- State: `%s`", report.StatePath),
	)
	if report.DashboardPath != "" {
		lines = append(lines, fmt.Sprintf("- Dashboard path: `%s`", report.DashboardPath))
	}
	lines = append(lines,
		fmt.Sprintf("- This-run target: `%d` agencies, `%d` recruiters", report.TargetAgencies, report.TargetRecruiters),
		fmt.Sprintf("- Real sends enabled: `%t`", report.AllowSend),
		fmt.Sprintf("- This-run sent: `%d` agencies, `%d` recruiters", report.RunCounts.Sent.Agencies, report.RunCounts.Sent.Recruiters),
		fmt.Sprintf("- This-run checked/skipped: conversation_exists `%d` agencies, `%d` recruiters; not_messageable `%d` agencies, `%d` recruiters; blocked `%d` agencies, `%d` recruiters; send_failed `%d` agencies, `%d` recruiters",
			report.RunCounts.ConversationExists.Agencies,
			report.RunCounts.ConversationExists.Recruiters,
			report.RunCounts.NotMessageable.Agencies,
			report.RunCounts.NotMessageable.Recruiters,
			report.RunCounts.Blocked.Agencies,
			report.RunCounts.Blocked.Recruiters,
			report.RunCounts.SendFailed.Agencies,
			report.RunCounts.SendFailed.Recruiters,
		),
		fmt.Sprintf("- Ready now: `%d` agencies, `%d` recruiters", report.ReadyCounts.Agencies, report.ReadyCounts.Recruiters),
		fmt.Sprintf("- Backlog drafted/needs validation: `%d` agencies, `%d` recruiters", report.BacklogCounts.Agencies, report.BacklogCounts.Recruiters),
		fmt.Sprintf("- Manually approved: `%d` agencies, `%d` recruiters", len(report.ApprovedAgencies), len(report.ApprovedRecruiters)),
		fmt.Sprintf("- Lifetime sent: `%d` agencies, `%d` recruiters", report.LifetimeCounts.Agencies, report.LifetimeCounts.Recruiters),
		fmt.Sprintf("- Lifetime checked/skipped: `%d` agencies, `%d` recruiters", len(report.SkippedAgencies), len(report.SkippedRecruiters)),
		fmt.Sprintf("- Agency accounts: `%d` qualified, `%d` needs review, `%d` rejected, `%d` exhausted",
			report.Counts.ByAgencyAccountStatus[AgencyAccountStatusQualified],
			report.Counts.ByAgencyAccountStatus[AgencyAccountStatusNeedsReview],
			report.Counts.ByAgencyAccountStatus[AgencyAccountStatusRejected],
			report.Counts.ByAgencyAccountStatus[AgencyAccountStatusExhausted],
		),
		fmt.Sprintf("- Agency contactability: `%d` qualified accounts, `%d` with contacts, `%d` with messageable/sent contacts, `%d` exhausted with no contacts, `%d` exhausted after contact attempts",
			report.AgencyFunnelCounts.Qualified,
			report.AgencyFunnelCounts.WithContacts,
			report.AgencyFunnelCounts.WithMessageableOrSentContacts,
			report.AgencyFunnelCounts.ExhaustedWithoutContacts,
			report.AgencyFunnelCounts.ExhaustedAfterContactAttempts,
		),
		fmt.Sprintf("- Agency drilldown: not searched `%d`; searched founder/recent `%d`; searched executive/delivery broad `%d`; searched resource/delivery broad `%d`; contacts found `%d`; no contacts found `%d`; browser error retryable `%d`",
			report.AgencyDrilldown.NotSearchedYet,
			report.AgencyDrilldown.SearchedFounderRecent,
			report.AgencyDrilldown.SearchedExecutiveBroad,
			report.AgencyDrilldown.SearchedResourceBroad,
			report.AgencyDrilldown.ContactsFound,
			report.AgencyDrilldown.NoContactsFound,
			report.AgencyDrilldown.BrowserErrorRetryable,
		),
		fmt.Sprintf("- Agency review-only contacts: `%d` website_contact_candidate, `%d` generic_inbox, `%d` contact_form",
			report.Counts.ByAgencyContactCandidateStatus[AgencyContactCandidateStatusWebsiteContactCandidate],
			report.Counts.ByAgencyContactCandidateStatus[AgencyContactCandidateStatusGenericInbox],
			report.Counts.ByAgencyContactCandidateStatus[AgencyContactCandidateStatusContactForm],
		),
		fmt.Sprintf("- Agency contact review: `%d` needs_review, `%d` approved, `%d` rejected, `%d` converted",
			report.Counts.ByAgencyContactCandidateReviewStatus[AgencyContactReviewStatusNeedsReview],
			report.Counts.ByAgencyContactCandidateReviewStatus[AgencyContactReviewStatusApproved],
			report.Counts.ByAgencyContactCandidateReviewStatus[AgencyContactReviewStatusRejected],
			report.Counts.ByAgencyContactCandidateReviewStatus[AgencyContactReviewStatusConverted],
		),
		"",
	)
	lines = append(lines,
		"## Sourcing Readiness",
		"",
		fmt.Sprintf("- Ready to send: `%d` agencies, `%d` recruiters", report.ReadyCounts.Agencies, report.ReadyCounts.Recruiters),
		fmt.Sprintf("- Drafted/needs validation: `%d` agencies, `%d` recruiters", report.BacklogCounts.Agencies, report.BacklogCounts.Recruiters),
		fmt.Sprintf("- Manually approved: `%d` agencies, `%d` recruiters", len(report.ApprovedAgencies), len(report.ApprovedRecruiters)),
		fmt.Sprintf("- Agency accounts: `%d` qualified, `%d` needs review, `%d` rejected, `%d` exhausted",
			report.Counts.ByAgencyAccountStatus[AgencyAccountStatusQualified],
			report.Counts.ByAgencyAccountStatus[AgencyAccountStatusNeedsReview],
			report.Counts.ByAgencyAccountStatus[AgencyAccountStatusRejected],
			report.Counts.ByAgencyAccountStatus[AgencyAccountStatusExhausted],
		),
		"",
		"## Send Results",
		"",
		fmt.Sprintf("- This-run sent: `%d` agencies, `%d` recruiters", report.RunCounts.Sent.Agencies, report.RunCounts.Sent.Recruiters),
		fmt.Sprintf("- This-run skipped: conversation_exists `%d` agencies, `%d` recruiters; not_messageable `%d` agencies, `%d` recruiters; blocked `%d` agencies, `%d` recruiters; send_failed `%d` agencies, `%d` recruiters",
			report.RunCounts.ConversationExists.Agencies,
			report.RunCounts.ConversationExists.Recruiters,
			report.RunCounts.NotMessageable.Agencies,
			report.RunCounts.NotMessageable.Recruiters,
			report.RunCounts.Blocked.Agencies,
			report.RunCounts.Blocked.Recruiters,
			report.RunCounts.SendFailed.Agencies,
			report.RunCounts.SendFailed.Recruiters,
		),
		fmt.Sprintf("- Lifetime sent: `%d` agencies, `%d` recruiters", report.LifetimeCounts.Agencies, report.LifetimeCounts.Recruiters),
		fmt.Sprintf("- Lifetime checked/skipped: `%d` agencies, `%d` recruiters", len(report.SkippedAgencies), len(report.SkippedRecruiters)),
		"",
	)
	if len(report.AgencySourceYields) > 0 {
		lines = append(lines, "- Agency source yield: "+renderAgencySourceYieldsInline(report.AgencySourceYields), "")
	}
	if report.LimitingReason != "" {
		lines = append(lines, "- Limiting reason: "+cleanInline(report.LimitingReason), "")
	}
	if report.LatestRun != nil {
		lines = append(lines,
			"## Latest Run",
			"",
			fmt.Sprintf("- Run ID: `%s`", report.LatestRun.RunID),
			fmt.Sprintf("- Status: `%s`", report.LatestRun.Status),
			fmt.Sprintf("- Started: `%s`", report.LatestRun.StartedAt.Format(time.RFC3339)),
			fmt.Sprintf("- Sent: `%d` agencies, `%d` recruiters", report.LatestRun.Counts.Sent.Agencies, report.LatestRun.Counts.Sent.Recruiters),
		)
		if !report.LatestRun.CompletedAt.IsZero() {
			lines = append(lines, fmt.Sprintf("- Completed: `%s`", report.LatestRun.CompletedAt.Format(time.RFC3339)))
		}
		if report.LatestRun.Blocker != "" {
			lines = append(lines, "- Blocker: "+cleanInline(report.LatestRun.Blocker))
		}
		lines = append(lines, "")
	}
	if report.Recommendation.ShouldRetry {
		lines = append(lines,
			"## Recommended Next Run",
			"",
			"- Reason: "+cleanInline(report.Recommendation.Reason),
			"- Command: `"+cleanInline(report.Recommendation.Command)+"`",
			"",
		)
	} else if cleanText(report.Recommendation.Reason) != "" {
		lines = append(lines, "## Recommended Next Run", "", "- "+cleanInline(report.Recommendation.Reason), "")
	}
	if len(report.Actions) > 0 {
		lines = append(lines, "## Run Actions", "")
		for _, action := range report.Actions {
			name := cleanInline(action.Name)
			note := ""
			if action.Note != nil && cleanText(*action.Note) != "" {
				note = " - " + cleanInline(*action.Note)
			}
			lines = append(lines, fmt.Sprintf("- `%s` `%s` `%s`: %s -> `%s`%s", action.Bucket, action.Action, action.LeadID, name, action.Result, note))
		}
		lines = append(lines, "")
	}
	lines = append(lines, "## Agencies", "")
	lines = append(lines, renderLeadCards("messageable/sendable", report.ReadyAgencies)...)
	lines = append(lines, renderLeadCards("manually approved", report.ApprovedAgencies)...)
	lines = append(lines, renderLeadCards("sent", report.SentAgencies)...)
	lines = append(lines, renderLeadCards("checked/skipped", report.SkippedAgencies)...)
	lines = append(lines, "## Recruiters", "")
	lines = append(lines, renderLeadCards("messageable/sendable", report.ReadyRecruiters)...)
	lines = append(lines, renderLeadCards("manually approved", report.ApprovedRecruiters)...)
	lines = append(lines, renderLeadCards("sent", report.SentRecruiters)...)
	lines = append(lines, renderLeadCards("checked/skipped", report.SkippedRecruiters)...)
	if len(report.ReadyAgencies)+len(report.ReadyRecruiters)+len(report.ApprovedAgencies)+len(report.ApprovedRecruiters)+len(report.SentAgencies)+len(report.SentRecruiters)+len(report.SkippedAgencies)+len(report.SkippedRecruiters) == 0 {
		lines = append(lines, "No messageable, approved, or sent recruiter/agency leads yet.")
	}
	return strings.Join(lines, "\n")
}

func WriteDashboardMarkdown(path string, report DashboardReport) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", filepath.Dir(path), err)
	}
	if err := os.WriteFile(path, []byte(RenderDashboardMarkdown(report)), 0o644); err != nil {
		return fmt.Errorf("writing %s: %w", path, err)
	}
	return nil
}

func WriteDashboardMarkdownAliases(paths []string, report DashboardReport) error {
	seen := map[string]bool{}
	for _, path := range paths {
		if cleanText(path) == "" || seen[path] {
			continue
		}
		seen[path] = true
		if err := WriteDashboardMarkdown(path, report); err != nil {
			return err
		}
	}
	return nil
}

func dashboardRunCounts(actions []DailyLeadAction) DashboardRunCounts {
	counts := DashboardRunCounts{}
	for _, action := range actions {
		var selected *DashboardBucketCounts
		switch action.Result {
		case "sent-clicked":
			selected = &counts.Sent
		case "dry-run-messageable":
			selected = &counts.DryRunReady
		case "conversation-exists":
			selected = &counts.ConversationExists
		case "not-messageable":
			selected = &counts.NotMessageable
		case "blocked":
			selected = &counts.Blocked
		case "send-button-missing", "composer-missing", "identity-mismatch":
			selected = &counts.SendFailed
		}
		if selected == nil {
			continue
		}
		switch action.Bucket {
		case "agency":
			selected.Agencies++
		case "recruiter":
			selected.Recruiters++
		}
	}
	return counts
}

func dashboardLimitingReason(state OutreachState, targetAgencies int, targetRecruiters int, allowSend bool, actions []DailyLeadAction) string {
	state.Normalize()
	if allowSend {
		agencyGap := targetAgencies - sentCountFromActions(actions, "agency")
		if agencyGap > 0 && targetAgencies > 0 {
			return agencyStageLimitingReason(state, agencyGap, "sends")
		}
		recruiterGap := targetRecruiters - sentCountFromActions(actions, "recruiter")
		if recruiterGap > 0 && targetRecruiters > 0 {
			return fmt.Sprintf("Recruiter target is short by %d sends. Current recruiter backlog: %d drafted, %d ready, %d conversation_exists, %d not_messageable, %d blocked, %d send_failed.",
				recruiterGap,
				dashboardBucketCount(state, "recruiter", MessageStatusDrafted),
				dashboardBucketCount(state, "recruiter", MessageStatusDryRunReady),
				dashboardBucketCount(state, "recruiter", MessageStatusConversationExists),
				dashboardBucketCount(state, "recruiter", MessageStatusNotMessageable),
				dashboardBucketCount(state, "recruiter", MessageStatusBlocked),
				dashboardBucketCount(state, "recruiter", MessageStatusSendFailed),
			)
		}
		return ""
	}
	if targetAgencies > 0 && readyCount(state, "agency") < targetAgencies {
		return agencyStageLimitingReason(state, targetAgencies-readyCount(state, "agency"), "ready-to-send leads")
	}
	if targetRecruiters > 0 && readyCount(state, "recruiter") < targetRecruiters {
		return fmt.Sprintf("Recruiter ready-to-send pool is short by %d for this render target. Current recruiter queue: %d drafted/needs validation, %d ready. The remaining send goal is shown under Recommended Next Run.",
			targetRecruiters-readyCount(state, "recruiter"),
			dashboardBucketCount(state, "recruiter", MessageStatusDrafted),
			readyCount(state, "recruiter"),
		)
	}
	return ""
}

func agencyStageLimitingReason(state OutreachState, gap int, unit string) string {
	diagnosis := BuildAgencyPoolDiagnosis(state, "", 0)
	drafted := dashboardBucketCount(state, "agency", MessageStatusDrafted)
	ready := readyCount(state, "agency")
	approved := len(agencyContactCandidatesReadyForPromotion(state))
	review := len(agencyContactCandidatesNeedingReview(state))
	prefix := fmt.Sprintf("Agency target is short by %d %s. ", gap, unit)
	suffix := ""
	if unit == "ready-to-send leads" {
		prefix = fmt.Sprintf("Agency ready-to-send pool is short by %d for this render target. ", gap)
		suffix = " The remaining send goal is shown under Recommended Next Run."
	}
	switch {
	case ready > 0:
		return prefix + fmt.Sprintf("Current limiting stage: send. There are %d dry_run_ready agency lead(s) available.", ready) + suffix
	case drafted > 0:
		return prefix + fmt.Sprintf("Current limiting stage: validation. There are %d drafted agency lead(s) that need dry-run messageability checks.", drafted) + suffix
	case approved > 0:
		return prefix + fmt.Sprintf("Current limiting stage: promotion. There are %d approved website contact candidate(s) ready to promote into drafted leads.", approved) + suffix
	case review > 0:
		return prefix + fmt.Sprintf("Current limiting stage: contact review. There are %d personal LinkedIn website contact candidate(s) awaiting review.", review) + suffix
	case diagnosis.WebsiteCandidates > 0:
		return prefix + fmt.Sprintf("Current limiting stage: website enrichment. There are %d agency account(s) with websites that can be checked for explicit review-only contacts.", diagnosis.WebsiteCandidates) + suffix
	case diagnosis.Drilldown.QualifiedRemaining > 0:
		return prefix + fmt.Sprintf("Current limiting stage: account contact search. There are %d qualified agency account(s) remaining for LinkedIn account-scoped contact search.", diagnosis.Drilldown.QualifiedRemaining) + suffix
	default:
		return prefix + "Current limiting stage: source accounts. Import or collect a new agency source artifact before another browser-heavy contact search." + suffix
	}
}

func agencyAccountFunnelCounts(state OutreachState) AgencyAccountFunnel {
	state.Normalize()
	accountsWithContacts := map[string]bool{}
	accountsWithMessageableOrSentContacts := map[string]bool{}
	for _, lead := range state.Leads {
		if bucketForLead(lead) != "agency" || lead.AgencyAccountID == nil || cleanText(*lead.AgencyAccountID) == "" || lead.Status != LeadStatusEligible {
			continue
		}
		accountID := cleanText(*lead.AgencyAccountID)
		accountsWithContacts[accountID] = true
		switch lead.MessageStatus {
		case MessageStatusDryRunReady, MessageStatusSent, MessageStatusManuallySent:
			accountsWithMessageableOrSentContacts[accountID] = true
		}
	}
	counts := AgencyAccountFunnel{}
	for _, account := range state.AgencyAccounts {
		switch account.Status {
		case AgencyAccountStatusQualified:
			counts.Qualified++
		case AgencyAccountStatusExhausted:
			if !accountsWithContacts[account.ID] {
				counts.ExhaustedWithoutContacts++
			}
			if account.ContactCaptureCount > 0 {
				counts.ExhaustedAfterContactAttempts++
			}
		}
		if accountsWithContacts[account.ID] {
			counts.WithContacts++
		}
		if accountsWithMessageableOrSentContacts[account.ID] {
			counts.WithMessageableOrSentContacts++
		}
	}
	return counts
}

func agencyDrilldownCounts(state OutreachState) AgencyDrilldownCounts {
	state.Normalize()
	contactsByAccount := map[string]int{}
	for _, lead := range state.Leads {
		if lead.AgencyAccountID == nil || cleanText(*lead.AgencyAccountID) == "" || lead.Status != LeadStatusEligible || bucketForLead(lead) != "agency" {
			continue
		}
		contactsByAccount[cleanText(*lead.AgencyAccountID)]++
	}
	counts := AgencyDrilldownCounts{}
	for _, account := range state.AgencyAccounts {
		hasContacts := contactsByAccount[account.ID] > 0
		if account.Status == AgencyAccountStatusQualified {
			counts.QualifiedRemaining++
			switch {
			case account.ContactCaptureCount <= 0:
				counts.NotSearchedYet++
			case account.ContactCaptureCount == 1:
				counts.SearchedFounderRecent++
			case account.ContactCaptureCount == 2:
				counts.SearchedExecutiveBroad++
			default:
				counts.SearchedResourceBroad++
			}
			if account.LastContactError != nil && cleanText(*account.LastContactError) != "" {
				counts.BrowserErrorRetryable++
			}
		}
		if hasContacts {
			counts.ContactsFound++
		}
		if account.ContactCaptureCount > 0 && !hasContacts {
			counts.NoContactsFound++
		}
		if account.Status == AgencyAccountStatusExhausted && !hasContacts {
			counts.ExhaustedWithoutContact++
		}
	}
	return counts
}

func agencySourceYields(state OutreachState) []AgencySourceYield {
	state.Normalize()
	bySource := map[string]*AgencySourceYield{}
	get := func(source string) *AgencySourceYield {
		cleaned := cleanText(source)
		if cleaned == "" {
			cleaned = "unknown"
		}
		item := bySource[cleaned]
		if item == nil {
			item = &AgencySourceYield{Source: cleaned}
			bySource[cleaned] = item
		}
		return item
	}
	for _, account := range state.AgencyAccounts {
		item := get(account.Source)
		switch account.Status {
		case AgencyAccountStatusQualified:
			item.QualifiedAccounts++
		case AgencyAccountStatusNeedsReview:
			item.NeedsReviewAccounts++
		case AgencyAccountStatusRejected:
			item.RejectedAccounts++
		case AgencyAccountStatusExhausted:
			item.ExhaustedAccounts++
		}
	}
	for _, candidate := range state.AgencyContactCandidates {
		item := get(candidate.Source)
		switch candidate.Status {
		case AgencyContactCandidateStatusWebsiteContactCandidate:
			item.WebsiteContactCandidates++
		case AgencyContactCandidateStatusGenericInbox:
			item.GenericInboxes++
		case AgencyContactCandidateStatusContactForm:
			item.ContactForms++
		}
	}
	yields := []AgencySourceYield{}
	for _, item := range bySource {
		yields = append(yields, *item)
	}
	sort.SliceStable(yields, func(i, j int) bool {
		leftTotal := yields[i].QualifiedAccounts + yields[i].NeedsReviewAccounts + yields[i].RejectedAccounts + yields[i].ExhaustedAccounts + yields[i].WebsiteContactCandidates + yields[i].GenericInboxes + yields[i].ContactForms
		rightTotal := yields[j].QualifiedAccounts + yields[j].NeedsReviewAccounts + yields[j].RejectedAccounts + yields[j].ExhaustedAccounts + yields[j].WebsiteContactCandidates + yields[j].GenericInboxes + yields[j].ContactForms
		if leftTotal != rightTotal {
			return leftTotal > rightTotal
		}
		return yields[i].Source < yields[j].Source
	})
	return yields
}

func renderAgencySourceYieldsInline(yields []AgencySourceYield) string {
	parts := []string{}
	for _, yield := range yields {
		parts = append(parts, fmt.Sprintf("%s accounts q%d/nr%d/r%d/ex%d contacts website_contact_candidate%d/generic_inbox%d/contact_form%d",
			cleanInline(yield.Source),
			yield.QualifiedAccounts,
			yield.NeedsReviewAccounts,
			yield.RejectedAccounts,
			yield.ExhaustedAccounts,
			yield.WebsiteContactCandidates,
			yield.GenericInboxes,
			yield.ContactForms,
		))
	}
	return strings.Join(parts, "; ")
}

func dashboardBucketCount(state OutreachState, bucket string, messageStatus MessageStatus) int {
	count := 0
	for _, lead := range state.Leads {
		if dashboardLeadMatchesBucket(state, lead, bucket, messageStatus) {
			count++
		}
	}
	return count
}

func dashboardLeads(state OutreachState, bucket string, messageStatus MessageStatus) []Lead {
	leads := []Lead{}
	for _, lead := range state.Leads {
		if !dashboardLeadMatchesBucket(state, lead, bucket, messageStatus) {
			continue
		}
		leads = append(leads, lead)
	}
	sort.SliceStable(leads, func(i, j int) bool {
		if leads[i].FitScore == leads[j].FitScore {
			return leads[i].Name < leads[j].Name
		}
		return leads[i].FitScore > leads[j].FitScore
	})
	return leads
}

func dashboardSkippedLeads(state OutreachState, bucket string) []Lead {
	leads := []Lead{}
	for _, lead := range state.Leads {
		switch lead.MessageStatus {
		case MessageStatusConversationExists, MessageStatusNotMessageable, MessageStatusBlocked, MessageStatusSendFailed:
			if !dashboardLeadMatchesBucket(state, lead, bucket, lead.MessageStatus) {
				continue
			}
			leads = append(leads, lead)
		}
	}
	sort.SliceStable(leads, func(i, j int) bool {
		if leads[i].FitScore == leads[j].FitScore {
			return leads[i].Name < leads[j].Name
		}
		return leads[i].FitScore > leads[j].FitScore
	})
	return leads
}

func dashboardLeadMatchesBucket(state OutreachState, lead Lead, bucket string, messageStatus MessageStatus) bool {
	if lead.MessageStatus != messageStatus || bucketForLead(lead) != bucket {
		return false
	}
	switch messageStatus {
	case MessageStatusSent, MessageStatusManuallySent, MessageStatusConversationExists, MessageStatusNotMessageable, MessageStatusBlocked, MessageStatusSendFailed:
		return lead.Status == LeadStatusEligible
	default:
		return leadMatchesSendableBucket(state, lead, bucket)
	}
}

func renderLeadCards(label string, leads []Lead) []string {
	lines := []string{}
	if len(leads) == 0 {
		return lines
	}
	lines = append(lines, fmt.Sprintf("### %s", titleLabel(label)), "")
	for _, lead := range leads {
		lines = append(lines, "#### "+cleanInline(lead.Name))
		lines = append(lines, "- ID: `"+lead.ID+"`")
		lines = append(lines, "- Type: `"+string(lead.LeadType)+"`")
		lines = append(lines, fmt.Sprintf("- Score: `%d`", lead.FitScore))
		lines = append(lines, "- Message status: `"+string(lead.MessageStatus)+"`")
		if lead.ProfileURL != nil {
			lines = append(lines, "- Profile: "+cleanInline(*lead.ProfileURL))
		}
		if lead.Title != nil {
			lines = append(lines, "- Title: "+cleanInline(*lead.Title))
		}
		if lead.Company != nil {
			lines = append(lines, "- Company: "+cleanInline(*lead.Company))
		}
		if lead.AgencyAccountName != nil {
			lines = append(lines, "- Agency account: "+cleanInline(*lead.AgencyAccountName))
		}
		if lead.AgencyAccountURL != nil {
			lines = append(lines, "- Agency account URL: "+cleanInline(*lead.AgencyAccountURL))
		}
		if len(lead.AgencyAccountReasons) > 0 {
			lines = append(lines, "- Agency account reasons: "+cleanInline(strings.Join(lead.AgencyAccountReasons, "; ")))
		}
		if lead.AgencyAccountEvidence != "" {
			lines = append(lines, "- Agency account evidence: "+cleanInline(lead.AgencyAccountEvidence))
		}
		if len(lead.FitReasons) > 0 {
			lines = append(lines, "- Why chosen: "+cleanInline(strings.Join(lead.FitReasons, "; ")))
		}
		if lead.Draft != nil {
			if lead.Draft.Subject != "" {
				lines = append(lines, "- Subject: "+cleanInline(lead.Draft.Subject))
			}
			lines = append(lines, "- Draft angle: "+cleanInline(lead.Draft.Angle))
			if len(lead.Draft.Evidence) > 0 {
				lines = append(lines, "- Draft evidence:")
				for _, evidence := range lead.Draft.Evidence {
					lines = append(lines, "  - "+cleanInline(evidence))
				}
			}
			lines = append(lines, "", "Draft:", "")
			lines = append(lines, renderMarkdownQuote(lead.Draft.Body)...)
			lines = append(lines, "")
		}
		if len(lead.SendAttempts) > 0 {
			last := lead.SendAttempts[len(lead.SendAttempts)-1]
			lines = append(lines, fmt.Sprintf("- Last send check: `%s` dry_run=`%t` at `%s`", last.Status, last.DryRun, last.At.Format(time.RFC3339)))
		}
		lines = append(lines, "")
	}
	return lines
}

func titleLabel(value string) string {
	if value == "" {
		return value
	}
	return strings.ToUpper(value[:1]) + value[1:]
}

func bucketForLead(lead Lead) string {
	switch lead.LeadType {
	case LeadTypeContractRecruiter:
		return "recruiter"
	case LeadTypeAgencyResource, LeadTypeAgencyDelivery, LeadTypeAgencyFounder:
		return "agency"
	default:
		return ""
	}
}
