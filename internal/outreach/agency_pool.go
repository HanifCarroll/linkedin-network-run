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
	GeneratedAt                   time.Time                            `json:"generated_at"`
	StatePath                     string                               `json:"state_path"`
	Counts                        StatusCounts                         `json:"counts"`
	Funnel                        AgencyAccountFunnel                  `json:"funnel"`
	Drilldown                     AgencyDrilldownCounts                `json:"drilldown"`
	ContactCandidateCounts        map[AgencyContactCandidateStatus]int `json:"contact_candidate_counts"`
	ContactCandidateReviewCounts  map[AgencyContactReviewStatus]int    `json:"contact_candidate_review_counts"`
	ContactCandidateSourceCounts  map[string]int                       `json:"contact_candidate_source_counts"`
	WebsiteCandidates             int                                  `json:"website_candidates"`
	QualifiedWebsiteCandidates    int                                  `json:"qualified_website_candidates"`
	ExhaustedWebsiteCandidates    int                                  `json:"exhausted_website_candidates"`
	RetryableBrowserErrorAccounts int                                  `json:"retryable_browser_error_accounts"`
	Accounts                      []AgencyPoolAccountDiagnosis         `json:"accounts"`
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
	cmd.AddCommand(agencyPoolImportSourceCommand(withStore))
	cmd.AddCommand(agencyPoolEnrichWebsitesCommand(withStore))
	cmd.AddCommand(agencyPoolContactsCommand(withStore))
	cmd.AddCommand(agencyPoolReviewContactCommand(withStore))
	cmd.AddCommand(agencyPoolPromoteContactCommand(withStore))
	cmd.AddCommand(agencyPoolPromoteContactsCommand(withStore))
	cmd.AddCommand(agencyPoolDiagnoseCommand(withStore))
	return cmd
}

func agencyPoolImportSourceCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "import-source <path>",
		Short: "Import structured agency accounts and review-only contact candidates",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error {
				capture, err := LoadAgencySourceCapture(args[0])
				if err != nil {
					return err
				}
				state, err := store.Load()
				if err != nil {
					return err
				}
				summary, err := ImportAgencySourceCapture(&state, capture)
				if err != nil {
					return err
				}
				if err := store.Save(state); err != nil {
					return err
				}
				if asJSON {
					raw, err := json.MarshalIndent(summary, "", "  ")
					if err != nil {
						return err
					}
					fmt.Println(string(raw))
					return nil
				}
				fmt.Printf("source=%s stored=%d updated=%d qualified=%d needs_review=%d rejected=%d contact_candidates_stored=%d contact_candidates_updated=%d total_accounts=%d\n",
					summary.Source,
					summary.Stored,
					summary.Updated,
					summary.Qualified,
					summary.NeedsReview,
					summary.Rejected,
					summary.ContactCandidatesStored,
					summary.ContactCandidatesUpdated,
					summary.TotalAccounts,
				)
				return nil
			})(cmd, args)
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolEnrichWebsitesCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var limit int
	var timeoutMS int
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "enrich-websites",
		Short: "Discover explicit review-only contacts from agency websites",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error {
				state, err := store.Load()
				if err != nil {
					return err
				}
				summary := EnrichAgencyWebsites(cmd.Context(), &state, AgencyWebsiteEnrichmentOptions{
					Limit:     limit,
					TimeoutMS: timeoutMS,
				})
				if err := store.Save(state); err != nil {
					return err
				}
				if asJSON {
					raw, err := json.MarshalIndent(summary, "", "  ")
					if err != nil {
						return err
					}
					fmt.Println(string(raw))
					return nil
				}
				fmt.Printf("checked=%d skipped=%d contact_candidates_stored=%d contact_candidates_updated=%d errors=%d\n",
					summary.Checked,
					summary.Skipped,
					summary.ContactCandidatesStored,
					summary.ContactCandidatesUpdated,
					summary.Errors,
				)
				return nil
			})(cmd, args)
		},
	}
	cmd.Flags().IntVar(&limit, "limit", 25, "max agency websites to check")
	cmd.Flags().IntVar(&timeoutMS, "timeout-ms", 10000, "HTTP timeout per request in milliseconds")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolContactsCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var limit int
	var status string
	var reviewStatus string
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "contacts",
		Short: "List review-only agency contact candidates",
		Args:  cobra.NoArgs,
		RunE: withStore(func(store *Store) error {
			state, err := store.Load()
			if err != nil {
				return err
			}
			candidates, err := agencyContactCandidatesForReview(state, status, reviewStatus, limit)
			if err != nil {
				return err
			}
			if asJSON {
				raw, err := json.MarshalIndent(candidates, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Println(RenderAgencyContactCandidatesText(candidates))
			return nil
		}),
	}
	cmd.Flags().IntVar(&limit, "limit", 20, "max contact candidate rows")
	cmd.Flags().StringVar(&status, "status", "", "candidate status filter")
	cmd.Flags().StringVar(&reviewStatus, "review-status", string(AgencyContactReviewStatusNeedsReview), "review status filter")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolReviewContactCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var candidateID string
	var reviewStatus string
	var name string
	var title string
	var note string
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "review-contact",
		Short: "Approve, reject, or annotate a review-only agency contact candidate",
		Args:  cobra.NoArgs,
		RunE: withStore(func(store *Store) error {
			parsedStatus, ok, err := parseAgencyContactReviewStatus(reviewStatus)
			if err != nil {
				return err
			}
			if !ok {
				return fmt.Errorf("--review-status is required")
			}
			state, err := store.Load()
			if err != nil {
				return err
			}
			candidate, err := ReviewAgencyContactCandidate(&state, AgencyContactReviewOptions{
				CandidateID:  candidateID,
				ReviewStatus: parsedStatus,
				Name:         name,
				Title:        title,
				Note:         note,
			})
			if err != nil {
				return err
			}
			if err := store.Save(state); err != nil {
				return err
			}
			if asJSON {
				raw, err := json.MarshalIndent(candidate, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Printf("candidate=%s review_status=%s status=%s name=%s title=%s\n",
				candidate.ID,
				candidate.ReviewStatus,
				candidate.Status,
				stringOrDash(candidate.Name),
				stringOrDash(candidate.Title),
			)
			return nil
		}),
	}
	cmd.Flags().StringVar(&candidateID, "candidate-id", "", "agency contact candidate id")
	cmd.Flags().StringVar(&reviewStatus, "review-status", string(AgencyContactReviewStatusApproved), "review status")
	cmd.Flags().StringVar(&name, "name", "", "reviewed person name")
	cmd.Flags().StringVar(&title, "title", "", "reviewed person title")
	cmd.Flags().StringVar(&note, "note", "", "review note")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolPromoteContactCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var candidateID string
	var draft bool
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "promote-contact",
		Short: "Promote one approved LinkedIn-profile candidate into a draftable agency lead",
		Args:  cobra.NoArgs,
		RunE: withStore(func(store *Store) error {
			if cleanText(candidateID) == "" {
				return fmt.Errorf("--candidate-id is required")
			}
			state, err := store.Load()
			if err != nil {
				return err
			}
			summary, err := PromoteAgencyContactCandidates(&state, AgencyContactPromotionOptions{
				CandidateIDs: []string{candidateID},
				Draft:        draft,
			})
			if err != nil {
				return err
			}
			if err := store.Save(state); err != nil {
				return err
			}
			if asJSON {
				raw, err := json.MarshalIndent(summary, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Println(RenderAgencyContactPromotionSummaryText(summary))
			return nil
		}),
	}
	cmd.Flags().StringVar(&candidateID, "candidate-id", "", "agency contact candidate id")
	cmd.Flags().BoolVar(&draft, "draft", false, "generate a draft for promoted leads")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolPromoteContactsCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var candidateIDs []string
	var limit int
	var draft bool
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "promote-contacts",
		Short: "Promote approved LinkedIn-profile candidates into draftable agency leads",
		Args:  cobra.NoArgs,
		RunE: withStore(func(store *Store) error {
			state, err := store.Load()
			if err != nil {
				return err
			}
			summary, err := PromoteAgencyContactCandidates(&state, AgencyContactPromotionOptions{
				CandidateIDs: candidateIDs,
				Limit:        limit,
				Draft:        draft,
			})
			if err != nil {
				return err
			}
			if err := store.Save(state); err != nil {
				return err
			}
			if asJSON {
				raw, err := json.MarshalIndent(summary, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Println(RenderAgencyContactPromotionSummaryText(summary))
			return nil
		}),
	}
	cmd.Flags().StringSliceVar(&candidateIDs, "candidate-id", []string{}, "candidate id to promote; repeat or comma-separate")
	cmd.Flags().IntVar(&limit, "limit", 20, "max approved candidates to promote when candidate ids are omitted")
	cmd.Flags().BoolVar(&draft, "draft", false, "generate drafts for promoted leads")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolDiagnoseCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var limit int
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "diagnose",
		Short: "Show agency account pool health and next actions",
		Args:  cobra.NoArgs,
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
	counts := Counts(state)
	leadCounts := agencyPoolLeadCountsByAccount(state)
	diagnosis := AgencyPoolDiagnosis{
		GeneratedAt:                  time.Now(),
		StatePath:                    statePath,
		Counts:                       counts,
		Funnel:                       agencyAccountFunnelCounts(state),
		Drilldown:                    agencyDrilldownCounts(state),
		ContactCandidateCounts:       counts.ByAgencyContactCandidateStatus,
		ContactCandidateReviewCounts: counts.ByAgencyContactCandidateReviewStatus,
		ContactCandidateSourceCounts: counts.ByAgencyContactCandidateSource,
		Accounts:                     []AgencyPoolAccountDiagnosis{},
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
		"review_only_contacts=" + renderAgencyContactCandidateStatusCounts(diagnosis.ContactCandidateCounts),
		"contact_review=" + renderAgencyContactReviewStatusCounts(diagnosis.ContactCandidateReviewCounts),
		"contact_sources=" + renderStringCounts(diagnosis.ContactCandidateSourceCounts),
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

func agencyContactCandidatesForReview(state OutreachState, status string, reviewStatus string, limit int) ([]AgencyContactCandidate, error) {
	state.Normalize()
	candidateStatus, filterByStatus, err := parseAgencyContactCandidateStatus(status)
	if err != nil {
		return nil, err
	}
	candidateReviewStatus, filterByReviewStatus, err := parseAgencyContactReviewStatus(reviewStatus)
	if err != nil {
		return nil, err
	}
	items := []AgencyContactCandidate{}
	for _, candidate := range state.AgencyContactCandidates {
		if filterByStatus && candidate.Status != candidateStatus {
			continue
		}
		if filterByReviewStatus && candidate.ReviewStatus != candidateReviewStatus {
			continue
		}
		items = append(items, candidate)
	}
	sortAgencyContactCandidates(items)
	if limit > 0 && len(items) > limit {
		items = items[:limit]
	}
	return items, nil
}

func parseAgencyContactCandidateStatus(value string) (AgencyContactCandidateStatus, bool, error) {
	cleaned := cleanText(value)
	if cleaned == "" {
		return "", false, nil
	}
	status := AgencyContactCandidateStatus(cleaned)
	if !validAgencyContactCandidateStatus(status) {
		return "", false, fmt.Errorf("invalid agency contact candidate status %q", cleaned)
	}
	return status, true, nil
}

func parseAgencyContactReviewStatus(value string) (AgencyContactReviewStatus, bool, error) {
	cleaned := cleanText(value)
	if cleaned == "" {
		return "", false, nil
	}
	status := AgencyContactReviewStatus(cleaned)
	switch status {
	case AgencyContactReviewStatusNeedsReview,
		AgencyContactReviewStatusApproved,
		AgencyContactReviewStatusRejected,
		AgencyContactReviewStatusConverted:
		return status, true, nil
	default:
		return "", false, fmt.Errorf("invalid agency contact review status %q", cleaned)
	}
}

func RenderAgencyContactCandidatesText(candidates []AgencyContactCandidate) string {
	lines := []string{
		fmt.Sprintf("agency_contact_candidates=%d", len(candidates)),
		"id\treview_status\tstatus\tsource\tagency\temail\tprofile_url\tcontact_url\tform_action\tpromoted_lead\tname",
	}
	for _, candidate := range candidates {
		lines = append(lines, fmt.Sprintf("%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s",
			candidate.ID,
			candidate.ReviewStatus,
			candidate.Status,
			cleanText(candidate.Source),
			cleanText(candidate.AgencyAccountName),
			stringOrDash(candidate.Email),
			stringOrDash(candidate.ProfileURL),
			stringOrDash(candidate.ContactURL),
			stringOrDash(candidate.FormAction),
			stringOrDash(candidate.PromotedLeadID),
			stringOrDash(candidate.Name),
		))
	}
	return strings.Join(lines, "\n")
}

func RenderAgencyContactPromotionSummaryText(summary AgencyContactPromotionSummary) string {
	lines := []string{
		fmt.Sprintf("stored=%d updated=%d drafted=%d skipped=%d", summary.Stored, summary.Updated, summary.Drafted, len(summary.Skipped)),
	}
	if len(summary.Leads) > 0 {
		lines = append(lines, "leads:")
		for _, lead := range summary.Leads {
			lines = append(lines, fmt.Sprintf("%s\t%s\t%s\t%s\t%s",
				lead.ID,
				lead.Name,
				lead.LeadType,
				stringOrDash(lead.Title),
				stringOrDash(lead.ProfileURL),
			))
		}
	}
	if len(summary.Skipped) > 0 {
		lines = append(lines, "skipped:")
		for _, skipped := range summary.Skipped {
			lines = append(lines, fmt.Sprintf("%s\t%s", skipped.CandidateID, skipped.Reason))
		}
	}
	return strings.Join(lines, "\n")
}

func renderAgencyContactCandidateStatusCounts(counts map[AgencyContactCandidateStatus]int) string {
	parts := []string{}
	for _, status := range []AgencyContactCandidateStatus{
		AgencyContactCandidateStatusWebsiteContactCandidate,
		AgencyContactCandidateStatusGenericInbox,
		AgencyContactCandidateStatusContactForm,
		AgencyContactCandidateStatusRejected,
		AgencyContactCandidateStatusConverted,
	} {
		parts = append(parts, fmt.Sprintf("%s %d", status, counts[status]))
	}
	return strings.Join(parts, "; ")
}

func renderAgencyContactReviewStatusCounts(counts map[AgencyContactReviewStatus]int) string {
	parts := []string{}
	for _, status := range []AgencyContactReviewStatus{
		AgencyContactReviewStatusNeedsReview,
		AgencyContactReviewStatusApproved,
		AgencyContactReviewStatusRejected,
		AgencyContactReviewStatusConverted,
	} {
		parts = append(parts, fmt.Sprintf("%s %d", status, counts[status]))
	}
	return strings.Join(parts, "; ")
}

func renderStringCounts(counts map[string]int) string {
	if len(counts) == 0 {
		return "-"
	}
	keys := []string{}
	for key := range counts {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	parts := []string{}
	for _, key := range keys {
		parts = append(parts, fmt.Sprintf("%s %d", key, counts[key]))
	}
	return strings.Join(parts, "; ")
}

func stringOrDash(value *string) string {
	if value == nil || cleanText(*value) == "" {
		return "-"
	}
	return cleanText(*value)
}
