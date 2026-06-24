package outreach

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const AgencySourceSchemaVersion = 1

type AgencySourceCSVOptions struct {
	Source     string
	SourceType string
	URL        string
	CapturedAt time.Time
}

type AgencySourceValidationWarning struct {
	Row     int    `json:"row,omitempty"`
	Field   string `json:"field,omitempty"`
	Message string `json:"message"`
}

type AgencySourceArtifactSummary struct {
	Path       string                          `json:"path,omitempty"`
	Source     string                          `json:"source"`
	SourceType string                          `json:"source_type"`
	Rows       int                             `json:"rows"`
	Warnings   []AgencySourceValidationWarning `json:"warnings,omitempty"`
}

func LoadAgencySourceCSV(path string, options AgencySourceCSVOptions) (AgencySourceCapture, error) {
	file, err := os.Open(path)
	if err != nil {
		return AgencySourceCapture{}, fmt.Errorf("opening agency source CSV %s: %w", path, err)
	}
	defer file.Close()
	reader := csv.NewReader(file)
	reader.TrimLeadingSpace = true
	records, err := reader.ReadAll()
	if err != nil {
		return AgencySourceCapture{}, fmt.Errorf("reading agency source CSV %s: %w", path, err)
	}
	if len(records) == 0 {
		return AgencySourceCapture{}, fmt.Errorf("agency source CSV %s is empty", path)
	}
	headers := map[string]int{}
	for index, header := range records[0] {
		cleaned := normalizeAgencySourceHeader(header)
		if cleaned != "" {
			headers[cleaned] = index
		}
	}
	source := cleanText(options.Source)
	if source == "" {
		return AgencySourceCapture{}, fmt.Errorf("--source is required")
	}
	capturedAt := ""
	if !options.CapturedAt.IsZero() {
		capturedAt = options.CapturedAt.Format(time.RFC3339)
	}
	capture := AgencySourceCapture{
		SchemaVersion: AgencySourceSchemaVersion,
		Source:        source,
		SourceType:    cleanText(options.SourceType),
		Rows:          []AgencySourceRow{},
	}
	if cleanText(options.URL) != "" {
		value := cleanText(options.URL)
		capture.URL = &value
	}
	if capturedAt != "" {
		capture.CapturedAt = &capturedAt
	}
	for rowIndex, record := range records[1:] {
		row := agencySourceRowFromCSVRecord(headers, record)
		if cleanText(row.Name) == "" {
			continue
		}
		if row.SourceURL == nil && capture.URL != nil {
			row.SourceURL = capture.URL
		}
		if contact := agencySourceContactFromCSVRecord(headers, record); contact != nil {
			row.Contacts = append(row.Contacts, *contact)
		}
		if row.SourceURL == nil {
			evidence := fmt.Sprintf("source CSV row %d in %s", rowIndex+2, filepath.Base(path))
			row.Evidence = append(row.Evidence, evidence)
		}
		capture.Rows = append(capture.Rows, row)
	}
	return capture, nil
}

func WriteAgencySourceCapture(path string, capture AgencySourceCapture) error {
	capture = NormalizeAgencySourceCapture(capture)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", filepath.Dir(path), err)
	}
	raw, err := json.MarshalIndent(capture, "", "  ")
	if err != nil {
		return fmt.Errorf("serializing agency source capture: %w", err)
	}
	raw = append(raw, '\n')
	if err := os.WriteFile(path, raw, 0o644); err != nil {
		return fmt.Errorf("writing agency source capture %s: %w", path, err)
	}
	return nil
}

