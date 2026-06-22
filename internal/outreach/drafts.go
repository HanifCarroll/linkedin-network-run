package outreach

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func DraftMessages(state *OutreachState, limit int) DraftReport {
	state.Normalize()
	items := draftableQueue(*state, limit)
	now := time.Now()
	for i := range items {
		index := findLeadByID(state.Leads, items[i].ID)
		if index < 0 {
			continue
		}
		draft := BuildMessageDraftRecord(state.Leads[index], now)
		state.Leads[index].Draft = &draft
		state.Leads[index].MessageStatus = MessageStatusDrafted
		state.Leads[index].UpdatedAt = now
		items[i].MessageStatus = MessageStatusDrafted
		items[i].Draft = &draft.Body
	}
	return DraftReport{GeneratedAt: now, Items: items}
}

func draftableQueue(state OutreachState, limit int) []QueueItem {
	items := Queue(state, []LeadStatus{LeadStatusEligible}, 0, false)
	filtered := []QueueItem{}
	for _, item := range items {
		if isTerminalMessageStatus(item.MessageStatus) {
			continue
		}
		filtered = append(filtered, item)
		if limit > 0 && len(filtered) >= limit {
			break
		}
	}
	return filtered
}

func isTerminalMessageStatus(status MessageStatus) bool {
	switch status {
	case MessageStatusDryRunReady, MessageStatusNeedsEdit, MessageStatusApproved, MessageStatusSendFailed, MessageStatusSent, MessageStatusManuallySent, MessageStatusNotMessageable, MessageStatusConversationExists, MessageStatusBlocked, MessageStatusReplied, MessageStatusRepliedNotFit, MessageStatusRepliedFuture, MessageStatusRepliedUnknown:
		return true
	default:
		return false
	}
}

func BuildMessageDraft(lead Lead) string {
	return BuildMessageDraftRecord(lead, time.Now()).Body
}

func BuildMessageDraftRecord(lead Lead, generatedAt time.Time) MessageDraft {
	angle := draftAngle(lead)
	return MessageDraft{
		Subject:     messageSubject(lead),
		Body:        messageBodyForAngle(lead, angle),
		Angle:       angle,
		Evidence:    draftEvidence(lead),
		GeneratedAt: generatedAt,
	}
}

func messageBodyForAngle(lead Lead, angle string) string {
	switch lead.LeadType {
	case LeadTypeContractRecruiter:
		return recruiterDraft(lead)
	case LeadTypeAgencyResource, LeadTypeAgencyDelivery, LeadTypeAgencyFounder:
		return agencyDraft(lead)
	default:
		return generalDraft(lead)
	}
}

func draftAngle(lead Lead) string {
	switch lead.LeadType {
	case LeadTypeContractRecruiter:
		return "contract recruiter routing for remote C2C/1099 product-engineering work"
	case LeadTypeAgencyResource:
		if isWebsiteAgencyLead(lead) {
			return "web design/WordPress agency resource manager for senior frontend/CMS implementation support"
		}
		return "agency resource manager for immediate outside senior engineering coverage"
	case LeadTypeAgencyDelivery:
		if isWebsiteAgencyLead(lead) {
			return "web design/WordPress agency delivery leader for frontend-heavy implementation overflow"
		}
		return "agency delivery or technical leader for overflow/rescue/prototyping support"
	case LeadTypeAgencyFounder:
		if isWebsiteAgencyLead(lead) {
			return "web design/WordPress agency founder for senior frontend/CMS implementation capacity"
		}
		return "agency founder/partner for senior contractor capacity on active client work"
	default:
		return "general contract product-engineering availability"
	}
}

