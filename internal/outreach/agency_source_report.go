package outreach

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type AgencySourceReport struct {
	GeneratedAt time.Time               `json:"generated_at"`
	StatePath   string                  `json:"state_path"`
	ReportPath  string                  `json:"report_path,omitempty"`
	Sources     []AgencySourceReportRow `json:"sources"`
	Totals      AgencySourceReportRow   `json:"totals"`
}

type AgencySourceReportRow struct {
	Source                   string `json:"source"`
	Accounts                 int    `json:"accounts"`
	QualifiedAccounts        int    `json:"qualified_accounts"`
	NeedsReviewAccounts      int    `json:"needs_review_accounts"`
	RejectedAccounts         int    `json:"rejected_accounts"`
	ExhaustedAccounts        int    `json:"exhausted_accounts"`
	DeadEndAccounts          int    `json:"dead_end_accounts"`
	ContactCandidates        int    `json:"contact_candidates"`
	WebsiteContactCandidates int    `json:"website_contact_candidates"`
	GenericInboxes           int    `json:"generic_inboxes"`
	ContactForms             int    `json:"contact_forms"`
	NeedsReviewContacts      int    `json:"needs_review_contacts"`
	ApprovedContacts         int    `json:"approved_contacts"`
	ConvertedContacts        int    `json:"converted_contacts"`
	PromotedLeads            int    `json:"promoted_leads"`
	DraftedLeads             int    `json:"drafted_leads"`
	ReadyLeads               int    `json:"ready_leads"`
	SentLeads                int    `json:"sent_leads"`
	ConversationExists       int    `json:"conversation_exists"`
	NotMessageable           int    `json:"not_messageable"`
	Blocked                  int    `json:"blocked"`
	SendFailed               int    `json:"send_failed"`
}

func BuildAgencySourceReport(state OutreachState, statePath string, reportPath string) AgencySourceReport {
	state.Normalize()
	accountsByID := map[string]AgencyAccount{}
	candidatesByAccount := map[string]int{}
	leadsByAccount := map[string]int{}
	bySource := map[string]*AgencySourceReportRow{}
	get := func(source string) *AgencySourceReportRow {
		cleaned := cleanText(source)
		if cleaned == "" {
			cleaned = "unknown"
		}
		item := bySource[cleaned]
		if item == nil {
			item = &AgencySourceReportRow{Source: cleaned}
			bySource[cleaned] = item
		}
		return item
	}
	for _, account := range state.AgencyAccounts {
		accountsByID[account.ID] = account
		item := get(account.Source)
		item.Accounts++
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
		source := candidate.Source
		if account, ok := accountsByID[candidate.AgencyAccountID]; ok {
			source = account.Source
		}
		item := get(source)
		candidatesByAccount[candidate.AgencyAccountID]++
		item.ContactCandidates++
		switch candidate.Status {
		case AgencyContactCandidateStatusWebsiteContactCandidate:
			item.WebsiteContactCandidates++
		case AgencyContactCandidateStatusGenericInbox:
			item.GenericInboxes++
		case AgencyContactCandidateStatusContactForm:
			item.ContactForms++
		}
		switch candidate.ReviewStatus {
		case AgencyContactReviewStatusNeedsReview:
			item.NeedsReviewContacts++
		case AgencyContactReviewStatusApproved:
			item.ApprovedContacts++
		case AgencyContactReviewStatusConverted:
			item.ConvertedContacts++
		}
		if candidate.PromotedLeadID != nil && cleanText(*candidate.PromotedLeadID) != "" {
			item.PromotedLeads++
		}
	}
	for _, lead := range state.Leads {
		if bucketForLead(lead) != "agency" || lead.Status != LeadStatusEligible {
			continue
		}
		source := lead.Source
		if lead.AgencyAccountID != nil {
			accountID := cleanText(*lead.AgencyAccountID)
			if account, ok := accountsByID[accountID]; ok {
				source = account.Source
			}
			leadsByAccount[accountID]++
		}
		item := get(source)
		if lead.Draft != nil {
			item.DraftedLeads++
		}
		switch lead.MessageStatus {
		case MessageStatusDryRunReady:
			item.ReadyLeads++
		case MessageStatusSent, MessageStatusManuallySent:
			item.SentLeads++
		case MessageStatusConversationExists:
			item.ConversationExists++
		case MessageStatusNotMessageable:
			item.NotMessageable++
		case MessageStatusBlocked:
			item.Blocked++
		case MessageStatusSendFailed:
			item.SendFailed++
		}
	}
	for _, account := range state.AgencyAccounts {
		if account.Status == AgencyAccountStatusRejected {
			get(account.Source).DeadEndAccounts++
			continue
		}
		if account.Status == AgencyAccountStatusExhausted && candidatesByAccount[account.ID] == 0 && leadsByAccount[account.ID] == 0 {
			get(account.Source).DeadEndAccounts++
		}
	}
	report := AgencySourceReport{
		GeneratedAt: time.Now(),
		StatePath:   statePath,
		ReportPath:  reportPath,
		Sources:     []AgencySourceReportRow{},
		Totals:      AgencySourceReportRow{Source: "total"},
	}
	for _, item := range bySource {
		report.Sources = append(report.Sources, *item)
		addAgencySourceReportRow(&report.Totals, *item)
	}
	sort.SliceStable(report.Sources, func(i, j int) bool {
		left := report.Sources[i]
		right := report.Sources[j]
		if left.QualifiedAccounts != right.QualifiedAccounts {
			return left.QualifiedAccounts > right.QualifiedAccounts
		}
		if left.ContactCandidates != right.ContactCandidates {
			return left.ContactCandidates > right.ContactCandidates
		}
		return left.Source < right.Source
	})
	return report
}