func NormalizeAgencySourceCapture(capture AgencySourceCapture) AgencySourceCapture {
	if capture.SchemaVersion == 0 {
		capture.SchemaVersion = AgencySourceSchemaVersion
	}
	capture.Source = cleanText(capture.Source)
	capture.SourceType = cleanText(capture.SourceType)
	if capture.Rows == nil {
		capture.Rows = []AgencySourceRow{}
	}
	for index := range capture.Rows {
		capture.Rows[index].Name = cleanText(capture.Rows[index].Name)
		capture.Rows[index].Website = optionalClean(pointerValue(capture.Rows[index].Website))
		capture.Rows[index].AccountURL = optionalClean(pointerValue(capture.Rows[index].AccountURL))
		capture.Rows[index].LinkedInURL = optionalClean(pointerValue(capture.Rows[index].LinkedInURL))
		capture.Rows[index].SourceURL = optionalClean(pointerValue(capture.Rows[index].SourceURL))
		capture.Rows[index].Services = cleanStringItems(capture.Rows[index].Services)
		capture.Rows[index].Specialties = cleanStringItems(capture.Rows[index].Specialties)
		capture.Rows[index].Evidence = cleanStringItems(capture.Rows[index].Evidence)
		for contactIndex := range capture.Rows[index].Contacts {
			contact := &capture.Rows[index].Contacts[contactIndex]
			contact.Name = optionalClean(pointerValue(contact.Name))
			contact.Title = optionalClean(pointerValue(contact.Title))
			contact.Email = optionalClean(pointerValue(contact.Email))
			contact.ProfileURL = optionalClean(pointerValue(contact.ProfileURL))
			contact.ContactURL = optionalClean(pointerValue(contact.ContactURL))
			contact.FormAction = optionalClean(pointerValue(contact.FormAction))
			contact.Evidence = cleanStringItems(contact.Evidence)
		}
	}
	return capture
}

func ValidateAgencySourceCapture(capture AgencySourceCapture) []AgencySourceValidationWarning {
	capture = NormalizeAgencySourceCapture(capture)
	warnings := []AgencySourceValidationWarning{}
	if capture.SchemaVersion != AgencySourceSchemaVersion {
		warnings = append(warnings, AgencySourceValidationWarning{Field: "schema_version", Message: fmt.Sprintf("expected schema_version %d", AgencySourceSchemaVersion)})
	}
	if capture.Source == "" {
		warnings = append(warnings, AgencySourceValidationWarning{Field: "source", Message: "source is required"})
	}
	seen := map[string]int{}
	for index, row := range capture.Rows {
		rowNumber := index + 1
		if row.Name == "" {
			warnings = append(warnings, AgencySourceValidationWarning{Row: rowNumber, Field: "name", Message: "name is required"})
			continue
		}
		if row.Website == nil && row.AccountURL == nil && row.LinkedInURL == nil && row.SourceURL == nil {
			warnings = append(warnings, AgencySourceValidationWarning{Row: rowNumber, Field: "identity", Message: "provide at least one of website, account_url, linkedin_url, or source_url"})
		}
		key := agencySourceRowIdentityKey(row)
		if key != "" {
			if prior, ok := seen[key]; ok {
				warnings = append(warnings, AgencySourceValidationWarning{Row: rowNumber, Field: "identity", Message: fmt.Sprintf("duplicates row %d by %s", prior, key)})
			} else {
				seen[key] = rowNumber
			}
		}
	}
	return warnings
}

func SummarizeAgencySourceArtifact(path string, capture AgencySourceCapture) AgencySourceArtifactSummary {
	capture = NormalizeAgencySourceCapture(capture)
	return AgencySourceArtifactSummary{
		Path:       path,
		Source:     capture.Source,
		SourceType: capture.SourceType,
		Rows:       len(capture.Rows),
		Warnings:   ValidateAgencySourceCapture(capture),
	}
}

func AgencySourceContractMarkdown() string {
	return strings.TrimSpace(`## Agency Source Artifact Contract

The artifact is deterministic JSON with schema_version 1. It stores reviewed agency accounts and review-only contact candidates before any LinkedIn send path.

Required top-level fields:
- schema_version: 1
- source: human-readable source name, such as "Shopify partners - services"
- source_type: deterministic source kind, such as "shopify_partner", "webflow_partner", or "manual_directory"
- rows: agency account rows

Required row field:
- name

Recommended row identity fields:
- website: preferred for domain dedupe
- account_url or linkedin_url: LinkedIn company URL when known
- source_url: public directory/profile URL used as evidence

Fit fields:
- services and specialties are structured lists. Partner-directory source types and development/product/ecommerce service tags raise fit score deterministically.
- status and fit_score may be provided by a reviewed source artifact; otherwise the importer assigns qualified or needs_review from deterministic scoring.

Contact rows are review-only:
- profile_url must be a LinkedIn /in/ profile URL to be promotable later.
- email and contact forms stay agency_contact_candidates and never become sendable LinkedIn leads.
- contacts remain needs_review until agency-pool review-contact approves them.
`)
}

