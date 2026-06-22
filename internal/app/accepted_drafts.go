package app

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
)

type AcceptedDraftCandidate struct {
	RunID              uuid.UUID `json:"run_id"`
	RunDate            Date      `json:"run_date"`
	Source             string    `json:"source"`
	Name               string    `json:"name"`
	ProfileURL         *string   `json:"profile_url"`
	SentAt             time.Time `json:"sent_at"`
	AcceptedAt         time.Time `json:"accepted_at"`
	Relationship       *string   `json:"relationship"`
	AcceptanceNote     *string   `json:"acceptance_note"`
	AcceptanceEvidence *string   `json:"acceptance_evidence"`
}

type AcceptanceFollowupLedger struct {
	Drafts []AcceptanceFollowupRecord `json:"drafts"`
}

type AcceptanceFollowupRecord struct {
	Key          string        `json:"key"`
	Source       string        `json:"source"`
	Name         string        `json:"name"`
	ProfileURL   *string       `json:"profile_url"`
	DraftedAt    time.Time     `json:"drafted_at"`
	AcceptedAt   time.Time     `json:"accepted_at"`
	Strategy     DraftStrategy `json:"strategy"`
	ReportPath   string        `json:"report_path"`
	ResearchPath *string       `json:"research_path"`
}

func (l *AcceptanceFollowupLedger) Normalize() {
	if l.Drafts == nil {
		l.Drafts = []AcceptanceFollowupRecord{}
	}
}

func (l AcceptanceFollowupLedger) HasDraftFor(candidate AcceptedDraftCandidate) bool {
	key := CandidateKey(candidate.Source, candidate.Name, candidate.ProfileURL)
	for _, record := range l.Drafts {
		if record.Key == key {
			return true
		}
	}
	return false
}

func (l *AcceptanceFollowupLedger) RecordReport(report DraftReport, reportPath string, researchPath *string) int {
	written := 0
	for _, item := range report.Items {
		key := CandidateKey(item.Candidate.Source, item.Candidate.Name, item.Candidate.ProfileURL)
		exists := false
		for _, record := range l.Drafts {
			if record.Key == key {
				exists = true
				break
			}
		}
		if exists {
			continue
		}
		l.Drafts = append(l.Drafts, AcceptanceFollowupRecord{
			Key:          key,
			Source:       item.Candidate.Source,
			Name:         item.Candidate.Name,
			ProfileURL:   item.Candidate.ProfileURL,
			DraftedAt:    report.GeneratedAt,
			AcceptedAt:   item.Candidate.AcceptedAt,
			Strategy:     report.Strategy,
			ReportPath:   reportPath,
			ResearchPath: researchPath,
		})
		written++
	}
	return written
}

type AcceptedResearchArtifact struct {
	CapturedAt *string               `json:"capturedAt"`
	Rows       []AcceptedResearchRow `json:"rows"`
}

func LoadAcceptedResearchArtifact(path string) (AcceptedResearchArtifact, error) {
	var artifact AcceptedResearchArtifact
	if err := readJSONFile(path, &artifact, "reading accepted research "+path, "parsing accepted research "+path); err != nil {
		return AcceptedResearchArtifact{}, err
	}
	if artifact.Rows == nil {
		artifact.Rows = []AcceptedResearchRow{}
	}
	return artifact, nil
}

type AcceptedResearchRow struct {
	Source     string            `json:"source"`
	Name       string            `json:"name"`
	ProfileURL *string           `json:"profileUrl"`
	SalesNav   *SalesNavResearch `json:"salesNav"`
	Web        *WebResearch      `json:"web"`
	Warnings   []string          `json:"warnings"`
}

func (r *AcceptedResearchRow) UnmarshalJSON(data []byte) error {
	type row struct {
		Source          string            `json:"source"`
		Name            string            `json:"name"`
		ProfileURL      *string           `json:"profileUrl"`
		ProfileURLSnake *string           `json:"profile_url"`
		SalesNav        *SalesNavResearch `json:"salesNav"`
		Web             *WebResearch      `json:"web"`
		Warnings        []string          `json:"warnings"`
	}
	var value row
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	r.Source = value.Source
	r.Name = value.Name
	r.ProfileURL = value.ProfileURL
	if r.ProfileURL == nil {
		r.ProfileURL = value.ProfileURLSnake
	}
	r.SalesNav = value.SalesNav
	r.Web = value.Web
	r.Warnings = value.Warnings
	if r.Warnings == nil {
		r.Warnings = []string{}
	}
	return nil
}

