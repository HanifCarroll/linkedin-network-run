package outreach

import (
	"context"
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/mail"
	"net/url"
	"os"
	"sort"
	"strings"
	"time"

	"golang.org/x/net/html"
)

type AgencySourceCapture struct {
	SchemaVersion int               `json:"schema_version"`
	Source        string            `json:"source"`
	SourceType    string            `json:"source_type"`
	CapturedAt    *string           `json:"captured_at"`
	URL           *string           `json:"url"`
	Rows          []AgencySourceRow `json:"rows"`
}

type AgencySourceRow struct {
	Name          string                   `json:"name"`
	Website       *string                  `json:"website,omitempty"`
	AccountURL    *string                  `json:"account_url,omitempty"`
	LinkedInURL   *string                  `json:"linkedin_url,omitempty"`
	SourceURL     *string                  `json:"source_url,omitempty"`
	Services      []string                 `json:"services,omitempty"`
	Specialties   []string                 `json:"specialties,omitempty"`
	Industry      *string                  `json:"industry,omitempty"`
	Headcount     *string                  `json:"headcount,omitempty"`
	Location      *string                  `json:"location,omitempty"`
	Description   *string                  `json:"description,omitempty"`
	FitScore      *int                     `json:"fit_score,omitempty"`
	Status        *AgencyAccountStatus     `json:"status,omitempty"`
	FitReasons    []string                 `json:"fit_reasons,omitempty"`
	RejectReasons []string                 `json:"reject_reasons,omitempty"`
	Evidence      []string                 `json:"evidence,omitempty"`
	Contacts      []AgencySourceContactRow `json:"contacts,omitempty"`
}

type AgencySourceContactRow struct {
	Name       *string                       `json:"name,omitempty"`
	Title      *string                       `json:"title,omitempty"`
	Email      *string                       `json:"email,omitempty"`
	ProfileURL *string                       `json:"profile_url,omitempty"`
	ContactURL *string                       `json:"contact_url,omitempty"`
	FormAction *string                       `json:"form_action,omitempty"`
	Status     *AgencyContactCandidateStatus `json:"status,omitempty"`
	Evidence   []string                      `json:"evidence,omitempty"`
}

type AgencySourceImportSummary struct {
	Source                   string `json:"source"`
	Stored                   int    `json:"stored"`
	Updated                  int    `json:"updated"`
	Qualified                int    `json:"qualified"`
	NeedsReview              int    `json:"needs_review"`
	Rejected                 int    `json:"rejected"`
	ContactCandidatesStored  int    `json:"contact_candidates_stored"`
	ContactCandidatesUpdated int    `json:"contact_candidates_updated"`
	TotalAccounts            int    `json:"total_accounts"`
}

type AgencyWebsiteEnrichmentOptions struct {
	Limit     int
	TimeoutMS int
	Now       time.Time
	Client    *http.Client
}

type AgencyWebsiteEnrichmentSummary struct {
	Checked                  int `json:"checked"`
	Skipped                  int `json:"skipped"`
	ContactCandidatesStored  int `json:"contact_candidates_stored"`
	ContactCandidatesUpdated int `json:"contact_candidates_updated"`
	Errors                   int `json:"errors"`
}

func LoadAgencySourceCapture(path string) (AgencySourceCapture, error) {
	var capture AgencySourceCapture
	raw, err := os.ReadFile(path)
	if err != nil {
		return AgencySourceCapture{}, fmt.Errorf("reading agency source capture %s: %w", path, err)
	}
	if err := json.Unmarshal(raw, &capture); err != nil {
		return AgencySourceCapture{}, fmt.Errorf("parsing agency source capture %s: %w", path, err)
	}
	if capture.Rows == nil {
		capture.Rows = []AgencySourceRow{}
	}
	return capture, nil
}