func draftEvidence(lead Lead) []string {
	evidence := []string{}
	if lead.Title != nil {
		evidence = append(evidence, "Title: "+*lead.Title)
	}
	if company := companyForDraft(lead.Company); company != "" {
		evidence = append(evidence, "Company: "+company)
	}
	if lead.AgencyAccountName != nil {
		evidence = append(evidence, "Agency account: "+*lead.AgencyAccountName)
	}
	if len(lead.AgencyAccountReasons) > 0 {
		evidence = append(evidence, "Agency account reasons: "+strings.Join(lead.AgencyAccountReasons, "; "))
	}
	if len(lead.FitReasons) > 0 {
		evidence = append(evidence, "Fit reasons: "+strings.Join(lead.FitReasons, "; "))
	}
	if lead.AgencyAccountEvidence != "" {
		evidence = append(evidence, "Agency account evidence: "+lead.AgencyAccountEvidence)
	}
	if lead.EvidenceText != "" {
		evidence = append(evidence, "Sales Nav evidence: "+lead.EvidenceText)
	}
	return evidence
}

func recruiterDraft(lead Lead) string {
	return recruiterContractDraft(lead.FirstName)
}

func agencyDraft(lead Lead) string {
	target := "your team"
	if lead.AgencyAccountName != nil && cleanText(*lead.AgencyAccountName) != "" {
		target = cleanText(*lead.AgencyAccountName)
	} else if company := companyForDraft(lead.Company); company != "" {
		target = company
	}
	_ = target
	return agencyProjectDraft(lead.FirstName)
}

func isWebsiteAgencyLead(lead Lead) bool {
	parts := []string{lead.AgencyAccountEvidence, lead.EvidenceText}
	parts = append(parts, lead.AgencyAccountReasons...)
	parts = append(parts, lead.FitReasons...)
	if lead.AgencyAccountName != nil {
		parts = append(parts, *lead.AgencyAccountName)
	}
	if lead.Company != nil {
		parts = append(parts, *lead.Company)
	}
	text := strings.ToLower(strings.Join(parts, " "))
	return containsAny(text, "website/wordpress build account signal", "wordpress", "shopify", "webflow", "cms", "web design", "web designer", "web developer", "website design", "website development", "high-performing websites")
}

func companyForDraft(company *string) string {
	if company == nil {
		return ""
	}
	value := cleanText(*company)
	if value == "" || isLikelyLocation(value) {
		return ""
	}
	return value
}

func isLikelyLocation(value string) bool {
	lower := strings.ToLower(cleanText(value))
	return containsAny(lower, "metropolitan area", "bay area", "united states") || strings.Count(value, ",") >= 2
}

func generalDraft(lead Lead) string {
	return recruiterContractDraft(lead.FirstName)
}

func agencyProjectDraft(firstName string) string {
	return fmt.Sprintf("Hi %s,\n\nI'm a full-stack product engineer (8 YoE) that builds and launches AI-powered web & mobile products. I'm reaching out about project or overflow work.\n\nRecent projects:\n\n• Turned an AI media MVP into a production agent platform for Amazon sellers (first 100 paying customers)\n• Built and launched a Spanish reading app (iOS, Android + web) from concept to App Store with teacher workflows, AI features, and subscriptions\n\nUS citizen contracting via my LLC (1099/C2C). Available for US-hours work from Buenos Aires. Comfortable collaborating with design and product teams.\n\nWould you like me to send my resume and project examples?", firstName)
}

func recruiterContractDraft(firstName string) string {
	return fmt.Sprintf("Hi %s,\n\nI'm a full-stack product engineer (8 YoE) that builds and launches AI-powered web & mobile products. I'm reaching out about contract work.\n\nRecent wins:\n\n• Turned an AI media MVP into a production agent platform for Amazon sellers (first 100 paying customers)\n• Built and launched a Spanish reading app (iOS, Android + web) from concept to App Store with teacher workflows, AI features, and subscriptions\n\nUS citizen contracting via my LLC (1099/C2C). Available for US-hours work from Buenos Aires.\n\nWould you like me to send my resume and project examples?", firstName)
}

