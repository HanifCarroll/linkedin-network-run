package outreach

import (
	"encoding/json"
	"fmt"
	"hash/fnv"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/hanifcarroll/linkedin-network-run/internal/app"
)

type SalesNavAccountCapture struct {
	SchemaVersion  int                          `json:"schemaVersion"`
	CapturedAt     *string                      `json:"capturedAt"`
	Source         *string                      `json:"source"`
	URL            *string                      `json:"url"`
	ResumeURL      *string                      `json:"resumeUrl"`
	Page           *SalesNavAccountCapturePage  `json:"page"`
	Pages          []SalesNavAccountCapturePage `json:"pages"`
	RawRowCount    *uint32                      `json:"rawRowCount"`
	OutputRowCount *uint32                      `json:"outputRowCount"`
	Rows           []SalesNavAccountCaptureRow  `json:"rows"`
}

type SalesNavAccountCapturePage struct {
	URL         *string `json:"url"`
	PageLabel   *string `json:"pageLabel"`
	ResultCount *string `json:"resultCount"`
}

type SalesNavAccountCaptureRow struct {
	Index       uint32                    `json:"index"`
	Name        *string                   `json:"name"`
	Text        *string                   `json:"text"`
	AccountURL  *string                   `json:"accountUrl"`
	AccountID   *string                   `json:"accountId"`
	Website     *string                   `json:"website"`
	Industry    *string                   `json:"industry"`
	Headcount   *string                   `json:"headcount"`
	Location    *string                   `json:"location"`
	Links       []app.SalesNavCaptureLink `json:"links"`
	RowHTMLPath *string                   `json:"rowHtmlPath"`
}

type AccountCaptureRunOptions struct {
	Pages            uint32
	Limit            uint32
	RowScrollDelayMS uint32
	SaveHTML         bool
	TimeoutMS        uint32
}

type AccountImportSummary struct {
	Source      string `json:"source"`
	Stored      int    `json:"stored"`
	Updated     int    `json:"updated"`
	Qualified   int    `json:"qualified"`
	NeedsReview int    `json:"needs_review"`
	Rejected    int    `json:"rejected"`
	Total       int    `json:"total"`
}

func RunPlaywriterAccountCapture(playwriter string, session string, script string, outDir string, source string, captureURL string, options AccountCaptureRunOptions) (string, error) {
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return "", fmt.Errorf("creating %s: %w", outDir, err)
	}
	outJSON, err := json.Marshal(outDir)
	if err != nil {
		return "", err
	}
	sourceJSON, err := json.Marshal(source)
	if err != nil {
		return "", err
	}
	urlJSON, err := json.Marshal(captureURL)
	if err != nil {
		return "", err
	}
	if options.TimeoutMS == 0 {
		options.TimeoutMS = 90000
	}
	configJS := fmt.Sprintf(
		"state.salesNavAccountCaptureConfig = { out: %s, source: %s, url: %s, limit: %d, pages: %d, rowScrollDelayMs: %d, saveHtml: %t }; console.log(JSON.stringify(state.salesNavAccountCaptureConfig));",
		string(outJSON),
		string(sourceJSON),
		string(urlJSON),
		options.Limit,
		options.Pages,
		options.RowScrollDelayMS,
		options.SaveHTML,
	)
	if err := app.RunPlaywriterConfig(playwriter, session, configJS); err != nil {
		return "", err
	}
	if err := app.RunPlaywriterFileWithTimeout(playwriter, session, script, options.TimeoutMS); err != nil {
		return "", err
	}
	return filepath.Join(outDir, "page.json"), nil
}

func LoadSalesNavAccountCapture(path string) (SalesNavAccountCapture, error) {
	var capture SalesNavAccountCapture
	raw, err := os.ReadFile(path)
	if err != nil {
		return SalesNavAccountCapture{}, fmt.Errorf("reading account capture %s: %w", path, err)
	}
	if err := json.Unmarshal(raw, &capture); err != nil {
		return SalesNavAccountCapture{}, fmt.Errorf("parsing account capture %s: %w", path, err)
	}
	if capture.Pages == nil {
		capture.Pages = []SalesNavAccountCapturePage{}
	}
	if capture.Rows == nil {
		capture.Rows = []SalesNavAccountCaptureRow{}
	}
	return capture, nil
}