func ImportAgencySourceCapture(state *OutreachState, capture AgencySourceCapture) (AgencySourceImportSummary, error) {
	state.Normalize()
	source := cleanText(capture.Source)
	if source == "" {
		return AgencySourceImportSummary{}, fmt.Errorf("agency source capture did not include source")
	}
	now := time.Now()
	summary := AgencySourceImportSummary{Source: source}
	for _, row := range capture.Rows {
		account, ok, err := buildAgencyAccountFromSourceRow(source, capture.SourceType, capture.CapturedAt, row, now)
		if err != nil {
			return AgencySourceImportSummary{}, err
		}
		if !ok {
			continue
		}
		index := findAgencyAccountIndex(state.AgencyAccounts, account)
		if index >= 0 {
			account.ID = state.AgencyAccounts[index].ID
			account.ImportedAt = state.AgencyAccounts[index].ImportedAt
			preserveAgencyAccountRuntimeFields(&account, state.AgencyAccounts[index])
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
		for _, contact := range row.Contacts {
			candidate, ok, err := buildAgencyContactCandidateFromSourceRow(source, account, row.SourceURL, contact, now)
			if err != nil {
				return AgencySourceImportSummary{}, err
			}
			if !ok {
				continue
			}
			if upsertAgencyContactCandidate(state, candidate) {
				summary.ContactCandidatesUpdated++
			} else {
				summary.ContactCandidatesStored++
			}
		}
	}
	sortAgencyAccounts(state.AgencyAccounts)
	sortAgencyContactCandidates(state.AgencyContactCandidates)
	summary.TotalAccounts = len(state.AgencyAccounts)
	return summary, nil
}

func preserveAgencyAccountRuntimeFields(account *AgencyAccount, existing AgencyAccount) {
	account.LastContactCaptureAt = existing.LastContactCaptureAt
	account.ContactCaptureCount = existing.ContactCaptureCount
	account.LastContactStrategy = existing.LastContactStrategy
	account.LastContactError = existing.LastContactError
	account.LastContactErrorAt = existing.LastContactErrorAt
	account.ContactErrorCount = existing.ContactErrorCount
	account.LastWebsiteEnrichedAt = existing.LastWebsiteEnrichedAt
	account.WebsiteEnrichmentCount = existing.WebsiteEnrichmentCount
	account.LastWebsiteEnrichmentError = existing.LastWebsiteEnrichmentError
	account.LastWebsiteEnrichmentErrorAt = existing.LastWebsiteEnrichmentErrorAt
	if len(existing.Notes) > 0 {
		account.Notes = existing.Notes
	}
}

func buildAgencyAccountFromSourceRow(source string, sourceType string, capturedAt *string, row AgencySourceRow, importedAt time.Time) (AgencyAccount, bool, error) {
	name := cleanText(row.Name)
	if name == "" {
		return AgencyAccount{}, false, nil
	}
	accountURL := normalizedLinkedInAccountURL(firstPresentString(row.AccountURL, row.LinkedInURL))
	website := optionalClean(pointerValue(row.Website))
	domain := domainFromWebsite(website)
	status, fitScore, fitReasons, rejectReasons, err := agencySourceRowDisposition(sourceType, row)
	if err != nil {
		return AgencyAccount{}, false, err
	}
	evidence := truncateEvidence(agencySourceRowEvidence(row))
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
		FitScore:      fitScore,
		FitReasons:    fitReasons,
		RejectReasons: rejectReasons,
		EvidenceText:  evidence,
		CapturedAt:    capturedAt,
		ImportedAt:    importedAt,
		UpdatedAt:     importedAt,
		Notes:         []string{},
	}, true, nil
}

func agencySourceRowDisposition(sourceType string, row AgencySourceRow) (AgencyAccountStatus, int, []string, []string, error) {
	status := AgencyAccountStatusNeedsReview
	if row.Status != nil {
		switch *row.Status {
		case AgencyAccountStatusQualified, AgencyAccountStatusNeedsReview, AgencyAccountStatusRejected, AgencyAccountStatusExhausted:
			status = *row.Status
		default:
			return "", 0, nil, nil, fmt.Errorf("invalid agency account status %q for %q", *row.Status, row.Name)
		}
	}
	fitScore := 50
	if row.FitScore != nil {
		fitScore = *row.FitScore
	}
	fitReasons := append([]string{}, row.FitReasons...)
	rejectReasons := append([]string{}, row.RejectReasons...)
	if len(fitReasons) == 0 {
		fitReasons = append(fitReasons, "imported from structured agency source")
	}
	if isPartnerSourceType(sourceType) && row.FitScore == nil {
		fitScore += 15
		fitReasons = append(fitReasons, "partner directory source")
	}
	for _, label := range append(row.Services, row.Specialties...) {
		if targetAgencyServiceTag(label) && row.FitScore == nil {
			fitScore += 15
			fitReasons = append(fitReasons, "structured service tag: "+cleanText(label))
		}
		if marketingServiceTag(label) {
			fitReasons = append(fitReasons, "marketing service profile; review for dev/product support gap")
		}
	}
	if fitScore > 100 {
		fitScore = 100
	}
	if fitScore < 0 {
		fitScore = 0
	}
	if row.Status == nil {
		if fitScore >= 65 {
			status = AgencyAccountStatusQualified
		} else {
			status = AgencyAccountStatusNeedsReview
		}
	}
	return status, fitScore, dedupeStrings(fitReasons), dedupeStrings(rejectReasons), nil
}