type SalesNavResearch struct {
	Name     *string  `json:"name"`
	Title    *string  `json:"title"`
	Company  *string  `json:"company"`
	Location *string  `json:"location"`
	URL      *string  `json:"url"`
	Warnings []string `json:"warnings"`
}

type WebResearch struct {
	Query    *string     `json:"query"`
	Results  []WebResult `json:"results"`
	Warnings []string    `json:"warnings"`
}

type WebResult struct {
	Title   *string `json:"title"`
	URL     *string `json:"url"`
	Snippet *string `json:"snippet"`
}

type DraftReport struct {
	GeneratedAt        time.Time     `json:"generated_at"`
	Strategy           DraftStrategy `json:"strategy"`
	ResearchPath       *string       `json:"research_path"`
	ResearchCapturedAt *string       `json:"research_captured_at"`
	Items              []DraftItem   `json:"items"`
	SkippedNames       []string      `json:"skipped_names"`
}

type DraftItem struct {
	Candidate AcceptedDraftCandidate `json:"candidate"`
	Angle     string                 `json:"angle"`
	Draft     string                 `json:"draft"`
	Evidence  []string               `json:"evidence"`
	Warnings  []string               `json:"warnings"`
}

func CandidateKey(source, name string, profileURL *string) string {
	url := ""
	if profileURL != nil {
		url = NormalizeLinkedInURL(*profileURL)
	}
	return strings.TrimSpace(source) + "|" + strings.TrimSpace(name) + "|" + url
}

func BuildDraftReport(candidates []AcceptedDraftCandidate, artifact *AcceptedResearchArtifact, strategy DraftStrategy, researchPath *string) DraftReport {
	var researchCapturedAt *string
	researchByKey := map[string]AcceptedResearchRow{}
	if artifact != nil {
		researchCapturedAt = artifact.CapturedAt
		for _, row := range artifact.Rows {
			researchByKey[CandidateKey(row.Source, row.Name, row.ProfileURL)] = row
		}
	}
	seen := map[string]bool{}
	items := []DraftItem{}
	skippedNames := []string{}
	for _, candidate := range candidates {
		key := CandidateKey(candidate.Source, candidate.Name, candidate.ProfileURL)
		if seen[key] {
			skippedNames = append(skippedNames, candidate.Name)
			continue
		}
		seen[key] = true
		var research *AcceptedResearchRow
		if row, ok := researchByKey[key]; ok {
			copy := row
			research = &copy
		}
		items = append(items, buildDraftItem(candidate, research, strategy))
	}
	return DraftReport{
		GeneratedAt:        time.Now(),
		Strategy:           strategy,
		ResearchPath:       researchPath,
		ResearchCapturedAt: researchCapturedAt,
		Items:              items,
		SkippedNames:       skippedNames,
	}
}

func RenderMarkdown(report DraftReport) string {
	lines := []string{}
	lines = append(lines, fmt.Sprintf("# LinkedIn Accepted Follow-Up Drafts %s", Date{Time: report.GeneratedAt}.String()))
	lines = append(lines, "")
	lines = append(lines, fmt.Sprintf("- Generated: `%s`", report.GeneratedAt.Format(time.RFC3339)))
	lines = append(lines, fmt.Sprintf("- Strategy: `%s`", report.Strategy.DebugString()))
	lines = append(lines, fmt.Sprintf("- Draft count: %d", len(report.Items)))
	if report.ResearchPath != nil {
		lines = append(lines, fmt.Sprintf("- Research artifact: `%s`", *report.ResearchPath))
	}
	if report.ResearchCapturedAt != nil {
		lines = append(lines, fmt.Sprintf("- Research captured: `%s`", cleanInline(*report.ResearchCapturedAt)))
	}
	if len(report.SkippedNames) > 0 {
		lines = append(lines, "- Duplicate candidates skipped: "+strings.Join(report.SkippedNames, ", "))
	}
	if len(report.Items) == 0 {
		lines = append(lines, "")
		lines = append(lines, "No newly accepted connections need first-message drafts.")
		return strings.Join(lines, "\n")
	}
	for _, item := range report.Items {
		lines = append(lines, "")
		lines = append(lines, "## "+cleanInline(item.Candidate.Name))
		lines = append(lines, "- Source: "+cleanInline(item.Candidate.Source))
		if item.Candidate.ProfileURL != nil {
			lines = append(lines, "- Profile: "+cleanInline(*item.Candidate.ProfileURL))
		}
		lines = append(lines, fmt.Sprintf("- Accepted at: `%s`", item.Candidate.AcceptedAt.Format(time.RFC3339)))
		lines = append(lines, "- Best angle: "+cleanInline(item.Angle))
		if len(item.Evidence) > 0 {
			lines = append(lines, "- Evidence used:")
			for _, evidence := range item.Evidence {
				lines = append(lines, "  - "+cleanInline(evidence))
			}
		}
		if len(item.Warnings) > 0 {
			lines = append(lines, "- Warnings:")
			for _, warning := range item.Warnings {
				lines = append(lines, "  - "+cleanInline(warning))
			}
		}
		lines = append(lines, "")
		lines = append(lines, "Draft:")
		lines = append(lines, "")
		lines = append(lines, blockquote(item.Draft)...)
	}
	return strings.Join(lines, "\n")
}

