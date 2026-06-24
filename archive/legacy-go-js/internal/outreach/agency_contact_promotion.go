package outreach

import (
	"fmt"
	"strings"
	"time"
)

type AgencyContactReviewOptions struct {
	CandidateID  string
	ReviewStatus AgencyContactReviewStatus
	Name         string
	Title        string
	Note         string
	Now          time.Time
}

type AgencyContactPromotionOptions struct {
	CandidateIDs           []string
	Limit                  int
	Draft                  bool
	MaxPerAgency           int
	AllowMultiplePerAgency bool
	Now                    time.Time
}

type AgencyContactPromotionSummary struct {
	Stored  int                          `json:"stored"`
	Updated int                          `json:"updated"`
	Drafted int                          `json:"drafted"`
	Skipped []AgencyContactPromotionSkip `json:"skipped"`
	Leads   []Lead                       `json:"leads"`
}

type AgencyContactPromotionSkip struct {
	CandidateID string `json:"candidate_id"`
	Reason      string `json:"reason"`
}

func ReviewAgencyContactCandidate(state *OutreachState, options AgencyContactReviewOptions) (AgencyContactCandidate, error) {
	state.Normalize()
	id := cleanText(options.CandidateID)
	if id == "" {
		return AgencyContactCandidate{}, fmt.Errorf("candidate id is required")
	}
	index := findAgencyContactCandidateByID(state.AgencyContactCandidates, id)
	if index < 0 {
		return AgencyContactCandidate{}, fmt.Errorf("agency contact candidate %s not found", id)
	}
	if options.ReviewStatus == "" {
		return AgencyContactCandidate{}, fmt.Errorf("review status is required")
	}
	switch options.ReviewStatus {
	case AgencyContactReviewStatusNeedsReview, AgencyContactReviewStatusApproved, AgencyContactReviewStatusRejected, AgencyContactReviewStatusConverted:
	default:
		return AgencyContactCandidate{}, fmt.Errorf("invalid agency contact review status %q", options.ReviewStatus)
	}
	now := options.Now
	if now.IsZero() {
		now = time.Now()
	}
	candidate := state.AgencyContactCandidates[index]
	candidate.ReviewStatus = options.ReviewStatus
	switch options.ReviewStatus {
	case AgencyContactReviewStatusRejected:
		candidate.Status = AgencyContactCandidateStatusRejected
	case AgencyContactReviewStatusConverted:
		candidate.Status = AgencyContactCandidateStatusConverted
	}
	if cleaned := cleanText(options.Name); cleaned != "" {
		candidate.Name = &cleaned
	}
	if cleaned := cleanText(options.Title); cleaned != "" {
		candidate.Title = &cleaned
	}
	if cleaned := cleanText(options.Note); cleaned != "" {
		candidate.Notes = append(candidate.Notes, cleaned)
	}
	candidate.UpdatedAt = now
	candidate.Normalize()
	state.AgencyContactCandidates[index] = candidate
	sortAgencyContactCandidates(state.AgencyContactCandidates)
	return candidate, nil
}