func ImportAccountCapture(state *OutreachState, capture SalesNavAccountCapture) (AccountImportSummary, error) {
	state.Normalize()
	source := cleanText(pointerValue(capture.Source))
	if source == "" {
		return AccountImportSummary{}, fmt.Errorf("account capture did not include source")
	}
	now := time.Now()
	summary := AccountImportSummary{Source: source}
	state.CaptureCursors[source] = captureCursorFromAccountCapture(source, capture, now)
	for _, row := range capture.Rows {
		if row.Name == nil || cleanText(*row.Name) == "" {
			continue
		}
		account := buildAgencyAccount(source, row, capture.CapturedAt, now)
		index := findAgencyAccountIndex(state.AgencyAccounts, account)
		if index >= 0 {
			account.ID = state.AgencyAccounts[index].ID
			account.ImportedAt = state.AgencyAccounts[index].ImportedAt
			account.LastContactCaptureAt = state.AgencyAccounts[index].LastContactCaptureAt
			account.ContactCaptureCount = state.AgencyAccounts[index].ContactCaptureCount
			account.LastContactStrategy = state.AgencyAccounts[index].LastContactStrategy
			account.LastContactError = state.AgencyAccounts[index].LastContactError
			account.LastContactErrorAt = state.AgencyAccounts[index].LastContactErrorAt
			account.ContactErrorCount = state.AgencyAccounts[index].ContactErrorCount
			account.LastWebsiteEnrichedAt = state.AgencyAccounts[index].LastWebsiteEnrichedAt
			account.WebsiteEnrichmentCount = state.AgencyAccounts[index].WebsiteEnrichmentCount
			account.LastWebsiteEnrichmentError = state.AgencyAccounts[index].LastWebsiteEnrichmentError
			account.LastWebsiteEnrichmentErrorAt = state.AgencyAccounts[index].LastWebsiteEnrichmentErrorAt
			if len(state.AgencyAccounts[index].Notes) > 0 {
				account.Notes = state.AgencyAccounts[index].Notes
			}
			state.AgencyAccounts[index] = account
			summary.Updated++
		} else {
			state.AgencyAccounts = append(state.AgencyAccounts, account)
			summary.Stored++
		}
		switch account.Status {
		case AgencyAccountStatusQualified:
			summary.Qualified++
		case AgencyAccountStatusNeedsReview:
			summary.NeedsReview++
		case AgencyAccountStatusRejected:
			summary.Rejected++
		}
	}
	sortAgencyAccounts(state.AgencyAccounts)
	summary.Total = len(state.AgencyAccounts)
	return summary, nil
}

func buildAgencyAccount(source string, row SalesNavAccountCaptureRow, capturedAt *string, importedAt time.Time) AgencyAccount {
	name := cleanText(pointerValue(row.Name))
	accountURL := normalizedLinkedInAccountURL(row.AccountURL)
	website := optionalClean(pointerValue(row.Website))
	domain := domainFromWebsite(website)
	evidence := truncateEvidence(rawAccountEvidenceText(row))
	status, score, reasons, rejects := classifyAgencyAccount(source, name, row.Industry, evidence)
	return AgencyAccount{
		ID:            stableAgencyAccountID(source, name, accountURL, domain),
		Source:        source,
		Name:          name,
		AccountURL:    accountURL,
		Website:       website,
		Domain:        domain,
		Industry:      optionalClean(pointerValue(row.Industry)),
		Headcount:     optionalClean(pointerValue(row.Headcount)),
		Location:      optionalClean(pointerValue(row.Location)),
		Status:        status,
		FitScore:      score,
		FitReasons:    reasons,
		RejectReasons: rejects,
		EvidenceText:  evidence,
		CapturedAt:    capturedAt,
		ImportedAt:    importedAt,
		UpdatedAt:     importedAt,
		Notes:         []string{},
	}
}