func isPartnerSourceType(value string) bool {
	switch strings.ToLower(cleanText(value)) {
	case "shopify_partner", "webflow_partner", "hubspot_partner", "google_partner":
		return true
	default:
		return false
	}
}

func targetAgencyServiceTag(value string) bool {
	tag := normalizedServiceTag(value)
	targets := map[string]bool{
		"application development":       true,
		"custom api integrations":       true,
		"crm implementation":            true,
		"data migration":                true,
		"ecommerce development":         true,
		"mobile app development":        true,
		"product design":                true,
		"shopify development":           true,
		"software development":          true,
		"solutions architecture design": true,
		"web development":               true,
		"web design":                    true,
		"website design":                true,
		"website development":           true,
		"webflow development":           true,
		"wordpress development":         true,
	}
	return targets[tag]
}

func marketingServiceTag(value string) bool {
	tag := normalizedServiceTag(value)
	targets := map[string]bool{
		"account based marketing":         true,
		"content creation":                true,
		"digital marketing":               true,
		"email marketing":                 true,
		"full inbound marketing services": true,
		"paid advertising":                true,
		"seo":                             true,
		"social media marketing":          true,
	}
	return targets[tag]
}

func normalizedServiceTag(value string) string {
	cleaned := strings.ToLower(cleanText(value))
	cleaned = strings.NewReplacer("&", " and ", "/", " ", "-", " ").Replace(cleaned)
	return cleanText(cleaned)
}

func agencySourceRowEvidence(row AgencySourceRow) string {
	parts := []string{}
	if row.SourceURL != nil && cleanText(*row.SourceURL) != "" {
		parts = append(parts, "source_url: "+cleanText(*row.SourceURL))
	}
	if len(row.Services) > 0 {
		parts = append(parts, "services: "+strings.Join(cleanStringItems(row.Services), "; "))
	}
	if len(row.Specialties) > 0 {
		parts = append(parts, "specialties: "+strings.Join(cleanStringItems(row.Specialties), "; "))
	}
	for _, value := range []*string{row.Description, row.Industry, row.Headcount, row.Location, row.Website} {
		if value != nil && cleanText(*value) != "" {
			parts = append(parts, cleanText(*value))
		}
	}
	parts = append(parts, cleanStringItems(row.Evidence)...)
	return strings.Join(parts, "\n")
}

func buildAgencyContactCandidateFromSourceRow(source string, account AgencyAccount, sourceURL *string, row AgencySourceContactRow, importedAt time.Time) (AgencyContactCandidate, bool, error) {
	email := normalizedEmail(row.Email)
	profileURL := normalizedProfileURL(row.ProfileURL)
	contactURL := optionalClean(pointerValue(row.ContactURL))
	formAction := optionalClean(pointerValue(row.FormAction))
	if email == nil && profileURL == nil && contactURL == nil && formAction == nil {
		return AgencyContactCandidate{}, false, nil
	}
	status := AgencyContactCandidateStatusWebsiteContactCandidate
	if row.Status != nil {
		if !validAgencyContactCandidateStatus(*row.Status) {
			return AgencyContactCandidate{}, false, fmt.Errorf("invalid agency contact candidate status %q for %q", *row.Status, account.Name)
		}
		status = *row.Status
	} else if email != nil && isGenericInbox(*email) {
		status = AgencyContactCandidateStatusGenericInbox
	} else if formAction != nil || contactURL != nil {
		status = AgencyContactCandidateStatusContactForm
	}
	candidate := AgencyContactCandidate{
		AgencyAccountID:   account.ID,
		AgencyAccountName: account.Name,
		Source:            source,
		SourceURL:         optionalClean(pointerValue(sourceURL)),
		Status:            status,
		ReviewStatus:      AgencyContactReviewStatusNeedsReview,
		Name:              optionalClean(pointerValue(row.Name)),
		Title:             optionalClean(pointerValue(row.Title)),
		Email:             email,
		ProfileURL:        profileURL,
		ContactURL:        contactURL,
		FormAction:        formAction,
		Evidence:          append([]string{}, row.Evidence...),
		ImportedAt:        importedAt,
		UpdatedAt:         importedAt,
		Notes:             []string{},
	}
	candidate.ID = stableAgencyContactCandidateID(candidate)
	return candidate, true, nil
}