func blockquote(value string) []string {
	normalized := strings.ReplaceAll(strings.TrimSpace(value), "\r\n", "\n")
	if normalized == "" {
		return []string{">"}
	}
	lines := strings.Split(normalized, "\n")
	out := make([]string, 0, len(lines))
	for _, line := range lines {
		if strings.TrimSpace(line) == "" {
			out = append(out, ">")
			continue
		}
		out = append(out, "> "+line)
	}
	return out
}

func buildDraftItem(candidate AcceptedDraftCandidate, research *AcceptedResearchRow, strategy DraftStrategy) DraftItem {
	switch strategy {
	case DraftStrategyAsapContractV1:
		return buildAsapContractDraft(candidate, research)
	default:
		return buildAsapContractDraft(candidate, research)
	}
}

type draftAngleKind string

const (
	draftAngleRecruiter       draftAngleKind = "recruiter"
	draftAngleAgency          draftAngleKind = "agency"
	draftAngleTechnicalLeader draftAngleKind = "technical-leader"
	draftAngleProofMatched    draftAngleKind = "proof-matched"
	draftAngleGeneralFounder  draftAngleKind = "general-founder"
)

type draftAngle struct {
	kind  draftAngleKind
	label string
}

func buildAsapContractDraft(candidate AcceptedDraftCandidate, research *AcceptedResearchRow) DraftItem {
	var salesNav *SalesNavResearch
	if research != nil {
		salesNav = research.SalesNav
	}
	var title *string
	var company *string
	if salesNav != nil {
		title = nonEmptyPtr(salesNav.Title)
		company = nonEmptyPtr(salesNav.Company)
	}
	var webResult *WebResult
	if research != nil && research.Web != nil && len(research.Web.Results) > 0 {
		webResult = &research.Web.Results[0]
	}
	first := firstName(candidate.Name)
	angle := chooseAngle(candidate.Source, title, company, webResult)
	draft := ""
	switch angle.kind {
	case draftAngleTechnicalLeader:
		draft = technicalAcceptedFollowupDraft(first, company)
	default:
		draft = generalAcceptedFollowupDraft(first)
	}
	evidence := []string{}
	if title != nil {
		evidence = append(evidence, "Sales Nav title/headline: "+*title)
	}
	if company != nil {
		evidence = append(evidence, "Sales Nav company: "+*company)
	}
	if salesNav != nil {
		if value := nonEmptyPtr(salesNav.Name); value != nil {
			evidence = append(evidence, "Sales Nav displayed name: "+*value)
		}
		if value := nonEmptyPtr(salesNav.Location); value != nil {
			evidence = append(evidence, "Sales Nav location: "+*value)
		}
		if value := nonEmptyPtr(salesNav.URL); value != nil {
			evidence = append(evidence, "Sales Nav URL after load: "+*value)
		}
	}
	if candidate.Relationship != nil {
		evidence = append(evidence, "Sales Nav relationship: "+*candidate.Relationship)
	}
	if candidate.AcceptanceNote != nil {
		evidence = append(evidence, "Acceptance check: "+*candidate.AcceptanceNote)
	}
	if webResult != nil {
		if webResult.Title != nil {
			evidence = append(evidence, "Public web result: "+*webResult.Title)
		}
		if webResult.URL != nil {
			evidence = append(evidence, "Public web URL: "+*webResult.URL)
		}
		if webResult.Snippet != nil {
			evidence = append(evidence, "Public web snippet: "+*webResult.Snippet)
		}
	}
	if research != nil && research.Web != nil && research.Web.Query != nil && *research.Web.Query != "" {
		evidence = append(evidence, "Public web query: "+*research.Web.Query)
	}
	warnings := []string{}
	if research == nil {
		warnings = append(warnings, "No research row matched this accepted candidate; draft uses source and ledger evidence only.")
	} else {
		warnings = append(warnings, research.Warnings...)
		if research.SalesNav != nil {
			warnings = append(warnings, research.SalesNav.Warnings...)
		}
		if research.Web != nil {
			warnings = append(warnings, research.Web.Warnings...)
		}
	}
	if title == nil && company == nil {
		warnings = append(warnings, "Sales Nav title/company were not extracted; review before sending.")
	}
	return DraftItem{Candidate: candidate, Angle: angle.label, Draft: draft, Evidence: evidence, Warnings: warnings}
}

