package app

import (
	"crypto/sha256"
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

type AcceptanceFollowupStatus string

const (
	AcceptanceFollowupStatusDrafted            AcceptanceFollowupStatus = "drafted"
	AcceptanceFollowupStatusDryRunReady        AcceptanceFollowupStatus = "dry_run_ready"
	AcceptanceFollowupStatusSent               AcceptanceFollowupStatus = "sent"
	AcceptanceFollowupStatusConversationExists AcceptanceFollowupStatus = "conversation_exists"
	AcceptanceFollowupStatusNotMessageable     AcceptanceFollowupStatus = "not_messageable"
	AcceptanceFollowupStatusBlocked            AcceptanceFollowupStatus = "blocked"
	AcceptanceFollowupStatusSendFailed         AcceptanceFollowupStatus = "send_failed"
)

type AcceptanceFollowupRecord struct {
	Key          string                      `json:"key"`
	ID           string                      `json:"id"`
	Source       string                      `json:"source"`
	Name         string                      `json:"name"`
	ProfileURL   *string                     `json:"profile_url"`
	DraftedAt    time.Time                   `json:"drafted_at"`
	UpdatedAt    time.Time                   `json:"updated_at"`
	AcceptedAt   time.Time                   `json:"accepted_at"`
	Strategy     DraftStrategy               `json:"strategy"`
	Angle        string                      `json:"angle"`
	Draft        string                      `json:"draft"`
	Evidence     []string                    `json:"evidence"`
	Warnings     []string                    `json:"warnings"`
	Status       AcceptanceFollowupStatus    `json:"status"`
	SentAt       *time.Time                  `json:"sent_at"`
	Attempts     []AcceptanceFollowupAttempt `json:"attempts"`
	ReportPath   string                      `json:"report_path"`
	ResearchPath *string                     `json:"research_path"`
}

type AcceptanceFollowupAttempt struct {
	At          time.Time         `json:"at"`
	DryRun      bool              `json:"dry_run"`
	Status      string            `json:"status"`
	ResultURL   *string           `json:"result_url"`
	Note        *string           `json:"note"`
	OutPath     string            `json:"out_path"`
	Diagnostics map[string]string `json:"diagnostics"`
}

func (l *AcceptanceFollowupLedger) Normalize() {
	if l.Drafts == nil {
		l.Drafts = []AcceptanceFollowupRecord{}
	}
	for i := range l.Drafts {
		if l.Drafts[i].Key == "" {
			l.Drafts[i].Key = CandidateKey(l.Drafts[i].Source, l.Drafts[i].Name, l.Drafts[i].ProfileURL)
		}
		if l.Drafts[i].ID == "" {
			l.Drafts[i].ID = AcceptanceFollowupID(l.Drafts[i].Key)
		}
		if l.Drafts[i].Status == "" {
			l.Drafts[i].Status = AcceptanceFollowupStatusDrafted
		}
		if l.Drafts[i].Evidence == nil {
			l.Drafts[i].Evidence = []string{}
		}
		if l.Drafts[i].Warnings == nil {
			l.Drafts[i].Warnings = []string{}
		}
		if l.Drafts[i].Attempts == nil {
			l.Drafts[i].Attempts = []AcceptanceFollowupAttempt{}
		}
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

func (l AcceptanceFollowupLedger) FindByID(id string) (int, bool) {
	for index, record := range l.Drafts {
		if record.ID == id {
			return index, true
		}
	}
	return -1, false
}

func (l AcceptanceFollowupLedger) Ready(limit int) []AcceptanceFollowupRecord {
	result := []AcceptanceFollowupRecord{}
	for _, record := range l.Drafts {
		if record.Status != AcceptanceFollowupStatusDryRunReady {
			continue
		}
		result = append(result, record)
		if limit > 0 && len(result) >= limit {
			break
		}
	}
	return result
}

func (l AcceptanceFollowupLedger) NeedsDryRun(limit int) []AcceptanceFollowupRecord {
	result := []AcceptanceFollowupRecord{}
	for _, record := range l.Drafts {
		switch record.Status {
		case AcceptanceFollowupStatusDrafted, AcceptanceFollowupStatusNotMessageable, AcceptanceFollowupStatusBlocked, AcceptanceFollowupStatusSendFailed:
		default:
			continue
		}
		result = append(result, record)
		if limit > 0 && len(result) >= limit {
			break
		}
	}
	return result
}

func (l *AcceptanceFollowupLedger) RecordReport(report DraftReport, reportPath string, researchPath *string) int {
	written := 0
	for _, item := range report.Items {
		key := CandidateKey(item.Candidate.Source, item.Candidate.Name, item.Candidate.ProfileURL)
		exists := -1
		for index, record := range l.Drafts {
			if record.Key == key {
				exists = index
				break
			}
		}
		if exists >= 0 {
			if !l.Drafts[exists].Terminal() {
				l.Drafts[exists].DraftedAt = report.GeneratedAt
				l.Drafts[exists].UpdatedAt = report.GeneratedAt
				l.Drafts[exists].Strategy = report.Strategy
				l.Drafts[exists].Angle = item.Angle
				l.Drafts[exists].Draft = item.Draft
				l.Drafts[exists].Evidence = append([]string{}, item.Evidence...)
				l.Drafts[exists].Warnings = append([]string{}, item.Warnings...)
				l.Drafts[exists].ReportPath = reportPath
				l.Drafts[exists].ResearchPath = researchPath
			}
			continue
		}
		l.Drafts = append(l.Drafts, AcceptanceFollowupRecord{
			Key:          key,
			ID:           AcceptanceFollowupID(key),
			Source:       item.Candidate.Source,
			Name:         item.Candidate.Name,
			ProfileURL:   item.Candidate.ProfileURL,
			DraftedAt:    report.GeneratedAt,
			UpdatedAt:    report.GeneratedAt,
			AcceptedAt:   item.Candidate.AcceptedAt,
			Strategy:     report.Strategy,
			Angle:        item.Angle,
			Draft:        item.Draft,
			Evidence:     append([]string{}, item.Evidence...),
			Warnings:     append([]string{}, item.Warnings...),
			Status:       AcceptanceFollowupStatusDrafted,
			Attempts:     []AcceptanceFollowupAttempt{},
			ReportPath:   reportPath,
			ResearchPath: researchPath,
		})
		written++
	}
	return written
}

func (r AcceptanceFollowupRecord) Terminal() bool {
	switch r.Status {
	case AcceptanceFollowupStatusSent, AcceptanceFollowupStatusConversationExists:
		return true
	default:
		return false
	}
}

func AcceptanceFollowupID(key string) string {
	sum := sha256.Sum256([]byte(key))
	return "afu_" + fmt.Sprintf("%x", sum[:])[:12]
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
		key := CandidateKey(item.Candidate.Source, item.Candidate.Name, item.Candidate.ProfileURL)
		lines = append(lines, "")
		lines = append(lines, "## "+cleanInline(item.Candidate.Name))
		lines = append(lines, "- Follow-up ID: `"+AcceptanceFollowupID(key)+"`")
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
	draftAngleInvestorAdvisor draftAngleKind = "investor-advisor"
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
	case draftAngleRecruiter:
		draft = recruiterAcceptedFollowupDraft(first)
	case draftAngleAgency:
		draft = agencyAcceptedFollowupDraft(first)
	case draftAngleInvestorAdvisor:
		draft = investorAdvisorAcceptedFollowupDraft(first)
	case draftAngleTechnicalLeader:
		draft = technicalAcceptedFollowupDraft(first)
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
	return fmt.Sprintf("Hey, %s. Thanks for connecting.\n\nI'm available for contract product engineering work through HC Studio LLC, mostly around full-stack product builds and AI workflows.\n\nIf it would be helpful, I'm happy to send over my resume and a couple of project examples.\n\nBest,\nHanif Carroll", first)
}

func technicalAcceptedFollowupDraft(first string) string {
	return fmt.Sprintf("Hey, %s. Thanks for connecting.\n\nI'm available for contract product engineering work through HC Studio LLC, mostly around full-stack product builds, AI workflows, and prototype-to-production work.\n\nIf it would be helpful, I'm happy to send over my resume and a couple of project examples.\n\nBest,\nHanif Carroll", first)
}

func investorAdvisorAcceptedFollowupDraft(first string) string {
	return fmt.Sprintf("Hey, %s. Thanks for connecting.\n\nI'm available for contract product engineering work through HC Studio LLC, mostly helping teams ship full-stack products and AI workflows.\n\nIf someone in your network ever needs that kind of help, I'm happy to send over my resume and a couple of project examples.\n\nBest,\nHanif Carroll", first)
}

func agencyAcceptedFollowupDraft(first string) string {
	return fmt.Sprintf("Hey, %s. Thanks for connecting.\n\nI'm available for contract product engineering work through HC Studio LLC, mostly helping with project overflow, prototypes, and AI-enabled product builds.\n\nIf it would be helpful, I'm happy to send over my resume and a couple of project examples.\n\nBest,\nHanif Carroll", first)
}

func recruiterAcceptedFollowupDraft(first string) string {
	return fmt.Sprintf("Hey, %s. Thanks for connecting.\n\nI'm available for contract product engineering work through HC Studio LLC, focused on full-stack product builds and AI workflows.\n\nIf useful, I'm happy to send over my resume and a couple of project examples for your files.\n\nBest,\nHanif Carroll", first)
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
	combined := strings.ToLower(strings.Join([]string{sourceLower, titleLower, companySuffix, webSuffix}, " "))
	switch {
	case containsAny(combined, "recruit", "staffing", "talent acquisition", "headhunter", "hire recruiters"):
		return draftAngle{kind: draftAngleRecruiter, label: "contract-role availability ask" + companySuffix + webSuffix}
	case containsAny(combined, "agency", "studio", "digital transformation", "custom ai solutions", "web design", "ux/ui", "cro", "seo", "implementation partner", "technology services", "software agency", "development agency", "consulting partners", "consulting services"):
		return draftAngle{kind: draftAngleAgency, label: "agency overflow or specialist contractor capacity" + companySuffix + webSuffix}
	case containsAny(combined, "cto", "cpo", "chief product", "product lead", "product manager", "ai product", "platform", "llm", "agentic", "software engineer", "developer", "technical", "data", "automation", "workflow", "internal tools", "voice agents", "enterprise ai", "ai-native", "product leader") || strings.Contains(sourceLower, "product leaders"):
		return draftAngle{kind: draftAngleTechnicalLeader, label: "senior product-engineering contractor help" + companySuffix + webSuffix}
	case containsAny(combined, "investor", "investment", "m&a", "broker", "fundraising", "private equity", "advisor", "coach", "mentor", "board", "career coach"):
		return draftAngle{kind: draftAngleInvestorAdvisor, label: "network referral for contract product-engineering help" + companySuffix + webSuffix}
	case strings.Contains(sourceLower, "vertical") || strings.Contains(sourceLower, "proof"):
		return draftAngle{kind: draftAngleProofMatched, label: "proof-matched product/workflow help" + companySuffix + webSuffix}
	default:
		return draftAngle{kind: draftAngleGeneralFounder, label: "fast contract product-engineering help" + companySuffix + webSuffix}
	}
}

func containsAny(value string, needles ...string) bool {
	for _, needle := range needles {
		if strings.Contains(value, needle) {
			return true
		}
	}
	return false
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