func EnrichAgencyWebsites(ctx context.Context, state *OutreachState, options AgencyWebsiteEnrichmentOptions) AgencyWebsiteEnrichmentSummary {
	state.Normalize()
	now := options.Now
	if now.IsZero() {
		now = time.Now()
	}
	client := options.Client
	if client == nil {
		timeout := 10 * time.Second
		if options.TimeoutMS > 0 {
			timeout = time.Duration(options.TimeoutMS) * time.Millisecond
		}
		client = &http.Client{Timeout: timeout}
	}
	summary := AgencyWebsiteEnrichmentSummary{}
	for index := range state.AgencyAccounts {
		if options.Limit > 0 && summary.Checked >= options.Limit {
			break
		}
		account := &state.AgencyAccounts[index]
		if !agencyAccountWebsiteEnrichmentEligible(*account) {
			summary.Skipped++
			continue
		}
		if account.Website == nil || cleanText(*account.Website) == "" {
			summary.Skipped++
			continue
		}
		candidates, err := DiscoverAgencyWebsiteContacts(ctx, client, *account)
		account.LastWebsiteEnrichedAt = &now
		account.WebsiteEnrichmentCount++
		account.UpdatedAt = now
		summary.Checked++
		if err != nil {
			message := err.Error()
			account.LastWebsiteEnrichmentError = &message
			account.LastWebsiteEnrichmentErrorAt = &now
			summary.Errors++
			continue
		}
		account.LastWebsiteEnrichmentError = nil
		account.LastWebsiteEnrichmentErrorAt = nil
		for _, candidate := range candidates {
			if candidate.ImportedAt.IsZero() {
				candidate.ImportedAt = now
			}
			candidate.UpdatedAt = now
			if upsertAgencyContactCandidate(state, candidate) {
				summary.ContactCandidatesUpdated++
			} else {
				summary.ContactCandidatesStored++
			}
		}
	}
	sortAgencyContactCandidates(state.AgencyContactCandidates)
	return summary
}

func DiscoverAgencyWebsiteContacts(ctx context.Context, client *http.Client, account AgencyAccount) ([]AgencyContactCandidate, error) {
	baseURL, err := normalizedWebsiteURL(account.Website)
	if err != nil {
		return nil, err
	}
	candidates := []AgencyContactCandidate{}
	seen := map[string]bool{}
	failures := []string{}
	successes := 0
	for _, pageURL := range agencyWebsiteContactPages(baseURL) {
		found, err := discoverAgencyWebsiteContactsOnPage(ctx, client, account, pageURL)
		if err != nil {
			failures = append(failures, err.Error())
			continue
		}
		successes++
		for _, candidate := range found {
			key := agencyContactCandidateKey(candidate)
			if seen[key] {
				continue
			}
			seen[key] = true
			candidates = append(candidates, candidate)
		}
	}
	if successes == 0 && len(failures) > 0 {
		if len(failures) > 3 {
			failures = append(failures[:3], fmt.Sprintf("%d more page failures", len(failures)-3))
		}
		return candidates, fmt.Errorf("website pages failed: %s", strings.Join(failures, "; "))
	}
	return candidates, nil
}