func agencySourceRowFromCSVRecord(headers map[string]int, record []string) AgencySourceRow {
	status := AgencyAccountStatus(csvValue(headers, record, "status"))
	fitScore := optionalInt(csvValue(headers, record, "fit_score"))
	row := AgencySourceRow{
		Name:          csvValue(headers, record, "name"),
		Website:       optionalClean(csvValue(headers, record, "website")),
		AccountURL:    optionalClean(csvValue(headers, record, "account_url")),
		LinkedInURL:   optionalClean(csvValue(headers, record, "linkedin_url")),
		SourceURL:     optionalClean(csvValue(headers, record, "source_url")),
		Services:      splitAgencySourceList(csvValue(headers, record, "services")),
		Specialties:   splitAgencySourceList(csvValue(headers, record, "specialties")),
		Industry:      optionalClean(csvValue(headers, record, "industry")),
		Headcount:     optionalClean(csvValue(headers, record, "headcount")),
		Location:      optionalClean(csvValue(headers, record, "location")),
		Description:   optionalClean(csvValue(headers, record, "description")),
		FitScore:      fitScore,
		FitReasons:    splitAgencySourceList(csvValue(headers, record, "fit_reasons")),
		RejectReasons: splitAgencySourceList(csvValue(headers, record, "reject_reasons")),
		Evidence:      splitAgencySourceList(csvValue(headers, record, "evidence")),
	}
	if cleanText(string(status)) != "" {
		row.Status = &status
	}
	return row
}

func agencySourceContactFromCSVRecord(headers map[string]int, record []string) *AgencySourceContactRow {
	status := AgencyContactCandidateStatus(csvValue(headers, record, "contact_status"))
	contact := AgencySourceContactRow{
		Name:       optionalClean(csvValue(headers, record, "contact_name")),
		Title:      optionalClean(csvValue(headers, record, "contact_title")),
		Email:      optionalClean(csvValue(headers, record, "contact_email")),
		ProfileURL: optionalClean(csvValue(headers, record, "contact_profile_url")),
		ContactURL: optionalClean(csvValue(headers, record, "contact_url")),
		FormAction: optionalClean(csvValue(headers, record, "contact_form_action")),
		Evidence:   splitAgencySourceList(csvValue(headers, record, "contact_evidence")),
	}
	if cleanText(string(status)) != "" {
		contact.Status = &status
	}
	if contact.Name == nil && contact.Title == nil && contact.Email == nil && contact.ProfileURL == nil && contact.ContactURL == nil && contact.FormAction == nil {
		return nil
	}
	return &contact
}

func normalizeAgencySourceHeader(value string) string {
	cleaned := strings.ToLower(cleanText(value))
	cleaned = strings.NewReplacer(" ", "_", "-", "_").Replace(cleaned)
	switch cleaned {
	case "linkedin", "linkedin_company_url":
		return "linkedin_url"
	case "url", "profile_url":
		return "source_url"
	case "contact_linkedin", "contact_linkedin_url":
		return "contact_profile_url"
	case "form_action":
		return "contact_form_action"
	default:
		return cleaned
	}
}

func csvValue(headers map[string]int, record []string, key string) string {
	index, ok := headers[key]
	if !ok || index < 0 || index >= len(record) {
		return ""
	}
	return cleanText(record[index])
}

func splitAgencySourceList(value string) []string {
	cleaned := cleanText(value)
	if cleaned == "" {
		return []string{}
	}
	parts := strings.FieldsFunc(cleaned, func(r rune) bool {
		return r == ';' || r == '|'
	})
	return cleanStringItems(parts)
}

func optionalInt(value string) *int {
	cleaned := cleanText(value)
	if cleaned == "" {
		return nil
	}
	parsed, err := strconv.Atoi(cleaned)
	if err != nil {
		return nil
	}
	return &parsed
}

func agencySourceRowIdentityKey(row AgencySourceRow) string {
	if row.AccountURL != nil && cleanText(*row.AccountURL) != "" {
		return "account_url:" + appNormalizeURL(*row.AccountURL)
	}
	if row.LinkedInURL != nil && cleanText(*row.LinkedInURL) != "" {
		return "linkedin_url:" + appNormalizeURL(*row.LinkedInURL)
	}
	if domain := domainFromWebsite(row.Website); domain != nil {
		return "domain:" + strings.ToLower(cleanText(*domain))
	}
	if row.SourceURL != nil && cleanText(*row.SourceURL) != "" {
		return "source_url:" + cleanText(*row.SourceURL)
	}
	return "name:" + strings.ToLower(cleanText(row.Name))
}

func appNormalizeURL(value string) string {
	return strings.ToLower(strings.TrimRight(cleanText(value), "/"))
}