func PromoteAgencyContactCandidates(state *OutreachState, options AgencyContactPromotionOptions) (AgencyContactPromotionSummary, error) {
	state.Normalize()
	now := options.Now
	if now.IsZero() {
		now = time.Now()
	}
	selected, err := selectedAgencyContactCandidateIndexes(state.AgencyContactCandidates, options.CandidateIDs, options.Limit)
	if err != nil {
		return AgencyContactPromotionSummary{}, err
	}
	summary := AgencyContactPromotionSummary{Skipped: []AgencyContactPromotionSkip{}, Leads: []Lead{}}
	maxPerAgency := promotionMaxPerAgency(options)
	activeByAgency := activeAgencyLeadsByAccount(*state)
	for _, candidateIndex := range selected {
		candidate := state.AgencyContactCandidates[candidateIndex]
		lead, ok, reason := leadFromAgencyContactCandidate(*state, candidate, now)
		if !ok {
			summary.Skipped = append(summary.Skipped, AgencyContactPromotionSkip{CandidateID: candidate.ID, Reason: reason})
			continue
		}
		accountID := ""
		if lead.AgencyAccountID != nil {
			accountID = cleanText(*lead.AgencyAccountID)
		}
		leadIndex := findLeadIndex(state.Leads, lead)
		existingLeadWasActive := leadIndex >= 0 && activeAgencyPromotionLead(state.Leads[leadIndex])
		activeLeads := activeByAgency[accountID]
		if maxPerAgency > 0 && accountID != "" && len(activeLeads) >= maxPerAgency && !existingLeadWasActive {
			summary.Skipped = append(summary.Skipped, AgencyContactPromotionSkip{
				CandidateID: candidate.ID,
				Reason:      fmt.Sprintf("agency already has %d active outreach lead(s); max per agency is %d; active lead(s): %s", len(activeLeads), maxPerAgency, renderActiveAgencyLeadRefs(activeLeads)),
			})
			continue
		}
		if leadIndex >= 0 {
			preservePromotedLeadRuntimeFields(&lead, state.Leads[leadIndex])
			state.Leads[leadIndex] = lead
			summary.Updated++
		} else {
			state.Leads = append(state.Leads, lead)
			leadIndex = len(state.Leads) - 1
			summary.Stored++
		}
		if options.Draft && !isTerminalMessageStatus(state.Leads[leadIndex].MessageStatus) {
			draft := BuildMessageDraftRecord(state.Leads[leadIndex], now)
			state.Leads[leadIndex].Draft = &draft
			state.Leads[leadIndex].MessageStatus = MessageStatusDrafted
			state.Leads[leadIndex].UpdatedAt = now
			summary.Drafted++
		}
		promotedLeadID := state.Leads[leadIndex].ID
		candidate.PromotedLeadID = &promotedLeadID
		candidate.ReviewStatus = AgencyContactReviewStatusConverted
		candidate.Status = AgencyContactCandidateStatusConverted
		candidate.UpdatedAt = now
		state.AgencyContactCandidates[candidateIndex] = candidate
		summary.Leads = append(summary.Leads, state.Leads[leadIndex])
		if !existingLeadWasActive && activeAgencyPromotionLead(state.Leads[leadIndex]) && accountID != "" {
			activeByAgency[accountID] = append(activeByAgency[accountID], state.Leads[leadIndex])
		}
	}
	sortLeads(state.Leads)
	sortAgencyContactCandidates(state.AgencyContactCandidates)
	return summary, nil
}

func promotionMaxPerAgency(options AgencyContactPromotionOptions) int {
	if options.AllowMultiplePerAgency {
		return 0
	}
	if options.MaxPerAgency <= 0 {
		return 1
	}
	return options.MaxPerAgency
}

func activeAgencyLeadsByAccount(state OutreachState) map[string][]Lead {
	state.Normalize()
	byAccount := map[string][]Lead{}
	for _, lead := range state.Leads {
		if !activeAgencyPromotionLead(lead) || lead.AgencyAccountID == nil {
			continue
		}
		accountID := cleanText(*lead.AgencyAccountID)
		if accountID == "" {
			continue
		}
		byAccount[accountID] = append(byAccount[accountID], lead)
	}
	for accountID := range byAccount {
		sortLeads(byAccount[accountID])
	}
	return byAccount
}

func activeAgencyPromotionLead(lead Lead) bool {
	if lead.Status != LeadStatusEligible || bucketForLead(lead) != "agency" {
		return false
	}
	switch lead.MessageStatus {
	case MessageStatusNotMessageable, MessageStatusBlocked, MessageStatusRepliedNotFit:
		return false
	default:
		return true
	}
}

func renderActiveAgencyLeadRefs(leads []Lead) string {
	parts := []string{}
	for _, lead := range leads {
		parts = append(parts, fmt.Sprintf("%s (%s, %s)", cleanText(lead.Name), lead.ID, lead.MessageStatus))
	}
	if len(parts) == 0 {
		return "none"
	}
	return strings.Join(parts, "; ")
}

func selectedAgencyContactCandidateIndexes(candidates []AgencyContactCandidate, ids []string, limit int) ([]int, error) {
	cleanIDs := []string{}
	seen := map[string]bool{}
	for _, id := range ids {
		cleaned := cleanText(id)
		if cleaned == "" || seen[cleaned] {
			continue
		}
		seen[cleaned] = true
		cleanIDs = append(cleanIDs, cleaned)
	}
	if len(cleanIDs) > 0 {
		indexes := []int{}
		for _, id := range cleanIDs {
			index := findAgencyContactCandidateByID(candidates, id)
			if index < 0 {
				return nil, fmt.Errorf("agency contact candidate %s not found", id)
			}
			indexes = append(indexes, index)
		}
		return indexes, nil
	}
	indexes := []int{}
	for index, candidate := range candidates {
		if candidate.ReviewStatus != AgencyContactReviewStatusApproved {
			continue
		}
		indexes = append(indexes, index)
		if limit > 0 && len(indexes) >= limit {
			break
		}
	}
	return indexes, nil
}