func discoverAgencyWebsiteContactsOnPage(ctx context.Context, client *http.Client, account AgencyAccount, pageURL string) ([]AgencyContactCandidate, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, pageURL, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "recruiter-agency-outreach/1.0")
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("GET %s returned %d", pageURL, resp.StatusCode)
	}
	node, err := html.Parse(io.LimitReader(resp.Body, 1_000_000))
	if err != nil {
		return nil, fmt.Errorf("parsing %s: %w", pageURL, err)
	}
	now := time.Now()
	candidates := []AgencyContactCandidate{}
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if n.Type == html.ElementNode {
			switch strings.ToLower(n.Data) {
			case "a":
				if candidate, ok := candidateFromAnchor(account, pageURL, n, now); ok {
					candidates = append(candidates, candidate)
				}
			case "form":
				if candidate, ok := candidateFromForm(account, pageURL, n, now); ok {
					candidates = append(candidates, candidate)
				}
			}
		}
		for child := n.FirstChild; child != nil; child = child.NextSibling {
			walk(child)
		}
	}
	walk(node)
	return candidates, nil
}

func candidateFromAnchor(account AgencyAccount, pageURL string, node *html.Node, importedAt time.Time) (AgencyContactCandidate, bool) {
	href := attrValue(node, "href")
	if href == "" {
		return AgencyContactCandidate{}, false
	}
	resolved := resolveURL(pageURL, href)
	if strings.HasPrefix(strings.ToLower(href), "mailto:") {
		email := emailFromMailto(href)
		if email == nil {
			return AgencyContactCandidate{}, false
		}
		status := AgencyContactCandidateStatusWebsiteContactCandidate
		if isGenericInbox(*email) {
			status = AgencyContactCandidateStatusGenericInbox
		}
		candidate := AgencyContactCandidate{
			AgencyAccountID:   account.ID,
			AgencyAccountName: account.Name,
			Source:            "website_enrichment",
			SourceURL:         &pageURL,
			Status:            status,
			ReviewStatus:      AgencyContactReviewStatusNeedsReview,
			Name:              optionalClean(anchorText(node)),
			Email:             email,
			Evidence:          []string{"explicit mailto link on " + pageURL},
			ImportedAt:        importedAt,
			UpdatedAt:         importedAt,
			Notes:             []string{},
		}
		candidate.ID = stableAgencyContactCandidateID(candidate)
		return candidate, true
	}
	if profileURL := normalizedLinkedInProfileURL(resolved); profileURL != nil {
		candidate := AgencyContactCandidate{
			AgencyAccountID:   account.ID,
			AgencyAccountName: account.Name,
			Source:            "website_enrichment",
			SourceURL:         &pageURL,
			Status:            AgencyContactCandidateStatusWebsiteContactCandidate,
			ReviewStatus:      AgencyContactReviewStatusNeedsReview,
			Name:              optionalClean(anchorText(node)),
			ProfileURL:        profileURL,
			Evidence:          []string{"explicit LinkedIn profile link on " + pageURL},
			ImportedAt:        importedAt,
			UpdatedAt:         importedAt,
			Notes:             []string{},
		}
		candidate.ID = stableAgencyContactCandidateID(candidate)
		return candidate, true
	}
	return AgencyContactCandidate{}, false
}

func candidateFromForm(account AgencyAccount, pageURL string, node *html.Node, importedAt time.Time) (AgencyContactCandidate, bool) {
	action := resolveURL(pageURL, attrValue(node, "action"))
	if action == "" {
		action = pageURL
	}
	if !contactFormSourceAllowed(pageURL, action) {
		return AgencyContactCandidate{}, false
	}
	contactURL := pageURL
	candidate := AgencyContactCandidate{
		AgencyAccountID:   account.ID,
		AgencyAccountName: account.Name,
		Source:            "website_enrichment",
		SourceURL:         &pageURL,
		Status:            AgencyContactCandidateStatusContactForm,
		ReviewStatus:      AgencyContactReviewStatusNeedsReview,
		ContactURL:        &contactURL,
		FormAction:        &action,
		Evidence:          []string{"explicit contact form on " + pageURL},
		ImportedAt:        importedAt,
		UpdatedAt:         importedAt,
		Notes:             []string{},
	}
	candidate.ID = stableAgencyContactCandidateID(candidate)
	return candidate, true
}

func contactFormSourceAllowed(pageURL string, action string) bool {
	return contactPathSignal(pageURL) || contactPathSignal(action)
}