func classifyAgencyAccount(source string, name string, industry *string, evidence string) (AgencyAccountStatus, int, []string, []string) {
	accountText := strings.ToLower(strings.Join([]string{name, pointerValue(industry), evidence}, " "))
	sourceText := strings.ToLower(source)
	score := 0
	reasons := []string{}
	rejects := []string{}

	if containsAny(accountText, "product studio", "digital product", "software development", "custom software", "web development", "mobile app", "application development", "product design", "ux design", "design services", "it services and it consulting") {
		score += 45
		reasons = append(reasons, "software/product delivery account signal")
	}
	if containsAny(accountText, "agency", "studio", "consultancy", "consulting", "development shop", "dev shop") {
		score += 20
		reasons = append(reasons, "agency/studio services signal")
	}
	if containsAny(sourceText, "product studio", "development agency", "digital agency", "software development") {
		score += 15
		reasons = append(reasons, "matched generated agency account source")
	}
	if containsAny(accountText, "react", "typescript", "node", "ai", "saas", "platform", "mvp", "startup") {
		score += 10
		reasons = append(reasons, "technical/product stack signal")
	}
	websiteBuildSignal := containsAny(accountText, "wordpress", "shopify", "webflow", "cms", "web design", "web designer", "web developer", "website design", "website development", "high-performing websites")
	if websiteBuildSignal {
		score += 35
		reasons = append(reasons, "website/wordpress build account signal")
	}
	marketingOnly := containsAny(accountText, "seo", "paid media", "media buying", "advertising", "social media marketing", "performance marketing", "lead generation", "public relations", "branding agency") &&
		!websiteBuildSignal &&
		!containsAny(accountText, "software", "product", "web development", "application", "mobile app", "ux", "ui")
	if marketingOnly {
		score -= 40
		rejects = append(rejects, "marketing/advertising-only account signal")
	}
	if containsAny(accountText, "staffing", "recruiting", "recruitment", "talent solutions") {
		score -= 20
		rejects = append(rejects, "staffing account belongs in recruiter lane")
	}
	if score > 100 {
		score = 100
	}
	if score < 0 {
		score = 0
	}
	if len(reasons) == 0 {
		reasons = append(reasons, "weak agency account evidence")
	}
	status := AgencyAccountStatusNeedsReview
	switch {
	case score >= 65:
		status = AgencyAccountStatusQualified
	case score < 45:
		status = AgencyAccountStatusRejected
	}
	return status, score, reasons, rejects
}

func captureCursorFromAccountCapture(source string, capture SalesNavAccountCapture, updatedAt time.Time) CaptureCursor {
	var lastPage *SalesNavAccountCapturePage
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
		Source:         source,
		UpdatedAt:      updatedAt,
		CapturedAt:     capture.CapturedAt,
		ResumeURL:      resumeURL,
		PageLabel:      pageLabel,
		CapturedPages:  capturedPages,
		RawRowCount:    rawRowCount,
		OutputRowCount: outputRowCount,
		StateCounts:    map[string]uint32{"accounts": outputRowCount},
	}
}

func agencyAccountsForContactCapture(state OutreachState, target int) []AgencyAccount {
	state.Normalize()
	accounts := []AgencyAccount{}
	for _, account := range state.AgencyAccounts {
		if account.Status != AgencyAccountStatusQualified {
			continue
		}
		accounts = append(accounts, account)
	}
	sort.SliceStable(accounts, func(i, j int) bool {
		iActive := accountHasActiveLead(state, accounts[i].ID)
		jActive := accountHasActiveLead(state, accounts[j].ID)
		if iActive != jActive {
			return !iActive
		}
		if accounts[i].ContactCaptureCount != accounts[j].ContactCaptureCount {
			return accounts[i].ContactCaptureCount < accounts[j].ContactCaptureCount
		}
		iLast := time.Time{}
		jLast := time.Time{}
		if accounts[i].LastContactCaptureAt != nil {
			iLast = *accounts[i].LastContactCaptureAt
		}
		if accounts[j].LastContactCaptureAt != nil {
			jLast = *accounts[j].LastContactCaptureAt
		}
		if !iLast.Equal(jLast) {
			return iLast.Before(jLast)
		}
		if accounts[i].FitScore != accounts[j].FitScore {
			return accounts[i].FitScore > accounts[j].FitScore
		}
		return accounts[i].Name < accounts[j].Name
	})
	if target > 0 && len(accounts) > target {
		return accounts[:target]
	}
	return accounts
}

func agencyAccountsNeedingContactCapture(state OutreachState, target int) []AgencyAccount {
	state.Normalize()
	accounts := []AgencyAccount{}
	for _, account := range agencyAccountsForContactCapture(state, 0) {
		if accountHasActiveLead(state, account.ID) {
			continue
		}
		if _, ok := nextAgencyContactSearchStrategy(account); !ok {
			continue
		}
		accounts = append(accounts, account)
		if target > 0 && len(accounts) >= target {
			return accounts
		}
	}
	return accounts
}

