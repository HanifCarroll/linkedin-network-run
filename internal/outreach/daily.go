package outreach

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/hanifcarroll/linkedin-network-run/internal/app"
)

const (
	RecruiterSource                = "ASAP - Contract Recruiter Titles"
	AgencySource                   = "ASAP - Agency Digital Agency Leaders"
	AgencySoftwareConsultingSource = "ASAP - Agency Software Consulting Leaders"
	AgencyDevelopmentAgencySource  = "ASAP - Agency Development Agency Leaders"
)

type DailyOptions struct {
	Session              string
	Playwriter           string
	CaptureScript        string
	MessageScript        string
	SavedSearchesScript  string
	SavedSearches        string
	TargetAgencies       int
	TargetRecruiters     int
	PagesPerCapture      uint32
	Limit                uint32
	StopAfterConnectable uint32
	RowScrollDelayMS     uint32
	MaxCaptureRounds     int
	AllowSend            bool
	RefreshSavedSearches bool
	SkipSessionReset     bool
	CaptureOutDir        string
	MessageOutDir        string
	DashboardPath        string
	PrintMarkdown        bool
	TimeoutMS            uint32
}

type DailyResult struct {
	Report        DashboardReport `json:"report"`
	DashboardPath string          `json:"dashboard_path"`
	Markdown      string          `json:"markdown"`
}

type dailyBucket struct {
	Name    string
	Sources []string
	Target  int
}

func RunDaily(store *Store, options DailyOptions) (DailyResult, error) {
	options = normalizeDailyOptions(store, options)
	if strings.TrimSpace(options.Session) == "" {
		return DailyResult{}, fmt.Errorf("--session is required")
	}
	if !options.SkipSessionReset {
		if err := app.ResetPlaywriterSession(options.Playwriter, options.Session); err != nil {
			return DailyResult{}, err
		}
	}
	buckets := dailyBuckets(options)
	if dailySourcesNeedSavedSearches(buckets) {
		if err := EnsureSavedSearches(options); err != nil {
			return DailyResult{}, err
		}
	}
	actions := []DailyLeadAction{}
	for _, bucket := range buckets {
		if bucket.Target <= 0 {
			continue
		}
		if len(bucket.Sources) == 0 {
			return DailyResult{}, fmt.Errorf("daily bucket %q has no sources", bucket.Name)
		}
		for round := 0; round < options.MaxCaptureRounds; round++ {
			for _, source := range bucket.Sources {
				state, err := store.Load()
				if err != nil {
					return DailyResult{}, err
				}
				if bucketCompleteForRun(state, bucket.Name, bucket.Target, options.AllowSend, actions) {
					break
				}
				if err := captureSource(store, options, source, round+1); err != nil {
					return DailyResult{}, err
				}
				state, err = store.Load()
				if err != nil {
					return DailyResult{}, err
				}
				DraftMessages(&state, 0)
				if err := store.Save(state); err != nil {
					return DailyResult{}, err
				}
				if err := validateBucket(store, options, bucket.Name, bucket.Target, &actions); err != nil {
					return DailyResult{}, err
				}
				if options.AllowSend {
					if err := sendBucket(store, options, bucket.Name, bucket.Target, &actions); err != nil {
						return DailyResult{}, err
					}
				}
				state, err = store.Load()
				if err != nil {
					return DailyResult{}, err
				}
				if bucketCompleteForRun(state, bucket.Name, bucket.Target, options.AllowSend, actions) {
					break
				}
			}
			state, err := store.Load()
			if err != nil {
				return DailyResult{}, err
			}
			if bucketCompleteForRun(state, bucket.Name, bucket.Target, options.AllowSend, actions) {
				break
			}
		}
	}
	state, err := store.Load()
	if err != nil {
		return DailyResult{}, err
	}
	report := BuildDashboardReport(state, store.StatePath(), options.TargetAgencies, options.TargetRecruiters, options.AllowSend, actions)
	markdown := RenderDashboardMarkdown(report)
	if err := WriteDashboardMarkdown(options.DashboardPath, report); err != nil {
		return DailyResult{}, err
	}
	return DailyResult{Report: report, DashboardPath: options.DashboardPath, Markdown: markdown}, nil
}