func generalAcceptedFollowupDraft(first string) string {
	return fmt.Sprintf("Thanks for connecting, %s.\n\nI'm available for contract product engineering work through HC Studio LLC. I usually help with full-stack product builds, AI workflows, internal tools, and prototype-to-production work.\n\nIf that ever becomes relevant, would you like me to send over my resume and a couple of project examples?\n\nBest,\nHanif Carroll", first)
}

func technicalAcceptedFollowupDraft(first string, company *string) string {
	context := "Given your work"
	if company != nil && cleanInline(*company) != "" {
		context = "Given your work at " + cleanInline(*company)
	}
	return fmt.Sprintf("Thanks for connecting, %s.\n\nI'm available for contract product engineering work through HC Studio LLC. I usually help with full-stack product builds, AI workflows, internal tools, and prototype-to-production work.\n\n%s, I thought the most relevant overlap would be helping ship a product, workflow, or internal tool quickly.\n\nWould you like me to send over my resume and a couple of project examples?\n\nBest,\nHanif Carroll", first, context)
}

func chooseAngle(source string, title *string, company *string, webResult *WebResult) draftAngle {
	sourceLower := strings.ToLower(source)
	titleLower := ""
	if title != nil {
		titleLower = strings.ToLower(*title)
	}
	companySuffix := ""
	if company != nil {
		companySuffix = " for " + cleanInline(*company)
	}
	webSuffix := ""
	if webResult != nil && webResult.Title != nil {
		webSuffix = "; public result: " + cleanInline(*webResult.Title)
	}
	switch {
	case strings.Contains(sourceLower, "recruiter") || strings.Contains(sourceLower, "staffing"):
		return draftAngle{kind: draftAngleRecruiter, label: "contract-role availability ask" + companySuffix + webSuffix}
	case strings.Contains(sourceLower, "agency") || strings.Contains(sourceLower, "delivery"):
		return draftAngle{kind: draftAngleAgency, label: "agency overflow or specialist contractor capacity" + companySuffix + webSuffix}
	case strings.Contains(sourceLower, "cto") || strings.Contains(sourceLower, "engineering") || strings.Contains(titleLower, "cto") || strings.Contains(titleLower, "engineering"):
		return draftAngle{kind: draftAngleTechnicalLeader, label: "senior product-engineering contractor help" + companySuffix + webSuffix}
	case strings.Contains(sourceLower, "vertical") || strings.Contains(sourceLower, "proof"):
		return draftAngle{kind: draftAngleProofMatched, label: "proof-matched product/workflow help" + companySuffix + webSuffix}
	default:
		return draftAngle{kind: draftAngleGeneralFounder, label: "fast contract product-engineering help" + companySuffix + webSuffix}
	}
}

func firstName(name string) string {
	fields := strings.Fields(name)
	if len(fields) == 0 {
		return "there"
	}
	return fields[0]
}

func nonEmptyPtr(value *string) *string {
	if value == nil || *value == "" {
		return nil
	}
	return value
}

func WriteDraftReport(path string, report DraftReport) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", filepath.Dir(path), err)
	}
	if err := os.WriteFile(path, []byte(RenderMarkdown(report)), 0o644); err != nil {
		return fmt.Errorf("writing %s: %w", path, err)
	}
	return nil
}

func SortDraftCandidates(candidates []AcceptedDraftCandidate) {
	sort.SliceStable(candidates, func(i, j int) bool {
		if candidates[i].AcceptedAt.Equal(candidates[j].AcceptedAt) {
			return candidates[i].Name < candidates[j].Name
		}
		return candidates[i].AcceptedAt.Before(candidates[j].AcceptedAt)
	})
}
