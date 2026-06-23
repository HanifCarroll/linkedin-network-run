package outreach

import (
	"fmt"
	"hash/fnv"
	"sort"
	"strings"
	"time"

	"github.com/hanifcarroll/linkedin-network-run/internal/app"
)

const (
	defaultEligibleScore = 70
	defaultReviewScore   = 50
)

type ImportOptions struct {
	OnlyConnectable bool
	AgencyAccount   *AgencyAccount
}

func ImportCapture(state *OutreachState, capture app.SalesNavCapture, options ImportOptions) (ImportSummary, error) {
	state.Normalize()
	source := ""
	if capture.Source != nil {
		source = cleanText(*capture.Source)
	}
	if source == "" {
		return ImportSummary{}, fmt.Errorf("capture did not include source")
	}

	now := time.Now()
	summary := ImportSummary{Source: source}
	state.CaptureCursors[source] = captureCursorFromCapture(source, capture, now)
	for _, row := range capture.Rows {
		if row.Name == nil || cleanText(*row.Name) == "" {
			continue
		}
		menuState := "unknown"
		if row.MenuState != nil {
			menuState = *row.MenuState
		}
		if options.OnlyConnectable && menuState != "connectable" {
			continue
		}
		lead := buildLead(source, row, capture.CapturedAt, now, options.AgencyAccount)
		index := findLeadIndex(state.Leads, lead)
		if index >= 0 {
			lead.ID = state.Leads[index].ID
			lead.ImportedAt = state.Leads[index].ImportedAt
			lead.Draft = state.Leads[index].Draft
			lead.MessageStatus = state.Leads[index].MessageStatus
			lead.SendAttempts = state.Leads[index].SendAttempts
			if len(state.Leads[index].Notes) > 0 {
				lead.Notes = state.Leads[index].Notes
			}
			if options.AgencyAccount == nil && state.Leads[index].AgencyAccountID != nil {
				lead.AgencyAccountID = state.Leads[index].AgencyAccountID
				lead.AgencyAccountName = state.Leads[index].AgencyAccountName
				lead.AgencyAccountURL = state.Leads[index].AgencyAccountURL
				lead.AgencyAccountReasons = state.Leads[index].AgencyAccountReasons
				lead.AgencyAccountEvidence = state.Leads[index].AgencyAccountEvidence
			}
			state.Leads[index] = lead
			summary.Updated++
		} else {
			state.Leads = append(state.Leads, lead)
			summary.Stored++
		}
		switch lead.Status {
		case LeadStatusEligible:
			summary.Eligible++
		case LeadStatusNeedsReview:
			summary.Reviewed++
		case LeadStatusRejected:
			summary.Rejected++
		}
	}
	sortLeads(state.Leads)
	summary.TotalLeads = len(state.Leads)
	return summary, nil
}

func buildLead(source string, row app.SalesNavCaptureRow, capturedAt *string, importedAt time.Time, agencyAccount *AgencyAccount) Lead {
	name := cleanText(pointerValue(row.Name))
	profileURL := row.ProfileURL
	if profileURL == nil && row.ScrollURN != nil {
		profileURL = app.SalesProfileURNToLeadURL(*row.ScrollURN)
	}
	rawText := rawEvidenceText(row)
	text := truncateEvidence(rawText)
	company := extractCompanyFromLinks(row.Links)
	if agencyAccount != nil && companyForDraft(company) == "" {
		company = &agencyAccount.Name
	}
	title := extractTitleCompany(name, rawText, company)
	leadType, score, reasons, rejects := classifyLead(source, title, company, text, agencyAccount)
	status := LeadStatusEligible
	if leadType == LeadTypeBadFit || score < defaultReviewScore {
		status = LeadStatusRejected
	} else if score < defaultEligibleScore {
		status = LeadStatusNeedsReview
	}
	lead := Lead{
		ID:              stableLeadID(source, name, profileURL, row.ScrollURN),
		Source:          source,
		Name:            name,
		FirstName:       firstName(name),
		ProfileURL:      profileURL,
		SalesProfileURN: row.ScrollURN,
		Title:           title,
		Company:         company,
		LeadType:        leadType,
		Status:          status,
		MessageStatus:   MessageStatusNone,
		FitScore:        score,
		FitReasons:      reasons,
		RejectReasons:   rejects,
		EvidenceText:    text,
		MenuState:       menuState(row),
		CapturedAt:      capturedAt,
		ImportedAt:      importedAt,
		UpdatedAt:       importedAt,
		Notes:           []string{},
	}
	if agencyAccount != nil {
		linkLeadToAgencyAccount(&lead, *agencyAccount)
	}
	return lead
}