func dailyBuckets(options DailyOptions) []dailyBucket {
	return []dailyBucket{
		{
			Name:    "agency",
			Sources: []string{AgencySource, AgencySoftwareConsultingSource, AgencyDevelopmentAgencySource},
			Target:  options.TargetAgencies,
		},
		{
			Name:    "recruiter",
			Sources: []string{RecruiterSource},
			Target:  options.TargetRecruiters,
		},
	}
}

func dailySourcesNeedSavedSearches(buckets []dailyBucket) bool {
	for _, bucket := range buckets {
		for _, source := range bucket.Sources {
			if _, ok := defaultOutreachSourceURL(source); !ok {
				return true
			}
		}
	}
	return false
}

func EnsureSavedSearches(options DailyOptions) error {
	if !options.RefreshSavedSearches {
		if _, err := os.Stat(options.SavedSearches); err == nil {
			return nil
		}
	}
	if err := os.MkdirAll(filepath.Dir(options.SavedSearches), 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", filepath.Dir(options.SavedSearches), err)
	}
	outJSON, err := json.Marshal(options.SavedSearches)
	if err != nil {
		return err
	}
	configJS := fmt.Sprintf("state.salesNavSavedSearchConfig = { out: %s }; console.log(JSON.stringify(state.salesNavSavedSearchConfig));", string(outJSON))
	if err := app.RunPlaywriterConfig(options.Playwriter, options.Session, configJS); err != nil {
		return err
	}
	return app.RunPlaywriterFileWithTimeout(options.Playwriter, options.Session, options.SavedSearchesScript, options.TimeoutMS)
}

func captureSource(store *Store, options DailyOptions, source string, round int) error {
	state, err := store.Load()
	if err != nil {
		return err
	}
	var explicitURL *string
	if cursor, ok := state.CaptureCursors[source]; ok && cursor.ResumeURL != nil {
		explicitURL = cursor.ResumeURL
	}
	url, err := resolveDailyCaptureURL(explicitURL, options.SavedSearches, source)
	if err != nil {
		return err
	}
	outDir := filepath.Join(options.CaptureOutDir, safePathSegment(source), fmt.Sprintf("round-%02d", round))
	path, err := app.RunPlaywriterCapture(options.Playwriter, options.Session, options.CaptureScript, outDir, source, url, app.CaptureRunOptions{
		Pages:                options.PagesPerCapture,
		StopAfterConnectable: options.StopAfterConnectable,
		Limit:                options.Limit,
		RowScrollDelayMS:     options.RowScrollDelayMS,
		OnlyConnectable:      false,
	})
	if err != nil {
		return err
	}
	capture, err := app.LoadSalesNavCapture(path)
	if err != nil {
		return err
	}
	state, err = store.Load()
	if err != nil {
		return err
	}
	if _, err := ImportCapture(&state, capture, ImportOptions{}); err != nil {
		return err
	}
	return store.Save(state)
}

func resolveDailyCaptureURL(explicitURL *string, savedSearches string, source string) (string, error) {
	if explicitURL != nil && cleanText(*explicitURL) != "" {
		return app.ResolveCaptureURL(explicitURL, savedSearches, source, "--url")
	}
	if generatedURL, ok := defaultOutreachSourceURL(source); ok {
		return generatedURL, nil
	}
	return app.ResolveCaptureURL(nil, savedSearches, source, "--url")
}

func validateBucket(store *Store, options DailyOptions, bucket string, target int, actions *[]DailyLeadAction) error {
	for {
		state, err := store.Load()
		if err != nil {
			return err
		}
		if readyCount(state, bucket) >= target {
			return nil
		}
		candidates := leadsForMessageValidation(state, bucket)
		if len(candidates) == 0 {
			return nil
		}
		lead := candidates[0]
		if err := SendMessage(store, SendMessageOptions{
			LeadID:     lead.ID,
			Session:    options.Session,
			Playwriter: options.Playwriter,
			Script:     options.MessageScript,
			OutDir:     options.MessageOutDir,
			DryRun:     true,
			AllowSend:  false,
			TimeoutMS:  options.TimeoutMS,
		}); err != nil {
			return err
		}
		recordLatestAction(store, bucket, lead.ID, "dry-run-message", actions)
	}
}

