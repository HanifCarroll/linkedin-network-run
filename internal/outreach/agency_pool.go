package outreach

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

type AgencyPoolDiagnosis struct {
	GeneratedAt                   time.Time                    `json:"generated_at"`
	StatePath                     string                       `json:"state_path"`
	Counts                        StatusCounts                 `json:"counts"`
	Funnel                        AgencyAccountFunnel          `json:"funnel"`
	Drilldown                     AgencyDrilldownCounts        `json:"drilldown"`
	WebsiteCandidates             int                          `json:"website_candidates"`
	QualifiedWebsiteCandidates    int                          `json:"qualified_website_candidates"`
	ExhaustedWebsiteCandidates    int                          `json:"exhausted_website_candidates"`
	RetryableBrowserErrorAccounts int                          `json:"retryable_browser_error_accounts"`
	Accounts                      []AgencyPoolAccountDiagnosis `json:"accounts"`
}

type AgencyPoolAccountDiagnosis struct {
	ID                   string              `json:"id"`
	Name                 string              `json:"name"`
	Status               AgencyAccountStatus `json:"status"`
	FitScore             int                 `json:"fit_score"`
	Website              *string             `json:"website,omitempty"`
	Domain               *string             `json:"domain,omitempty"`
	ContactCaptureCount  int                 `json:"contact_capture_count"`
	LastContactStrategy  *string             `json:"last_contact_strategy,omitempty"`
	LastContactError     *string             `json:"last_contact_error,omitempty"`
	Contacts             int                 `json:"contacts"`
	OpenLeads            int                 `json:"open_leads"`
	MessageableOrSent    int                 `json:"messageable_or_sent"`
	NextLinkedInStrategy *string             `json:"next_linkedin_strategy,omitempty"`
	NextStep             string              `json:"next_step"`
}

type agencyPoolLeadCounts struct {
	Contacts          int
	OpenLeads         int
	MessageableOrSent int
}

func agencyPoolCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "agency-pool",
		Short: "Inspect agency account sourcing and contactability",
	}
	cmd.AddCommand(agencyPoolDiagnoseCommand(withStore))
	return cmd
}

func agencyPoolDiagnoseCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var limit int
	var asJSON bool
	cmd := &cobra.Command{
		Use:  "diagnose",
		Args: cobra.NoArgs,
		RunE: withStore(func(store *Store) error {
			state, err := store.Load()
			if err != nil {
				return err
			}
			diagnosis := BuildAgencyPoolDiagnosis(state, store.StatePath(), limit)
			if asJSON {
				raw, err := json.MarshalIndent(diagnosis, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Println(RenderAgencyPoolDiagnosisText(diagnosis))
			return nil
		}),
	}
	cmd.Flags().IntVar(&limit, "limit", 20, "max account rows")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func BuildAgencyPoolDiagnosis(state OutreachState, statePath string, limit int) AgencyPoolDiagnosis {
	state.Normalize()
	leadCounts := agencyPoolLeadCountsByAccount(state)
	diagnosis := AgencyPoolDiagnosis{
		GeneratedAt: time.Now(),
		StatePath:   statePath,
		Counts:      Counts(state),
		Funnel:      agencyAccountFunnelCounts(state),
		Drilldown:   agencyDrilldownCounts(state),
		Accounts:    []AgencyPoolAccountDiagnosis{},
	}
	for _, account := range state.AgencyAccounts {
		counts := leadCounts[account.ID]
		item := buildAgencyPoolAccountDiagnosis(account, counts)
		if item.NextStep == "website_enrichment" {
			diagnosis.WebsiteCandidates++
			switch account.Status {
			case AgencyAccountStatusQualified:
				diagnosis.QualifiedWebsiteCandidates++
			case AgencyAccountStatusExhausted:
				diagnosis.ExhaustedWebsiteCandidates++
			}
		}
		if account.LastContactError != nil && cleanText(*account.LastContactError) != "" {
			diagnosis.RetryableBrowserErrorAccounts++
		}
		if item.NextStep == "no_action" {
			continue
		}
		diagnosis.Accounts = append(diagnosis.Accounts, item)
	}
	sort.SliceStable(diagnosis.Accounts, func(i, j int) bool {
		left := agencyPoolNextStepRank(diagnosis.Accounts[i].NextStep)
		right := agencyPoolNextStepRank(diagnosis.Accounts[j].NextStep)
		if left != right {
			return left < right
		}
		if diagnosis.Accounts[i].FitScore != diagnosis.Accounts[j].FitScore {
			return diagnosis.Accounts[i].FitScore > diagnosis.Accounts[j].FitScore
		}
		return diagnosis.Accounts[i].Name < diagnosis.Accounts[j].Name
	})
	if limit > 0 && len(diagnosis.Accounts) > limit {
		diagnosis.Accounts = diagnosis.Accounts[:limit]
	}
	return diagnosis
}

func buildAgencyPoolAccountDiagnosis(account AgencyAccount, counts agencyPoolLeadCounts) AgencyPoolAccountDiagnosis {
	item := AgencyPoolAccountDiagnosis{
		ID:                  account.ID,
		Name:                account.Name,
		Status:              account.Status,
		FitScore:            account.FitScore,
		Website:             account.Website,
		Domain:              account.Domain,
		ContactCaptureCount: account.ContactCaptureCount,
		LastContactStrategy: account.LastContactStrategy,
		LastContactError:    account.LastContactError,
		Contacts:            counts.Contacts,
		OpenLeads:           counts.OpenLeads,
		MessageableOrSent:   counts.MessageableOrSent,
		NextStep:            "no_action",
	}
	if strategy, ok := nextAgencyContactSearchStrategy(account); ok {
		item.NextLinkedInStrategy = &strategy.Name
	}
	switch {
	case account.Status == AgencyAccountStatusQualified && counts.OpenLeads > 0:
		item.NextStep = "validate_or_send_open_lead"
	case account.Status == AgencyAccountStatusQualified && account.LastContactError != nil && cleanText(*account.LastContactError) != "":
		item.NextStep = "retry_linkedin_contact_search"
	case account.Status == AgencyAccountStatusQualified && item.NextLinkedInStrategy != nil:
		item.NextStep = "continue_linkedin_contact_search:" + *item.NextLinkedInStrategy
	case accountHasWebsite(account) && counts.Contacts == 0 && agencyAccountWebsiteEnrichmentEligible(account):
		item.NextStep = "website_enrichment"
	case account.Status == AgencyAccountStatusNeedsReview:
		item.NextStep = "review_account_fit"
	}
	return item
}

func agencyPoolLeadCountsByAccount(state OutreachState) map[string]agencyPoolLeadCounts {
	state.Normalize()
	byAccount := map[string]agencyPoolLeadCounts{}
	for _, lead := range state.Leads {
		if lead.AgencyAccountID == nil || cleanText(*lead.AgencyAccountID) == "" || bucketForLead(lead) != "agency" || lead.Status != LeadStatusEligible {
			continue
		}
		accountID := cleanText(*lead.AgencyAccountID)
		counts := byAccount[accountID]
		counts.Contacts++
		if !isTerminalMessageStatus(lead.MessageStatus) || lead.MessageStatus == MessageStatusDryRunReady {
			counts.OpenLeads++
		}
		switch lead.MessageStatus {
		case MessageStatusDryRunReady, MessageStatusSent, MessageStatusManuallySent:
			counts.MessageableOrSent++
		}
		byAccount[accountID] = counts
	}
	return byAccount
}

func agencyAccountWebsiteEnrichmentEligible(account AgencyAccount) bool {
	return account.Status == AgencyAccountStatusQualified || account.Status == AgencyAccountStatusExhausted
}

func accountHasWebsite(account AgencyAccount) bool {
	return account.Website != nil && cleanText(*account.Website) != ""
}

func agencyPoolNextStepRank(step string) int {
	switch {
	case step == "validate_or_send_open_lead":
		return 0
	case step == "retry_linkedin_contact_search":
		return 1
	case strings.HasPrefix(step, "continue_linkedin_contact_search:"):
		return 2
	case step == "website_enrichment":
		return 3
	case step == "review_account_fit":
		return 4
	default:
		return 9
	}
}

func RenderAgencyPoolDiagnosisText(diagnosis AgencyPoolDiagnosis) string {
	lines := []string{
		fmt.Sprintf("state=%s", diagnosis.StatePath),
		fmt.Sprintf("agency_accounts=qualified %d; needs_review %d; rejected %d; exhausted %d",
			diagnosis.Counts.ByAgencyAccountStatus[AgencyAccountStatusQualified],
			diagnosis.Counts.ByAgencyAccountStatus[AgencyAccountStatusNeedsReview],
			diagnosis.Counts.ByAgencyAccountStatus[AgencyAccountStatusRejected],
			diagnosis.Counts.ByAgencyAccountStatus[AgencyAccountStatusExhausted],
		),
		fmt.Sprintf("contactability=qualified %d; with_contacts %d; with_messageable_or_sent %d; exhausted_without_contacts %d; exhausted_after_contact_attempts %d",
			diagnosis.Funnel.Qualified,
			diagnosis.Funnel.WithContacts,
			diagnosis.Funnel.WithMessageableOrSentContacts,
			diagnosis.Funnel.ExhaustedWithoutContacts,
			diagnosis.Funnel.ExhaustedAfterContactAttempts,
		),
		fmt.Sprintf("drilldown=not_searched %d; founder_recent %d; executive_broad %d; resource_broad %d; contacts_found %d; no_contacts_found %d; browser_error_retryable %d",
			diagnosis.Drilldown.NotSearchedYet,
			diagnosis.Drilldown.SearchedFounderRecent,
			diagnosis.Drilldown.SearchedExecutiveBroad,
			diagnosis.Drilldown.SearchedResourceBroad,
			diagnosis.Drilldown.ContactsFound,
			diagnosis.Drilldown.NoContactsFound,
			diagnosis.Drilldown.BrowserErrorRetryable,
		),
		fmt.Sprintf("website_candidates=all %d; qualified %d; exhausted %d",
			diagnosis.WebsiteCandidates,
			diagnosis.QualifiedWebsiteCandidates,
			diagnosis.ExhaustedWebsiteCandidates,
		),
		fmt.Sprintf("retryable_browser_error_accounts=%d", diagnosis.RetryableBrowserErrorAccounts),
		"next_accounts:",
		"id\tscore\tstatus\tcaptures\tcontacts\topen_leads\tlast_strategy\twebsite\tnext_step\tname",
	}
	for _, account := range diagnosis.Accounts {
		lines = append(lines, fmt.Sprintf("%s\t%d\t%s\t%d\t%d\t%d\t%s\t%s\t%s\t%s",
			account.ID,
			account.FitScore,
			account.Status,
			account.ContactCaptureCount,
			account.Contacts,
			account.OpenLeads,
			stringOrDash(account.LastContactStrategy),
			stringOrDash(account.Website),
			cleanText(account.NextStep),
			cleanText(account.Name),
		))
	}
	return strings.Join(lines, "\n")
}

func stringOrDash(value *string) string {
	if value == nil || cleanText(*value) == "" {
		return "-"
	}
	return cleanText(*value)
}