func leadFromAgencyContactCandidate(state OutreachState, candidate AgencyContactCandidate, importedAt time.Time) (Lead, bool, string) {
	if candidate.ReviewStatus != AgencyContactReviewStatusApproved {
		return Lead{}, false, "candidate is not approved"
	}
	if candidate.Status != AgencyContactCandidateStatusWebsiteContactCandidate {
		return Lead{}, false, "only personal LinkedIn profile candidates can be promoted"
	}
	if candidate.ProfileURL == nil || cleanText(*candidate.ProfileURL) == "" {
		return Lead{}, false, "candidate has no LinkedIn profile URL"
	}
	name := cleanText(pointerValue(candidate.Name))
	if !usableReviewedContactName(name) {
		return Lead{}, false, "candidate needs a reviewed person name"
	}
	title := cleanText(pointerValue(candidate.Title))
	if title == "" {
		return Lead{}, false, "candidate needs a reviewed title"
	}
	accountIndex := findAgencyAccountByID(state.AgencyAccounts, candidate.AgencyAccountID)
	if accountIndex < 0 {
		return Lead{}, false, "agency account not found"
	}
	account := state.AgencyAccounts[accountIndex]
	if account.Status == AgencyAccountStatusRejected {
		return Lead{}, false, "agency account is rejected"
	}
	profileURL := candidate.ProfileURL
	company := cleanText(candidate.AgencyAccountName)
	if company == "" {
		company = account.Name
	}
	leadType, fitScore, fitReasons := promotedAgencyLeadDisposition(title)
	evidence := promotedAgencyLeadEvidence(candidate)
	lead := Lead{
		ID:            stableLeadID("agency_contact_candidate", name, profileURL, nil),
		Source:        "Agency website contact - " + account.Name,
		Name:          name,
		FirstName:     firstName(name),
		ProfileURL:    profileURL,
		Title:         &title,
		Company:       &company,
		LeadType:      leadType,
		Status:        LeadStatusEligible,
		MessageStatus: MessageStatusNone,
		FitScore:      fitScore,
		FitReasons:    fitReasons,
		RejectReasons: []string{},
		EvidenceText:  evidence,
		MenuState:     "unknown",
		ImportedAt:    importedAt,
		UpdatedAt:     importedAt,
		Notes:         []string{"promoted from agency contact candidate " + candidate.ID},
	}
	linkLeadToAgencyAccount(&lead, account)
	return lead, true, ""
}

func promotedAgencyLeadDisposition(title string) (LeadType, int, []string) {
	lower := strings.ToLower(cleanText(title))
	reasons := []string{
		"reviewed website contact candidate",
		"official agency website linked this LinkedIn profile",
		"agency account qualified or previously sourced",
		"title supplied during review: " + cleanText(title),
	}
	switch {
	case containsAny(lower, "founder", "co-founder", "owner", "partner", "principal", "ceo", "president", "chairman"):
		return LeadTypeAgencyFounder, 95, append(reasons, "founder/partner executive title")
	case containsAny(lower, "recruit", "talent", "people", "resourcing"):
		return LeadTypeAgencyResource, 88, append(reasons, "agency talent/resource title")
	default:
		return LeadTypeAgencyDelivery, 88, append(reasons, "agency delivery/sales/client leadership title")
	}
}

func promotedAgencyLeadEvidence(candidate AgencyContactCandidate) string {
	parts := []string{"Agency contact candidate: " + candidate.ID}
	if candidate.SourceURL != nil && cleanText(*candidate.SourceURL) != "" {
		parts = append(parts, "Source URL: "+cleanText(*candidate.SourceURL))
	}
	if candidate.ProfileURL != nil && cleanText(*candidate.ProfileURL) != "" {
		parts = append(parts, "LinkedIn profile: "+cleanText(*candidate.ProfileURL))
	}
	parts = append(parts, candidate.Evidence...)
	if len(candidate.Notes) > 0 {
		parts = append(parts, "Review notes: "+strings.Join(candidate.Notes, "; "))
	}
	return truncateEvidence(strings.Join(parts, "\n"))
}

func preservePromotedLeadRuntimeFields(lead *Lead, existing Lead) {
	lead.ID = existing.ID
	lead.ImportedAt = existing.ImportedAt
	lead.Draft = existing.Draft
	lead.MessageStatus = existing.MessageStatus
	lead.MessageStatusAt = existing.MessageStatusAt
	lead.SendAttempts = existing.SendAttempts
	if len(existing.Notes) > 0 {
		lead.Notes = existing.Notes
	}
}

func usableReviewedContactName(name string) bool {
	cleaned := strings.ToLower(cleanText(name))
	switch cleaned {
	case "", "linkedin", "linked in", "linkedin profile", "profile", "social", "contact", "learn more":
		return false
	default:
		return true
	}
}

func findAgencyContactCandidateByID(candidates []AgencyContactCandidate, id string) int {
	for index, candidate := range candidates {
		if candidate.ID == id {
			return index
		}
	}
	return -1
}