func WriteAgencySourceReport(path string, report AgencySourceReport) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", filepath.Dir(path), err)
	}
	report.ReportPath = path
	raw, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		return fmt.Errorf("serializing agency source report: %w", err)
	}
	raw = append(raw, '\n')
	if err := os.WriteFile(path, raw, 0o644); err != nil {
		return fmt.Errorf("writing agency source report %s: %w", path, err)
	}
	return nil
}

func RenderAgencySourceReportText(report AgencySourceReport) string {
	lines := []string{
		"state=" + cleanText(report.StatePath),
		"report=" + cleanText(report.ReportPath),
		fmt.Sprintf("totals accounts=%d qualified=%d contact_candidates=%d promoted=%d drafted=%d ready=%d sent=%d conversation_exists=%d not_messageable=%d blocked=%d send_failed=%d dead_end_accounts=%d",
			report.Totals.Accounts,
			report.Totals.QualifiedAccounts,
			report.Totals.ContactCandidates,
			report.Totals.PromotedLeads,
			report.Totals.DraftedLeads,
			report.Totals.ReadyLeads,
			report.Totals.SentLeads,
			report.Totals.ConversationExists,
			report.Totals.NotMessageable,
			report.Totals.Blocked,
			report.Totals.SendFailed,
			report.Totals.DeadEndAccounts,
		),
		"source\taccounts\tqualified\tneeds_review\trejected\texhausted\tcontacts\tprofile_candidates\tgeneric_inbox\tcontact_form\tapproved\tconverted\tpromoted\tdrafted\tready\tsent\tconversation_exists\tnot_messageable\tblocked\tsend_failed\tdead_end_accounts",
	}
	for _, row := range report.Sources {
		lines = append(lines, fmt.Sprintf("%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d",
			cleanText(row.Source),
			row.Accounts,
			row.QualifiedAccounts,
			row.NeedsReviewAccounts,
			row.RejectedAccounts,
			row.ExhaustedAccounts,
			row.ContactCandidates,
			row.WebsiteContactCandidates,
			row.GenericInboxes,
			row.ContactForms,
			row.ApprovedContacts,
			row.ConvertedContacts,
			row.PromotedLeads,
			row.DraftedLeads,
			row.ReadyLeads,
			row.SentLeads,
			row.ConversationExists,
			row.NotMessageable,
			row.Blocked,
			row.SendFailed,
			row.DeadEndAccounts,
		))
	}
	return strings.Join(lines, "\n")
}

func addAgencySourceReportRow(total *AgencySourceReportRow, row AgencySourceReportRow) {
	total.Accounts += row.Accounts
	total.QualifiedAccounts += row.QualifiedAccounts
	total.NeedsReviewAccounts += row.NeedsReviewAccounts
	total.RejectedAccounts += row.RejectedAccounts
	total.ExhaustedAccounts += row.ExhaustedAccounts
	total.DeadEndAccounts += row.DeadEndAccounts
	total.ContactCandidates += row.ContactCandidates
	total.WebsiteContactCandidates += row.WebsiteContactCandidates
	total.GenericInboxes += row.GenericInboxes
	total.ContactForms += row.ContactForms
	total.NeedsReviewContacts += row.NeedsReviewContacts
	total.ApprovedContacts += row.ApprovedContacts
	total.ConvertedContacts += row.ConvertedContacts
	total.PromotedLeads += row.PromotedLeads
	total.DraftedLeads += row.DraftedLeads
	total.ReadyLeads += row.ReadyLeads
	total.SentLeads += row.SentLeads
	total.ConversationExists += row.ConversationExists
	total.NotMessageable += row.NotMessageable
	total.Blocked += row.Blocked
	total.SendFailed += row.SendFailed
}