func RenderDraftMarkdown(report DraftReport) string {
	lines := []string{
		fmt.Sprintf("# Recruiter And Agency Drafts %s", report.GeneratedAt.Format("2006-01-02")),
		"",
		fmt.Sprintf("- Generated: `%s`", report.GeneratedAt.Format(time.RFC3339)),
		fmt.Sprintf("- Draft count: %d", len(report.Items)),
		"- Send policy: draft-only. No connection request or LinkedIn message was sent by this command.",
	}
	if len(report.Items) == 0 {
		lines = append(lines, "", "No eligible recruiter or agency leads need drafts.")
		return strings.Join(lines, "\n")
	}
	for _, item := range report.Items {
		lines = append(lines, "")
		lines = append(lines, "## "+cleanInline(item.Name))
		lines = append(lines, "- ID: `"+item.ID+"`")
		lines = append(lines, "- Source: "+cleanInline(item.Source))
		lines = append(lines, "- Type: `"+string(item.LeadType)+"`")
		lines = append(lines, fmt.Sprintf("- Fit score: `%d`", item.FitScore))
		if item.ProfileURL != nil {
			lines = append(lines, "- Profile: "+cleanInline(*item.ProfileURL))
		}
		if item.Title != nil {
			lines = append(lines, "- Title: "+cleanInline(*item.Title))
		}
		if item.Company != nil {
			lines = append(lines, "- Company: "+cleanInline(*item.Company))
		}
		if item.AgencyAccountName != nil {
			lines = append(lines, "- Agency account: "+cleanInline(*item.AgencyAccountName))
		}
		if item.AgencyAccountURL != nil {
			lines = append(lines, "- Agency account URL: "+cleanInline(*item.AgencyAccountURL))
		}
		if len(item.AgencyAccountReasons) > 0 {
			lines = append(lines, "- Agency account reasons: "+cleanInline(strings.Join(item.AgencyAccountReasons, "; ")))
		}
		if len(item.FitReasons) > 0 {
			lines = append(lines, "- Fit reasons: "+cleanInline(strings.Join(item.FitReasons, "; ")))
		}
		if item.AgencyAccountEvidence != "" {
			lines = append(lines, "- Agency account evidence: "+cleanInline(item.AgencyAccountEvidence))
		}
		if item.Draft != nil {
			if leadDraftAngle := draftAngleFromQueueItem(item); leadDraftAngle != "" {
				lines = append(lines, "- Draft angle: "+cleanInline(leadDraftAngle))
			}
		}
		if strings.TrimSpace(item.EvidenceText) != "" {
			lines = append(lines, "- Evidence: "+cleanInline(item.EvidenceText))
		}
		lines = append(lines, "", "Draft:", "")
		if item.Draft != nil {
			lines = append(lines, renderMarkdownQuote(*item.Draft)...)
		} else {
			lines = append(lines, "> No draft generated.")
		}
	}
	return strings.Join(lines, "\n")
}

func WriteDraftMarkdown(path string, report DraftReport) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", filepath.Dir(path), err)
	}
	if err := os.WriteFile(path, []byte(RenderDraftMarkdown(report)), 0o644); err != nil {
		return fmt.Errorf("writing %s: %w", path, err)
	}
	return nil
}

func findLeadByID(leads []Lead, id string) int {
	for i, lead := range leads {
		if lead.ID == id {
			return i
		}
	}
	return -1
}

func cleanInline(value string) string {
	return strings.ReplaceAll(cleanText(value), "`", "'")
}

func renderMarkdownQuote(value string) []string {
	lines := []string{}
	for _, line := range strings.Split(strings.TrimSpace(strings.ReplaceAll(value, "\r\n", "\n")), "\n") {
		if strings.TrimSpace(line) == "" {
			lines = append(lines, ">")
			continue
		}
		lines = append(lines, "> "+strings.ReplaceAll(line, "`", "'"))
	}
	if len(lines) == 0 {
		return []string{">"}
	}
	return lines
}

func draftAngleFromQueueItem(item QueueItem) string {
	switch item.LeadType {
	case LeadTypeContractRecruiter:
		return "contract recruiter routing for remote C2C/1099 product-engineering work"
	case LeadTypeAgencyResource:
		return "agency resource manager for immediate outside senior engineering coverage"
	case LeadTypeAgencyDelivery:
		return "agency delivery or technical leader for overflow/rescue/prototyping support"
	case LeadTypeAgencyFounder:
		return "agency founder/partner for senior contractor capacity on active client work"
	default:
		return ""
	}
}