func classifyLead(source string, title *string, company *string, evidence string, agencyAccount *AgencyAccount) (LeadType, int, []string, []string) {
	titleText := strings.ToLower(pointerValue(title))
	companyText := strings.ToLower(pointerValue(company))
	evidenceLower := strings.ToLower(evidence)
	profileText := strings.Join([]string{titleText, companyText, evidenceLower}, " ")
	score := 0
	reasons := []string{}
	rejects := []string{}

	titleLooksRecruiter := containsAny(titleText, "recruiter", "talent acquisition", "talent partner", "sourcer", "account manager", "staffing")
	titleLooksAgencyResource := containsAny(titleText, "resource manager", "resourcing", "talent manager", "head of talent")
	titleLooksAgencyDelivery := containsAny(titleText, "delivery", "technical director", "engineering director", "head of engineering", "vp engineering", "head of delivery", "client services", "partnerships")
	titleLooksAgencyFounder := containsAny(titleText, "founder", "partner", "principal", "owner", "ceo", "president", "managing director")
	titleLooksAgencyPersona := titleLooksAgencyResource || titleLooksAgencyDelivery || titleLooksAgencyFounder
	accountLooksAgency := agencyAccount != nil && agencyAccount.Status == AgencyAccountStatusQualified
	companyLooksAgency := accountLooksAgency || containsAny(companyText, "product studio", "digital product", "digital agency", "software agency", "development agency", "design agency", "dev shop", "studio", "consultancy", "consulting", "agency")
	companyLooksStaffing := containsAny(companyText, "staffing", "recruiting", "recruitment", "talent solutions", "consulting firm")
	contractSignal := containsAny(profileText, "contract", "c2c", "1099", "consultant", "fractional", "freelance", "temporary", "staff augmentation")
	softwareSignal := containsAny(profileText, "react", "typescript", "node", "frontend", "front-end", "full-stack", "full stack", "product engineer", "software engineer", "ai", "genai", "saas")

	leadType := LeadTypeBadFit
	switch {
	case titleLooksRecruiter || companyLooksStaffing:
		leadType = LeadTypeContractRecruiter
		score += 40
		reasons = append(reasons, "recruiter/staffing signal")
	case companyLooksAgency && titleLooksAgencyPersona:
		switch {
		case titleLooksAgencyResource:
			leadType = LeadTypeAgencyResource
			reasons = append(reasons, "agency resource/resourcing title")
		case titleLooksAgencyDelivery:
			leadType = LeadTypeAgencyDelivery
			reasons = append(reasons, "agency delivery/technical leadership title")
		case titleLooksAgencyFounder:
			leadType = LeadTypeAgencyFounder
			reasons = append(reasons, "agency founder/partner title")
		default:
			leadType = LeadTypeAgencyDelivery
			reasons = append(reasons, "agency/delivery source signal")
		}
		score += 40
	default:
		rejects = append(rejects, "not a recruiter or agency/resource target")
	}

	if titleLooksRecruiter || titleLooksAgencyResource || titleLooksAgencyDelivery || titleLooksAgencyFounder {
		score += 25
		reasons = append(reasons, "title matches target persona")
	}
	if contractSignal {
		score += 15
		reasons = append(reasons, "contract/fractional signal")
	}
	if softwareSignal {
		score += 12
		reasons = append(reasons, "software/product/AI signal")
	}
	if companyLooksAgency || companyLooksStaffing {
		score += 10
		reasons = append(reasons, "company/source matches target market")
	}
	if accountLooksAgency {
		score += 10
		reasons = append(reasons, "qualified agency account context")
	}
	if containsAny(profileText, "onsite only", "clearance", "secret clearance", "top secret", "w2 only", "local candidates only") {
		score -= 35
		rejects = append(rejects, "likely blocked by onsite, clearance, or W2-only requirement")
	}
	if containsAny(profileText, "europe", "uk only", "india only", "latam only", "canada only") && !containsAny(profileText, "us", "united states", "remote") {
		score -= 20
		rejects = append(rejects, "market/location signal may not match US contract work")
	}

	if score > 100 {
		score = 100
	}
	if score < 0 {
		score = 0
	}
	if len(reasons) == 0 {
		reasons = append(reasons, "weak target evidence")
	}
	if leadType == LeadTypeBadFit && len(rejects) == 0 {
		rejects = append(rejects, "failed target-persona classification")
	}
	return leadType, score, reasons, rejects
}