func sendBucket(store *Store, options DailyOptions, bucket string, target int, actions *[]DailyLeadAction) error {
	for sentCountFromActions(*actions, bucket) < target {
		state, err := store.Load()
		if err != nil {
			return err
		}
		candidates := readyLeads(state, bucket)
		if len(candidates) == 0 {
			return nil
		}
		lead := candidates[0]
		if err := SendMessage(store, SendMessageOptions{
			LeadID:     lead.ID,
			Session:    options.Session,
			Playwriter: options.Playwriter,
			Script:     options.MessageScript,
			OutDir:     options.MessageOutDir,
			DryRun:     false,
			AllowSend:  true,
			TimeoutMS:  options.TimeoutMS,
		}); err != nil {
			return err
		}
		recordLatestAction(store, bucket, lead.ID, "send-message", actions)
	}
	return nil
}

func recordLatestAction(store *Store, bucket string, leadID string, action string, actions *[]DailyLeadAction) {
	state, err := store.Load()
	if err != nil {
		return
	}
	index := findLeadByID(state.Leads, leadID)
	if index < 0 {
		return
	}
	lead := state.Leads[index]
	result := string(lead.MessageStatus)
	var note *string
	if len(lead.SendAttempts) > 0 {
		last := lead.SendAttempts[len(lead.SendAttempts)-1]
		result = last.Status
		note = last.Note
	}
	*actions = append(*actions, DailyLeadAction{
		At:            time.Now(),
		Bucket:        bucket,
		LeadID:        lead.ID,
		Name:          lead.Name,
		ProfileURL:    lead.ProfileURL,
		LeadType:      lead.LeadType,
		MessageStatus: lead.MessageStatus,
		Action:        action,
		Result:        result,
		Note:          note,
	})
}

func normalizeDailyOptions(store *Store, options DailyOptions) DailyOptions {
	if options.Playwriter == "" {
		options.Playwriter = defaultPlaywriter
	}
	if options.CaptureScript == "" {
		options.CaptureScript = defaultCaptureScript
	}
	if options.MessageScript == "" {
		options.MessageScript = defaultMessageScript
	}
	if options.SavedSearchesScript == "" {
		options.SavedSearchesScript = defaultSavedSearchesScript
	}
	if options.SavedSearches == "" {
		options.SavedSearches = defaultSavedSearches
	}
	if options.TargetAgencies == 0 {
		options.TargetAgencies = 5
	}
	if options.TargetRecruiters == 0 {
		options.TargetRecruiters = 5
	}
	if options.PagesPerCapture == 0 {
		options.PagesPerCapture = 2
	}
	if options.Limit == 0 {
		options.Limit = 25
	}
	if options.RowScrollDelayMS == 0 {
		options.RowScrollDelayMS = 250
	}
	if options.MaxCaptureRounds == 0 {
		options.MaxCaptureRounds = 4
	}
	if options.CaptureOutDir == "" {
		options.CaptureOutDir = defaultCaptureOutDir
	}
	if options.MessageOutDir == "" {
		options.MessageOutDir = defaultMessageOutDir
	}
	if options.DashboardPath == "" {
		options.DashboardPath = store.DefaultDailyDashboardPath()
	}
	if options.TimeoutMS == 0 {
		options.TimeoutMS = 90000
	}
	return options
}

func bucketCompleteForRun(state OutreachState, bucket string, target int, allowSend bool, actions []DailyLeadAction) bool {
	if allowSend {
		return sentCountFromActions(actions, bucket) >= target
	}
	return readyCount(state, bucket) >= target
}

func readyCount(state OutreachState, bucket string) int {
	return len(readyLeads(state, bucket))
}

func sentCountFromActions(actions []DailyLeadAction, bucket string) int {
	count := 0
	for _, action := range actions {
		if action.Bucket == bucket && action.Result == "sent-clicked" {
			count++
		}
	}
	return count
}

func leadsForMessageValidation(state OutreachState, bucket string) []Lead {
	leads := []Lead{}
	for _, lead := range state.Leads {
		if lead.Status != LeadStatusEligible || bucketForLead(lead) != bucket || lead.ProfileURL == nil || lead.Draft == nil {
			continue
		}
		if lead.MessageStatus != MessageStatusDrafted {
			continue
		}
		leads = append(leads, lead)
	}
	sortLeads(leads)
	return leads
}

func readyLeads(state OutreachState, bucket string) []Lead {
	leads := []Lead{}
	for _, lead := range state.Leads {
		if lead.Status == LeadStatusEligible && bucketForLead(lead) == bucket && lead.MessageStatus == MessageStatusDryRunReady {
			leads = append(leads, lead)
		}
	}
	sortLeads(leads)
	return leads
}