func contactPathSignal(raw string) bool {
	parsed, err := url.Parse(raw)
	if err != nil {
		return false
	}
	value := strings.ToLower(strings.Trim(parsed.Path, "/"))
	if value == "" {
		return false
	}
	compact := strings.NewReplacer("-", "", "_", "", "/", "").Replace(value)
	switch compact {
	case "contact", "contactus", "contacts", "getintouch", "inquiry", "inquiries", "enquiry", "enquiries", "quote", "requestquote", "project", "startaproject", "estimate":
		return true
	}
	for _, token := range strings.FieldsFunc(value, func(r rune) bool {
		return r == '/' || r == '-' || r == '_'
	}) {
		switch token {
		case "contact", "contacts", "inquiry", "inquiries", "enquiry", "enquiries", "quote", "project", "estimate":
			return true
		}
	}
	return false
}

func agencyWebsiteContactPages(baseURL string) []string {
	parsed, err := url.Parse(baseURL)
	if err != nil {
		return []string{baseURL}
	}
	paths := []string{"/", "/about", "/team", "/contact", "/partners", "/services"}
	pages := []string{}
	seen := map[string]bool{}
	for _, path := range paths {
		next := *parsed
		next.Path = path
		next.RawQuery = ""
		next.Fragment = ""
		value := next.String()
		if seen[value] {
			continue
		}
		seen[value] = true
		pages = append(pages, value)
	}
	return pages
}

func upsertAgencyContactCandidate(state *OutreachState, candidate AgencyContactCandidate) bool {
	state.Normalize()
	candidate.Normalize()
	if candidate.ID == "" {
		candidate.ID = stableAgencyContactCandidateID(candidate)
	}
	for index := range state.AgencyContactCandidates {
		if agencyContactCandidateKey(state.AgencyContactCandidates[index]) == agencyContactCandidateKey(candidate) {
			candidate.ID = state.AgencyContactCandidates[index].ID
			candidate.ImportedAt = state.AgencyContactCandidates[index].ImportedAt
			candidate.ReviewStatus = state.AgencyContactCandidates[index].ReviewStatus
			if len(state.AgencyContactCandidates[index].Notes) > 0 {
				candidate.Notes = state.AgencyContactCandidates[index].Notes
			}
			state.AgencyContactCandidates[index] = candidate
			return true
		}
	}
	state.AgencyContactCandidates = append(state.AgencyContactCandidates, candidate)
	return false
}

func agencyContactCandidateKey(candidate AgencyContactCandidate) string {
	parts := []string{candidate.AgencyAccountID, string(candidate.Status)}
	for _, value := range []*string{candidate.Email, candidate.ProfileURL, candidate.ContactURL, candidate.FormAction} {
		if value != nil && cleanText(*value) != "" {
			parts = append(parts, strings.ToLower(cleanText(*value)))
		}
	}
	if len(parts) <= 2 && candidate.SourceURL != nil {
		parts = append(parts, strings.ToLower(cleanText(*candidate.SourceURL)))
	}
	return strings.Join(parts, "|")
}

func stableAgencyContactCandidateID(candidate AgencyContactCandidate) string {
	hash := sha1.Sum([]byte(agencyContactCandidateKey(candidate)))
	return "agc_" + hex.EncodeToString(hash[:])[:12]
}

func sortAgencyContactCandidates(candidates []AgencyContactCandidate) {
	sort.SliceStable(candidates, func(i, j int) bool {
		if candidates[i].ReviewStatus != candidates[j].ReviewStatus {
			return candidates[i].ReviewStatus < candidates[j].ReviewStatus
		}
		if candidates[i].Status != candidates[j].Status {
			return candidates[i].Status < candidates[j].Status
		}
		if candidates[i].AgencyAccountName != candidates[j].AgencyAccountName {
			return candidates[i].AgencyAccountName < candidates[j].AgencyAccountName
		}
		return candidates[i].ID < candidates[j].ID
	})
}

func normalizedWebsiteURL(value *string) (string, error) {
	if value == nil || cleanText(*value) == "" {
		return "", fmt.Errorf("website is empty")
	}
	raw := cleanText(*value)
	if !strings.Contains(raw, "://") {
		raw = "https://" + raw
	}
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Hostname() == "" {
		return "", fmt.Errorf("invalid website %q", raw)
	}
	parsed.Fragment = ""
	return parsed.String(), nil
}