func captureCursorFromCapture(source string, capture app.SalesNavCapture, updatedAt time.Time) CaptureCursor {
	var lastPage *app.SalesNavCapturePage
	if capture.Page != nil {
		lastPage = capture.Page
	} else if len(capture.Pages) > 0 {
		lastPage = &capture.Pages[len(capture.Pages)-1]
	}
	resumeURL := capture.ResumeURL
	if resumeURL == nil {
		resumeURL = capture.URL
	}
	if resumeURL == nil && lastPage != nil {
		resumeURL = lastPage.URL
	}
	capturedPages := uint32(len(capture.Pages))
	if capturedPages == 0 && capture.Page != nil {
		capturedPages = 1
	}
	rawRowCount := uint32(len(capture.Rows))
	if capture.RawRowCount != nil {
		rawRowCount = *capture.RawRowCount
	}
	outputRowCount := uint32(len(capture.Rows))
	if capture.OutputRowCount != nil {
		outputRowCount = *capture.OutputRowCount
	}
	var pageLabel *string
	if lastPage != nil {
		pageLabel = lastPage.PageLabel
	}
	return CaptureCursor{
		Source:              source,
		UpdatedAt:           updatedAt,
		CapturedAt:          capture.CapturedAt,
		ResumeURL:           resumeURL,
		PageLabel:           pageLabel,
		CapturedPages:       capturedPages,
		RawRowCount:         rawRowCount,
		OutputRowCount:      outputRowCount,
		ConnectableCount:    app.CaptureStateCount(capture, "connectable"),
		AlreadyPendingCount: app.CaptureStateCount(capture, "already-pending"),
		StateCounts:         capture.StateCounts,
	}
}

func Queue(state OutreachState, statuses []LeadStatus, limit int, includeDraft bool) []QueueItem {
	state.Normalize()
	statusSet := map[LeadStatus]bool{}
	for _, status := range statuses {
		statusSet[status] = true
	}
	items := []QueueItem{}
	for _, lead := range state.Leads {
		if len(statusSet) > 0 && !statusSet[lead.Status] {
			continue
		}
		draft := (*string)(nil)
		if includeDraft && lead.Draft != nil {
			draft = &lead.Draft.Body
		}
		items = append(items, QueueItem{
			ID:                    lead.ID,
			Name:                  lead.Name,
			ProfileURL:            lead.ProfileURL,
			Title:                 lead.Title,
			Company:               lead.Company,
			AgencyAccountName:     lead.AgencyAccountName,
			AgencyAccountURL:      lead.AgencyAccountURL,
			AgencyAccountReasons:  lead.AgencyAccountReasons,
			AgencyAccountEvidence: lead.AgencyAccountEvidence,
			Source:                lead.Source,
			LeadType:              lead.LeadType,
			Status:                lead.Status,
			MessageStatus:         lead.MessageStatus,
			FitScore:              lead.FitScore,
			FitReasons:            lead.FitReasons,
			EvidenceText:          lead.EvidenceText,
			Draft:                 draft,
		})
	}
	sort.SliceStable(items, func(i, j int) bool {
		if items[i].FitScore == items[j].FitScore {
			return items[i].Name < items[j].Name
		}
		return items[i].FitScore > items[j].FitScore
	})
	if limit > 0 && len(items) > limit {
		return items[:limit]
	}
	return items
}

func Counts(state OutreachState) StatusCounts {
	state.Normalize()
	counts := StatusCounts{
		ByStatus:                             map[LeadStatus]int{},
		ByLeadType:                           map[LeadType]int{},
		ByMessageStatus:                      map[MessageStatus]int{},
		BySource:                             map[string]int{},
		ByAgencyAccountStatus:                map[AgencyAccountStatus]int{},
		ByAgencyAccountSource:                map[string]int{},
		ByAgencyContactCandidateStatus:       map[AgencyContactCandidateStatus]int{},
		ByAgencyContactCandidateReviewStatus: map[AgencyContactReviewStatus]int{},
		ByAgencyContactCandidateSource:       map[string]int{},
	}
	for _, lead := range state.Leads {
		counts.ByStatus[lead.Status]++
		counts.ByLeadType[lead.LeadType]++
		counts.ByMessageStatus[lead.MessageStatus]++
		counts.BySource[lead.Source]++
	}
	for _, account := range state.AgencyAccounts {
		counts.ByAgencyAccountStatus[account.Status]++
		counts.ByAgencyAccountSource[account.Source]++
	}
	for _, candidate := range state.AgencyContactCandidates {
		counts.ByAgencyContactCandidateStatus[candidate.Status]++
		counts.ByAgencyContactCandidateReviewStatus[candidate.ReviewStatus]++
		counts.ByAgencyContactCandidateSource[candidate.Source]++
	}
	return counts
}

func findLeadIndex(leads []Lead, candidate Lead) int {
	candidateKey := leadKey(candidate)
	for i, lead := range leads {
		if leadKey(lead) == candidateKey {
			return i
		}
	}
	return -1
}

func leadKey(lead Lead) string {
	if lead.ProfileURL != nil && cleanText(*lead.ProfileURL) != "" {
		return "url:" + app.NormalizeLinkedInURL(*lead.ProfileURL)
	}
	if lead.SalesProfileURN != nil && cleanText(*lead.SalesProfileURN) != "" {
		return "urn:" + cleanText(*lead.SalesProfileURN)
	}
	return "name:" + strings.ToLower(lead.Source+"|"+lead.Name)
}

