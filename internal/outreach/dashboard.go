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
	StatePath          string                `json:"state_path"`
	TargetAgencies     int                   `json:"target_agencies"`
	TargetRecruiters   int                   `json:"target_recruiters"`
	AllowSend          bool                  `json:"allow_send"`
	Actions            []DailyLeadAction     `json:"actions"`
	Counts             StatusCounts          `json:"counts"`
	RunCounts          DashboardRunCounts    `json:"run_counts"`
	BacklogCounts      DashboardBucketCounts `json:"backlog_counts"`
	ReadyCounts        DashboardBucketCounts `json:"ready_counts"`
	LifetimeCounts     DashboardBucketCounts `json:"lifetime_counts"`
	AgencyFunnelCounts AgencyAccountFunnel   `json:"agency_funnel_counts"`
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

type DailyLeadAction struct {
	At            time.Time     `json:"at"`
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

func BuildDashboardReport(state OutreachState, statePath string, targetAgencies int, targetRecruiters int, allowSend bool, actions []DailyLeadAction) DashboardReport {
	state.Normalize()
	return DashboardReport{
		GeneratedAt:      time.Now(),
		StatePath:        statePath,
		TargetAgencies:   targetAgencies,
		TargetRecruiters: targetRecruiters,
		AllowSend:        allowSend,
		Actions:          actions,
		Counts:           Counts(state),
		RunCounts:        dashboardRunCounts(actions),
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
		fmt.Sprintf("- State: `%s`", report.StatePath),
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
		"",
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