func safePathSegment(value string) string {
	cleaned := strings.ToLower(cleanText(value))
	cleaned = strings.NewReplacer("/", "-", "\\", "-", " ", "-", ":", "-").Replace(cleaned)
	return cleaned
}

type salesNavFilter struct {
	Type   string
	Values []salesNavFilterValue
}

type salesNavFilterValue struct {
	ID   string
	Text string
}

func defaultOutreachSourceURL(source string) (string, bool) {
	base := []salesNavFilter{
		{Type: "REGION", Values: []salesNavFilterValue{{ID: "103644278", Text: "United States"}}},
		{Type: "RELATIONSHIP", Values: []salesNavFilterValue{{ID: "S", Text: "2nd degree connections"}}},
		{Type: "POSTED_ON_LINKEDIN", Values: []salesNavFilterValue{{ID: "RPOL", Text: "Posted on LinkedIn"}}},
	}
	contractRecruiterTitles := salesNavFilter{Type: "CURRENT_TITLE", Values: []salesNavFilterValue{
		{ID: "1711", Text: "Contract Recruiter"},
		{ID: "8379", Text: "Senior Contract Recruiter"},
		{ID: "16659", Text: "Contract Technical Recruiter"},
		{ID: "21060", Text: "Senior Technical Recruiter Contract"},
	}}
	agencyLeaderTitles := salesNavFilter{Type: "CURRENT_TITLE", Values: []salesNavFilterValue{
		{ID: "35", Text: "Founder"},
		{ID: "103", Text: "Co-Founder"},
		{ID: "1", Text: "Owner"},
		{ID: "18", Text: "Partner"},
		{ID: "154", Text: "Managing Partner"},
		{ID: "182", Text: "Principal Consultant"},
		{ID: "200", Text: "Technical Director"},
	}}
	agencyIndustries := salesNavFilter{Type: "INDUSTRY", Values: []salesNavFilterValue{
		{ID: "4", Text: "Software Development"},
		{ID: "96", Text: "IT Services and IT Consulting"},
		{ID: "99", Text: "Design Services"},
	}}

	switch source {
	case RecruiterSource:
		return salesNavPeopleSearchURL(appendSalesNavFilters(base, contractRecruiterTitles), ""), true
	case AgencySource:
		return salesNavPeopleSearchURL(appendSalesNavFilters(base, agencyLeaderTitles, agencyIndustries), "digital agency"), true
	case AgencySoftwareConsultingSource:
		return salesNavPeopleSearchURL(appendSalesNavFilters(base, agencyLeaderTitles, agencyIndustries), "software consulting"), true
	case AgencyDevelopmentAgencySource:
		return salesNavPeopleSearchURL(appendSalesNavFilters(base, agencyLeaderTitles), "development agency"), true
	default:
		return "", false
	}
}

func appendSalesNavFilters(base []salesNavFilter, extra ...salesNavFilter) []salesNavFilter {
	filters := make([]salesNavFilter, 0, len(base)+len(extra))
	filters = append(filters, base...)
	filters = append(filters, extra...)
	return filters
}

func salesNavPeopleSearchURL(filters []salesNavFilter, keywords string) string {
	parts := make([]string, 0, len(filters))
	for _, filter := range filters {
		parts = append(parts, salesNavFilterExpression(filter))
	}
	body := fmt.Sprintf("filters:List(%s)", strings.Join(parts, ","))
	if cleanText(keywords) != "" {
		body += ",keywords:" + salesNavValueEscape(keywords)
	}
	query := fmt.Sprintf("(%s)", body)
	return "https://www.linkedin.com/sales/search/people?query=" + url.QueryEscape(query)
}

func salesNavFilterExpression(filter salesNavFilter) string {
	values := make([]string, 0, len(filter.Values))
	for _, value := range filter.Values {
		values = append(values, fmt.Sprintf(
			"(id:%s,text:%s,selectionType:INCLUDED)",
			salesNavValueEscape(value.ID),
			salesNavValueEscape(value.Text),
		))
	}
	return fmt.Sprintf("(type:%s,values:List(%s))", filter.Type, strings.Join(values, ","))
}

func salesNavValueEscape(value string) string {
	return strings.ReplaceAll(url.QueryEscape(value), "+", "%20")
}