func stableLeadID(source string, name string, profileURL *string, salesProfileURN *string) string {
	key := strings.ToLower(source + "|" + name)
	if profileURL != nil && cleanText(*profileURL) != "" {
		key = app.NormalizeLinkedInURL(*profileURL)
	} else if salesProfileURN != nil && cleanText(*salesProfileURN) != "" {
		key = cleanText(*salesProfileURN)
	}
	hash := fnv.New32a()
	_, _ = hash.Write([]byte(key))
	return fmt.Sprintf("lead_%08x", hash.Sum32())
}

func sortLeads(leads []Lead) {
	sort.SliceStable(leads, func(i, j int) bool {
		if leads[i].FitScore == leads[j].FitScore {
			return leads[i].Name < leads[j].Name
		}
		return leads[i].FitScore > leads[j].FitScore
	})
}

func rawEvidenceText(row app.SalesNavCaptureRow) string {
	parts := []string{}
	if row.Text != nil {
		parts = append(parts, *row.Text)
	}
	for _, link := range row.Links {
		if link.Text != nil {
			parts = append(parts, *link.Text)
		}
		if link.Aria != nil {
			parts = append(parts, *link.Aria)
		}
	}
	parts = append(parts, menuLabelText(row.MenuLabels)...)
	return strings.Join(parts, "\n")
}

func extractTitleCompany(name string, evidence string, company *string) *string {
	lines := splitEvidenceLines(evidence)
	cleanName := strings.ToLower(cleanText(name))
	filtered := []string{}
	for _, line := range lines {
		lower := strings.ToLower(line)
		if lower == "about:" || lower == "experience:" {
			break
		}
		if lower == cleanName || strings.HasPrefix(lower, "add ") || lower == "2nd" || lower == "3rd+" || lower == "viewed" || lower == "saved" {
			continue
		}
		if containsAny(lower, "connect", "message", "save", "more actions", "selection", "degree connection", "linkedin premium", "last active") || strings.HasPrefix(lower, "·") {
			continue
		}
		filtered = append(filtered, line)
	}
	var title *string
	for _, line := range filtered {
		lower := strings.ToLower(line)
		if title == nil && containsAny(lower, "recruiter", "talent", "resource", "delivery", "technical director", "engineering", "founder", "partner", "principal", "owner", "ceo", "account manager", "sourcer") {
			title = optionalClean(cleanTitleLine(line, company))
			continue
		}
	}
	return title
}

func extractCompanyFromLinks(links []app.SalesNavCaptureLink) *string {
	for _, link := range links {
		if link.Href == nil || link.Text == nil {
			continue
		}
		if strings.Contains(*link.Href, "/sales/company/") {
			return optionalClean(*link.Text)
		}
	}
	return nil
}

func cleanTitleLine(line string, company *string) string {
	cleaned := cleanText(strings.ReplaceAll(line, "\u00a0", " "))
	if company == nil {
		return cleaned
	}
	companyText := cleanText(*company)
	if companyText == "" {
		return cleaned
	}
	lowerCleaned := strings.ToLower(cleaned)
	lowerCompany := strings.ToLower(companyText)
	if lowerCleaned == lowerCompany {
		return ""
	}
	suffix := " " + lowerCompany
	if strings.HasSuffix(lowerCleaned, suffix) {
		cleaned = strings.TrimSpace(cleaned[:len(cleaned)-len(companyText)])
	}
	return cleaned
}

func splitEvidenceLines(value string) []string {
	raw := strings.FieldsFunc(value, func(r rune) bool {
		return r == '\n' || r == '\r' || r == '\t'
	})
	result := []string{}
	for _, item := range raw {
		cleaned := cleanText(item)
		if cleaned != "" {
			result = append(result, cleaned)
		}
	}
	return result
}

func menuState(row app.SalesNavCaptureRow) string {
	if row.MenuState == nil || cleanText(*row.MenuState) == "" {
		return "unknown"
	}
	return cleanText(*row.MenuState)
}

func menuLabelText(labels []app.SalesNavCaptureMenuLabel) []string {
	result := []string{}
	for _, label := range labels {
		if label.Text != nil && cleanText(*label.Text) != "" {
			result = append(result, *label.Text)
		}
		if label.Aria != nil && cleanText(*label.Aria) != "" {
			result = append(result, *label.Aria)
		}
	}
	return result
}

func containsAny(value string, needles ...string) bool {
	for _, needle := range needles {
		if strings.Contains(value, needle) {
			return true
		}
	}
	return false
}

func pointerValue(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

func optionalClean(value string) *string {
	cleaned := cleanText(value)
	if cleaned == "" {
		return nil
	}
	return &cleaned
}