func resolveURL(base string, href string) string {
	cleaned := cleanText(href)
	if cleaned == "" {
		return ""
	}
	parsedBase, err := url.Parse(base)
	if err != nil {
		return cleaned
	}
	parsedHref, err := url.Parse(cleaned)
	if err != nil {
		return cleaned
	}
	return parsedBase.ResolveReference(parsedHref).String()
}

func attrValue(node *html.Node, key string) string {
	for _, attr := range node.Attr {
		if strings.EqualFold(attr.Key, key) {
			return cleanText(attr.Val)
		}
	}
	return ""
}

func anchorText(node *html.Node) string {
	parts := []string{}
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if n.Type == html.TextNode {
			parts = append(parts, n.Data)
		}
		for child := n.FirstChild; child != nil; child = child.NextSibling {
			walk(child)
		}
	}
	walk(node)
	return cleanText(strings.Join(parts, " "))
}

func emailFromMailto(value string) *string {
	raw := strings.TrimPrefix(value, "mailto:")
	if index := strings.Index(raw, "?"); index >= 0 {
		raw = raw[:index]
	}
	return normalizedEmail(&raw)
}

func normalizedEmail(value *string) *string {
	if value == nil || cleanText(*value) == "" {
		return nil
	}
	cleaned := strings.ToLower(cleanText(*value))
	address, err := mail.ParseAddress(cleaned)
	if err != nil {
		if strings.Contains(cleaned, "@") {
			return &cleaned
		}
		return nil
	}
	normalized := strings.ToLower(address.Address)
	return &normalized
}

func isGenericInbox(email string) bool {
	local, _, ok := strings.Cut(strings.ToLower(cleanText(email)), "@")
	if !ok {
		return false
	}
	generic := map[string]bool{
		"business":     true,
		"contact":      true,
		"hello":        true,
		"hi":           true,
		"info":         true,
		"inquiries":    true,
		"partnerships": true,
		"partners":     true,
		"sales":        true,
		"support":      true,
		"team":         true,
	}
	return generic[local]
}

func validAgencyContactCandidateStatus(status AgencyContactCandidateStatus) bool {
	switch status {
	case AgencyContactCandidateStatusWebsiteContactCandidate,
		AgencyContactCandidateStatusGenericInbox,
		AgencyContactCandidateStatusContactForm,
		AgencyContactCandidateStatusRejected,
		AgencyContactCandidateStatusConverted:
		return true
	default:
		return false
	}
}

func normalizedProfileURL(value *string) *string {
	if value == nil || cleanText(*value) == "" {
		return nil
	}
	return normalizedLinkedInProfileURL(cleanText(*value))
}

func normalizedLinkedInProfileURL(value string) *string {
	cleaned := cleanText(value)
	if cleaned == "" {
		return nil
	}
	parsed, err := url.Parse(cleaned)
	if err != nil || parsed.Hostname() == "" {
		return nil
	}
	host := strings.TrimPrefix(strings.ToLower(parsed.Hostname()), "www.")
	if host != "linkedin.com" || !strings.HasPrefix(parsed.Path, "/in/") {
		return nil
	}
	parsed.RawQuery = ""
	parsed.Fragment = ""
	normalized := parsed.String()
	return &normalized
}

func firstPresentString(values ...*string) *string {
	for _, value := range values {
		if value != nil && cleanText(*value) != "" {
			cleaned := cleanText(*value)
			return &cleaned
		}
	}
	return nil
}

func cleanStringItems(values []string) []string {
	items := []string{}
	for _, value := range values {
		cleaned := cleanText(value)
		if cleaned == "" {
			continue
		}
		items = append(items, cleaned)
	}
	return items
}

func dedupeStrings(values []string) []string {
	seen := map[string]bool{}
	items := []string{}
	for _, value := range values {
		cleaned := cleanText(value)
		if cleaned == "" || seen[strings.ToLower(cleaned)] {
			continue
		}
		seen[strings.ToLower(cleaned)] = true
		items = append(items, cleaned)
	}
	return items
}
