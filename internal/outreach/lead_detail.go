package outreach

import (
	"fmt"
	"strings"
	"time"
)

type LeadDetail struct {
	StatePath              string                  `json:"state_path,omitempty"`
	Bucket                 string                  `json:"bucket"`
	Sendable               bool                    `json:"sendable"`
	Lead                   Lead                    `json:"lead"`
	QueueItem              QueueItem               `json:"queue_item"`
	AgencyAccount          *AgencyAccount          `json:"agency_account,omitempty"`
	AgencyContactCandidate *AgencyContactCandidate `json:"agency_contact_candidate,omitempty"`
}

func BuildLeadDetail(state OutreachState, statePath string, leadID string) (LeadDetail, bool) {
	state.Normalize()
	index := findLeadByID(state.Leads, cleanText(leadID))
	if index < 0 {
		return LeadDetail{}, false
	}
	lead := state.Leads[index]
	bucket := bucketForLead(lead)
	detail := LeadDetail{
		StatePath: statePath,
		Bucket:    bucket,
		Sendable:  leadMatchesSendableBucket(state, lead, bucket),
		Lead:      lead,
		QueueItem: queueItemFromLead(lead, true),
	}
	if lead.AgencyAccountID != nil {
		if accountIndex := findAgencyAccountByID(state.AgencyAccounts, cleanText(*lead.AgencyAccountID)); accountIndex >= 0 {
			account := state.AgencyAccounts[accountIndex]
			detail.AgencyAccount = &account
		}
	}
	if candidateIndex := findAgencyContactCandidateByPromotedLeadID(state.AgencyContactCandidates, lead.ID); candidateIndex >= 0 {
		candidate := state.AgencyContactCandidates[candidateIndex]
		detail.AgencyContactCandidate = &candidate
	}
	return detail, true
}

func RenderLeadDetailText(detail LeadDetail) string {
	lead := detail.Lead
	lines := []string{
		"lead=" + lead.ID,
		"state=" + valueOrDash(detail.StatePath),
		"name=" + valueOrDash(lead.Name),
		"bucket=" + valueOrDash(detail.Bucket),
		"type=" + string(lead.LeadType),
		"status=" + string(lead.Status),
		"message_status=" + string(lead.MessageStatus),
		fmt.Sprintf("sendable=%t", detail.Sendable),
		fmt.Sprintf("fit_score=%d", lead.FitScore),
	}
	if lead.ProfileURL != nil {
		lines = append(lines, "profile_url="+cleanText(*lead.ProfileURL))
	}
	if lead.Title != nil {
		lines = append(lines, "title="+cleanText(*lead.Title))
	}
	if lead.Company != nil {
		lines = append(lines, "company="+cleanText(*lead.Company))
	}
	if detail.AgencyAccount != nil {
		account := detail.AgencyAccount
		lines = append(lines,
			"",
			"agency_account="+account.ID,
			"agency_account_name="+valueOrDash(account.Name),
			"agency_account_status="+string(account.Status),
			fmt.Sprintf("agency_account_score=%d", account.FitScore),
		)
		if account.AccountURL != nil {
			lines = append(lines, "agency_account_url="+cleanText(*account.AccountURL))
		}
		if account.Website != nil {
			lines = append(lines, "agency_account_website="+cleanText(*account.Website))
		}
		if len(account.FitReasons) > 0 {
			lines = append(lines, "agency_account_reasons="+strings.Join(account.FitReasons, "; "))
		}
	}
	if detail.AgencyContactCandidate != nil {
		candidate := detail.AgencyContactCandidate
		lines = append(lines,
			"",
			"agency_contact_candidate="+candidate.ID,
			"candidate_source="+valueOrDash(candidate.Source),
			"candidate_status="+string(candidate.Status),
			"candidate_review_status="+string(candidate.ReviewStatus),
		)
		if candidate.SourceURL != nil {
			lines = append(lines, "candidate_source_url="+cleanText(*candidate.SourceURL))
		}
		if candidate.ProfileURL != nil {
			lines = append(lines, "candidate_profile_url="+cleanText(*candidate.ProfileURL))
		}
		if len(candidate.Evidence) > 0 {
			lines = append(lines, "candidate_evidence="+strings.Join(candidate.Evidence, "; "))
		}
	}
	if len(lead.FitReasons) > 0 {
		lines = append(lines, "", "fit_reasons:")
		for _, reason := range lead.FitReasons {
			lines = append(lines, "- "+cleanText(reason))
		}
	}
	if lead.EvidenceText != "" {
		lines = append(lines, "", "evidence:", lead.EvidenceText)
	}
	if lead.Draft != nil {
		lines = append(lines,
			"",
			"draft:",
			"subject="+valueOrDash(lead.Draft.Subject),
			"angle="+valueOrDash(lead.Draft.Angle),
			"generated_at="+formatOptionalTime(lead.Draft.GeneratedAt),
		)
		if len(lead.Draft.Evidence) > 0 {
			lines = append(lines, "draft_evidence:")
			for _, evidence := range lead.Draft.Evidence {
				lines = append(lines, "- "+cleanText(evidence))
			}
		}
		lines = append(lines, "body:", lead.Draft.Body)
	}
	if len(lead.SendAttempts) > 0 {
		lines = append(lines, "", "send_attempts:")
		for _, attempt := range lead.SendAttempts {
			line := fmt.Sprintf("- %s status=%s dry_run=%t", attempt.At.Format(time.RFC3339), attempt.Status, attempt.DryRun)
			if attempt.RunID != "" {
				line += " run_id=" + attempt.RunID
			}
			if attempt.Note != nil && cleanText(*attempt.Note) != "" {
				line += " note=" + cleanText(*attempt.Note)
			}
			if attempt.OutPath != "" {
				line += " out=" + attempt.OutPath
			}
			lines = append(lines, line)
		}
	}
	if len(lead.Notes) > 0 {
		lines = append(lines, "", "notes:")
		for _, note := range lead.Notes {
			lines = append(lines, "- "+cleanText(note))
		}
	}
	return strings.Join(lines, "\n")
}

func findAgencyContactCandidateByPromotedLeadID(candidates []AgencyContactCandidate, leadID string) int {
	cleanedID := cleanText(leadID)
	for index, candidate := range candidates {
		if candidate.PromotedLeadID != nil && cleanText(*candidate.PromotedLeadID) == cleanedID {
			return index
		}
	}
	return -1
}

func valueOrDash(value string) string {
	cleaned := cleanText(value)
	if cleaned == "" {
		return "-"
	}
	return cleaned
}

func formatOptionalTime(value time.Time) string {
	if value.IsZero() {
		return "-"
	}
	return value.Format(time.RFC3339)
}