func accountHasActiveLead(state OutreachState, accountID string) bool {
	for _, lead := range state.Leads {
		if lead.AgencyAccountID == nil || *lead.AgencyAccountID != accountID {
			continue
		}
		if lead.Status == LeadStatusRejected {
			continue
		}
		if isTerminalMessageStatus(lead.MessageStatus) && lead.MessageStatus != MessageStatusDryRunReady {
			continue
		}
		return true
	}
	return false
}

func linkLeadToAgencyAccount(lead *Lead, account AgencyAccount) {
	lead.AgencyAccountID = &account.ID
	lead.AgencyAccountName = &account.Name
	lead.AgencyAccountURL = account.AccountURL
	lead.AgencyAccountReasons = append([]string{}, account.FitReasons...)
	lead.AgencyAccountEvidence = account.EvidenceText
	if lead.Company == nil || companyForDraft(lead.Company) == "" {
		lead.Company = &account.Name
	}
}

func findAgencyAccountIndex(accounts []AgencyAccount, candidate AgencyAccount) int {
	candidateKey := agencyAccountKey(candidate)
	for i, account := range accounts {
		if agencyAccountKey(account) == candidateKey {
			return i
		}
	}
	return -1
}

func agencyAccountKey(account AgencyAccount) string {
	if account.AccountURL != nil && cleanText(*account.AccountURL) != "" {
		return "url:" + app.NormalizeLinkedInURL(*account.AccountURL)
	}
	if account.Domain != nil && cleanText(*account.Domain) != "" {
		return "domain:" + strings.ToLower(cleanText(*account.Domain))
	}
	return "name:" + strings.ToLower(cleanText(account.Name))
}

func stableAgencyAccountID(source string, name string, accountURL *string, domain *string) string {
	key := strings.ToLower(source + "|" + name)
	if accountURL != nil && cleanText(*accountURL) != "" {
		key = app.NormalizeLinkedInURL(*accountURL)
	} else if domain != nil && cleanText(*domain) != "" {
		key = strings.ToLower(cleanText(*domain))
	}
	hash := fnv.New32a()
	_, _ = hash.Write([]byte(key))
	return fmt.Sprintf("acct_%08x", hash.Sum32())
}

func sortAgencyAccounts(accounts []AgencyAccount) {
	sort.SliceStable(accounts, func(i, j int) bool {
		if accounts[i].Status != accounts[j].Status {
			return accounts[i].Status < accounts[j].Status
		}
		if accounts[i].FitScore != accounts[j].FitScore {
			return accounts[i].FitScore > accounts[j].FitScore
		}
		return accounts[i].Name < accounts[j].Name
	})
}

func rawAccountEvidenceText(row SalesNavAccountCaptureRow) string {
	parts := []string{}
	if row.Text != nil {
		parts = append(parts, *row.Text)
	}
	for _, value := range []*string{row.Industry, row.Headcount, row.Location, row.Website} {
		if value != nil && cleanText(*value) != "" {
			parts = append(parts, *value)
		}
	}
	for _, link := range row.Links {
		if link.Text != nil {
			parts = append(parts, *link.Text)
		}
		if link.Aria != nil {
			parts = append(parts, *link.Aria)
		}
		if link.Href != nil && !strings.Contains(*link.Href, "linkedin.com") {
			parts = append(parts, *link.Href)
		}
	}
	return strings.Join(parts, "\n")
}

func normalizedLinkedInAccountURL(value *string) *string {
	if value == nil || cleanText(*value) == "" {
		return nil
	}
	raw := cleanText(*value)
	if strings.HasPrefix(raw, "/") {
		raw = "https://www.linkedin.com" + raw
	}
	normalized := app.NormalizeLinkedInURL(raw)
	return &normalized
}

func domainFromWebsite(website *string) *string {
	if website == nil || cleanText(*website) == "" {
		return nil
	}
	raw := cleanText(*website)
	if !strings.Contains(raw, "://") {
		raw = "https://" + raw
	}
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Hostname() == "" {
		return nil
	}
	domain := strings.TrimPrefix(strings.ToLower(parsed.Hostname()), "www.")
	if domain == "" || strings.Contains(domain, "linkedin.com") {
		return nil
	}
	return &domain
}
