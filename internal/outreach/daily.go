package outreach

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/hanifcarroll/linkedin-network-run/internal/app"
)

var errBlankLeadPageValidation = errors.New("sales navigator lead page rendered blank during validation")
var errAgencyNoProgressStop = errors.New("agency contact search stopped after no-progress threshold")

const (
	RecruiterSource                = "ASAP - Contract Recruiter Titles"
	AgencySource                   = "ASAP - Agency Digital Agency Leaders"
	AgencySoftwareConsultingSource = "ASAP - Agency Software Consulting Leaders"
	AgencyDevelopmentAgencySource  = "ASAP - Agency Development Agency Leaders"
	AgencyProductStudioSource      = "ASAP - Agency Product Studio Leaders"
	AgencyAccountSource            = "ASAP - Agency Accounts Digital Agency"
	AgencyAccountDevelopmentSource = "ASAP - Agency Accounts Development Agency"
	AgencyAccountProductSource     = "ASAP - Agency Accounts Product Studio"
	AgencyAccountContactsSource    = "ASAP - Agency Account Contacts"
)

type DailyOptions struct {
	RunID                  string
	Command                string
	Args                   []string
	Session                string
	Playwriter             string
	CaptureScript          string
	AccountCaptureScript   string
	MessageScript          string
	SavedSearchesScript    string
	SavedSearches          string
	TargetAgencies         int
	TargetRecruiters       int
	PagesPerCapture        uint32
	AccountPagesPerCapture uint32
	Limit                  uint32
	AccountLimit           uint32
	StopAfterConnectable   uint32
	RowScrollDelayMS       uint32
	MaxCaptureRounds       int
	AllowSend              bool
	RefreshSavedSearches   bool
	SkipSessionReset       bool
	CaptureOutDir          string
	AccountCaptureOutDir   string
	MessageOutDir          string
	DashboardPath          string
	PrintMarkdown          bool
	TimeoutMS              uint32
	StopWhenNoProgress     bool
	MaxNoProgressSearches  int
}

type DailyResult struct {
	Report        DashboardReport `json:"report"`
	Summary       RunSummary      `json:"summary"`
	DashboardPath string          `json:"dashboard_path"`
	Markdown      string          `json:"markdown"`
}

type dailyBucket struct {
	Name    string
	Sources []string
	Target  int
}

type dailyProgress struct {
	AgencyTarget                 int
	AgencyContactSearches        int
	AgencyZeroContactSearches    int
	AgencyNoProgressSearches     int
	AgencyNoProgressStreak       int
	AgencyContactsFound          int
	AgencyAccountsCaptured       int
	AgencyAccountSourcesCaptured int
}

func RunDaily(store *Store, options DailyOptions) (DailyResult, error) {
	options = normalizeDailyOptions(store, options)
	if strings.TrimSpace(options.Session) == "" {
		return DailyResult{}, fmt.Errorf("--session is required")
	}
	resolvedSession, err := resolvePlaywriterSession(options.Playwriter, options.Session)
	if err != nil {
		return DailyResult{}, err
	}
	options.Session = resolvedSession
	startedAt := time.Now()
	if err := appendRunLifecycleEvent(store, RunEvent{
		At:               startedAt,
		RunID:            options.RunID,
		Phase:            "run-start",
		Command:          options.Command,
		Args:             options.Args,
		StatePath:        store.StatePath(),
		DashboardPath:    options.DashboardPath,
		TargetAgencies:   options.TargetAgencies,
		TargetRecruiters: options.TargetRecruiters,
		AllowSend:        options.AllowSend,
		StartedAt:        startedAt,
	}); err != nil {
		return DailyResult{}, err
	}
	actions := []DailyLeadAction{}
	runBlocker := ""
	finishRun := func(result string, blocker string, dashboardPath string) {
		completedAt := time.Now()
		_ = appendRunLifecycleEvent(store, RunEvent{
			At:               completedAt,
			RunID:            options.RunID,
			Phase:            "run-finish",
			Command:          options.Command,
			Args:             options.Args,
			Result:           result,
			StatePath:        store.StatePath(),
			DashboardPath:    dashboardPath,
			TargetAgencies:   options.TargetAgencies,
			TargetRecruiters: options.TargetRecruiters,
			AllowSend:        options.AllowSend,
			StartedAt:        startedAt,
			CompletedAt:      completedAt,
			Blocker:          blocker,
		})
	}
	if !options.SkipSessionReset {
		if err := app.ResetPlaywriterSession(options.Playwriter, options.Session); err != nil {
			finishRun("failed", err.Error(), options.DashboardPath)
			return DailyResult{}, err
		}
	}
	buckets := dailyBuckets(options)
	if dailySourcesNeedSavedSearches(buckets) {
		if err := EnsureSavedSearches(options); err != nil {
			finishRun("failed", err.Error(), options.DashboardPath)
			return DailyResult{}, err
		}
	}
bucketLoop:
	for _, bucket := range buckets {
		if bucket.Target <= 0 {
			continue
		}
		if bucket.Name == "agency" {
			if err := runAgencyAccountBucket(store, options, bucket, &actions); err != nil {
				if errors.Is(err, errBlankLeadPageValidation) || errors.Is(err, errAgencyNoProgressStop) {
					runBlocker = err.Error()
					break bucketLoop
				}
				finishRun("failed", err.Error(), options.DashboardPath)
				return DailyResult{}, err
			}
			continue
		}
		if len(bucket.Sources) == 0 {
			finishRun("failed", fmt.Sprintf("daily bucket %q has no sources", bucket.Name), options.DashboardPath)
			return DailyResult{}, fmt.Errorf("daily bucket %q has no sources", bucket.Name)
		}
		for round := 0; round < options.MaxCaptureRounds; round++ {
			for _, source := range bucket.Sources {
				state, err := store.Load()
				if err != nil {
					finishRun("failed", err.Error(), options.DashboardPath)
					return DailyResult{}, err
				}
				if bucketCompleteForRun(state, bucket.Name, bucket.Target, options.AllowSend, actions) {
					break
				}
				if err := captureSource(store, options, source, round+1); err != nil {
					finishRun("failed", err.Error(), options.DashboardPath)
					return DailyResult{}, err
				}
				state, err = store.Load()
				if err != nil {
					finishRun("failed", err.Error(), options.DashboardPath)
					return DailyResult{}, err
				}
				DraftMessages(&state, 0)
				if err := store.Save(state); err != nil {
					finishRun("failed", err.Error(), options.DashboardPath)
					return DailyResult{}, err
				}
				if err := validateBucket(store, options, bucket.Name, bucket.Target, &actions); err != nil {
					if errors.Is(err, errBlankLeadPageValidation) {
						runBlocker = err.Error()
						break bucketLoop
					}
					finishRun("failed", err.Error(), options.DashboardPath)
					return DailyResult{}, err
				}
				if options.AllowSend {
					if err := sendBucket(store, options, bucket.Name, bucket.Target, &actions); err != nil {
						finishRun("failed", err.Error(), options.DashboardPath)
						return DailyResult{}, err
					}
				}
				state, err = store.Load()
				if err != nil {
					finishRun("failed", err.Error(), options.DashboardPath)
					return DailyResult{}, err
				}
				if bucketCompleteForRun(state, bucket.Name, bucket.Target, options.AllowSend, actions) {
					break
				}
			}
			state, err := store.Load()
			if err != nil {
				finishRun("failed", err.Error(), options.DashboardPath)
				return DailyResult{}, err
			}
			if bucketCompleteForRun(state, bucket.Name, bucket.Target, options.AllowSend, actions) {
				break
			}
		}
	}
	state, err := store.Load()
	if err != nil {
		finishRun("failed", err.Error(), options.DashboardPath)
		return DailyResult{}, err
	}
	completedAt := time.Now()
	recommendation := RunRecommendation{}
	reportStatus := "completed"
	if runBlocker != "" {
		reportStatus = "blocked"
	}
	report := BuildDashboardReportWithOptions(state, store.StatePath(), DashboardBuildOptions{
		Mode:             "run",
		RunID:            options.RunID,
		RunStartedAt:     &startedAt,
		RunCompletedAt:   &completedAt,
		DashboardPath:    options.DashboardPath,
		TargetAgencies:   options.TargetAgencies,
		TargetRecruiters: options.TargetRecruiters,
		AllowSend:        options.AllowSend,
		Actions:          actions,
		Recommendation:   &recommendation,
	})
	report.Recommendation = RecommendNextRunSummary(RunSummary{
		RunID:            options.RunID,
		Command:          options.Command,
		Args:             options.Args,
		StartedAt:        startedAt,
		CompletedAt:      completedAt,
		Status:           reportStatus,
		Blocker:          runBlocker,
		DashboardPath:    options.DashboardPath,
		StatePath:        store.StatePath(),
		TargetAgencies:   options.TargetAgencies,
		TargetRecruiters: options.TargetRecruiters,
		AllowSend:        options.AllowSend,
		Counts:           dashboardRunCounts(actions),
		Actions:          actions,
	})
	markdown := RenderDashboardMarkdown(report)
	if err := WriteDashboardMarkdownAliases([]string{options.DashboardPath, store.LatestRunDashboardPath(), store.DefaultDailyDashboardPath()}, report); err != nil {
		finishRun("failed", err.Error(), options.DashboardPath)
		return DailyResult{}, err
	}
	finishRun(reportStatus, runBlocker, options.DashboardPath)
	state, err = store.Load()
	if err != nil {
		return DailyResult{}, err
	}
	summary, ok := LatestRunSummary(state, store.StatePath())
	if !ok || summary.RunID != options.RunID {
		summary = RunSummary{
			RunID:            options.RunID,
			Command:          options.Command,
			Args:             options.Args,
			StartedAt:        startedAt,
			CompletedAt:      completedAt,
			Status:           reportStatus,
			Blocker:          runBlocker,
			DashboardPath:    options.DashboardPath,
			StatePath:        store.StatePath(),
			TargetAgencies:   options.TargetAgencies,
			TargetRecruiters: options.TargetRecruiters,
			AllowSend:        options.AllowSend,
			Counts:           dashboardRunCounts(actions),
			Actions:          actions,
		}
		summary.Recommendation = RecommendNextRunSummary(summary)
	}
	return DailyResult{Report: report, Summary: summary, DashboardPath: options.DashboardPath, Markdown: markdown}, nil
}

func dailyBuckets(options DailyOptions) []dailyBucket {
	return []dailyBucket{
		{
			Name:    "agency",
			Sources: []string{},
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
		TimeoutMS:            options.TimeoutMS,
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

func runAgencyAccountBucket(store *Store, options DailyOptions, bucket dailyBucket, actions *[]DailyLeadAction) error {
	progress := &dailyProgress{AgencyTarget: bucket.Target}
	for round := 0; round < options.MaxCaptureRounds; round++ {
		if err := retireStaleAgencyAccounts(store); err != nil {
			return err
		}
		state, err := store.Load()
		if err != nil {
			return err
		}
		if bucketCompleteForRun(state, bucket.Name, bucket.Target, options.AllowSend, *actions) {
			return nil
		}
		printAgencyProgress(state, *progress, *actions, "round-start", fmt.Sprintf("round=%d", round+1))
		if err := ensureAgencyAccountReservoir(store, options, bucket.Target, round+1, progress); err != nil {
			return err
		}
		captured, err := captureAgencyContactsFromAccounts(store, options, bucket.Target, round+1, progress)
		if captured > 0 {
			if err := draftValidateAndMaybeSendBucket(store, options, bucket.Name, bucket.Target, actions); err != nil {
				return err
			}
		}
		state, loadErr := store.Load()
		if loadErr == nil {
			printAgencyProgress(state, *progress, *actions, "round-complete", fmt.Sprintf("round=%d captured_rows=%d", round+1, captured))
		}
		if err != nil {
			return err
		}
	}
	return nil
}

func retireStaleAgencyAccounts(store *Store) error {
	state, err := store.Load()
	if err != nil {
		return err
	}
	changed := false
	now := time.Now()
	for i := range state.AgencyAccounts {
		account := &state.AgencyAccounts[i]
		if account.Status != AgencyAccountStatusQualified || account.ContactCaptureCount < agencyAccountContactStrategyCount(*account) {
			continue
		}
		if agencyAccountOpenLeadCount(state, account.ID) > 0 {
			continue
		}
		account.Status = AgencyAccountStatusExhausted
		account.UpdatedAt = now
		changed = true
	}
	if !changed {
		return nil
	}
	return store.Save(state)
}

func draftValidateAndMaybeSendBucket(store *Store, options DailyOptions, bucket string, target int, actions *[]DailyLeadAction) error {
	state, err := store.Load()
	if err != nil {
		return err
	}
	DraftMessages(&state, 0)
	if err := store.Save(state); err != nil {
		return err
	}
	if err := validateBucket(store, options, bucket, target, actions); err != nil {
		return err
	}
	if options.AllowSend {
		return sendBucket(store, options, bucket, target, actions)
	}
	return nil
}

func ensureAgencyAccountReservoir(store *Store, options DailyOptions, target int, round int, progress *dailyProgress) error {
	state, err := store.Load()
	if err != nil {
		return err
	}
	desired := target * 2
	if desired < target {
		desired = target
	}
	if len(agencyAccountsNeedingContactCapture(state, desired)) >= target {
		return nil
	}
	for _, source := range defaultAgencyAccountSources() {
		stored, err := captureAgencyAccountSource(store, options, source, round)
		if err != nil {
			return err
		}
		progress.AgencyAccountSourcesCaptured++
		progress.AgencyAccountsCaptured += stored
		state, err = store.Load()
		if err != nil {
			return err
		}
		printAgencyProgress(state, *progress, nil, "account-reservoir", fmt.Sprintf("source=%q stored_or_updated=%d needing_contact=%d", source, stored, len(agencyAccountsNeedingContactCapture(state, desired))))
		if len(agencyAccountsNeedingContactCapture(state, desired)) >= desired {
			return nil
		}
	}
	return nil
}

func captureAgencyAccountSource(store *Store, options DailyOptions, source string, round int) (int, error) {
	state, err := store.Load()
	if err != nil {
		return 0, err
	}
	var explicitURL *string
	if cursor, ok := state.CaptureCursors[source]; ok && cursor.ResumeURL != nil {
		explicitURL = cursor.ResumeURL
	}
	captureURL, err := resolveDailyAccountCaptureURL(explicitURL, options.SavedSearches, source)
	if err != nil {
		return 0, err
	}
	outDir := filepath.Join(options.AccountCaptureOutDir, safePathSegment(source), fmt.Sprintf("round-%02d", round))
	path, err := RunPlaywriterAccountCapture(options.Playwriter, options.Session, options.AccountCaptureScript, outDir, source, captureURL, AccountCaptureRunOptions{
		Pages:            options.AccountPagesPerCapture,
		Limit:            options.AccountLimit,
		RowScrollDelayMS: options.RowScrollDelayMS,
		TimeoutMS:        options.TimeoutMS,
	})
	if err != nil {
		return 0, err
	}
	capture, err := LoadSalesNavAccountCapture(path)
	if err != nil {
		return 0, err
	}
	state, err = store.Load()
	if err != nil {
		return 0, err
	}
	summary, err := ImportAccountCapture(&state, capture)
	if err != nil {
		return 0, err
	}
	if err := store.Save(state); err != nil {
		return 0, err
	}
	return summary.Stored + summary.Updated, nil
}

func captureAgencyContactsFromAccounts(store *Store, options DailyOptions, target int, round int, progress *dailyProgress) (int, error) {
	state, err := store.Load()
	if err != nil {
		return 0, err
	}
	needed := target - readyCount(state, "agency")
	if needed <= 0 {
		return 0, nil
	}
	accounts := agencyAccountsNeedingContactCapture(state, agencyContactAccountLimit(needed))
	capturedContacts := 0
	for _, account := range accounts {
		if account.AccountURL == nil {
			continue
		}
		strategy, ok := nextAgencyContactSearchStrategy(account)
		if !ok {
			continue
		}
		contactURL, err := agencyAccountContactSearchURLForStrategy(account, strategy)
		if err != nil {
			continue
		}
		printAgencyProgress(state, *progress, nil, "contact-search-start", fmt.Sprintf("account=%q strategy=%s", account.Name, strategy.Name))
		source := agencyContactSource(account, strategy)
		outDir := filepath.Join(options.CaptureOutDir, safePathSegment(AgencyAccountContactsSource), safePathSegment(account.ID), safePathSegment(strategy.Name), fmt.Sprintf("round-%02d", round))
		path, err := app.RunPlaywriterCapture(options.Playwriter, options.Session, options.CaptureScript, outDir, source, contactURL, app.CaptureRunOptions{
			Pages:                options.PagesPerCapture,
			StopAfterConnectable: options.StopAfterConnectable,
			Limit:                options.Limit,
			RowScrollDelayMS:     options.RowScrollDelayMS,
			OnlyConnectable:      false,
			TimeoutMS:            options.TimeoutMS,
		})
		if err != nil {
			_ = recordAgencyContactCaptureError(store, account.ID, strategy, err)
			return capturedContacts, err
		}
		capture, err := app.LoadSalesNavCapture(path)
		if err != nil {
			_ = recordAgencyContactCaptureError(store, account.ID, strategy, err)
			return capturedContacts, err
		}
		state, err = store.Load()
		if err != nil {
			return capturedContacts, err
		}
		index := findAgencyAccountByID(state.AgencyAccounts, account.ID)
		if index < 0 {
			continue
		}
		accountForImport := state.AgencyAccounts[index]
		openBefore := agencyAccountOpenLeadCount(state, account.ID)
		if _, err := ImportCapture(&state, capture, ImportOptions{AgencyAccount: &accountForImport}); err != nil {
			return capturedContacts, err
		}
		openAfter := agencyAccountOpenLeadCount(state, account.ID)
		newOpenContacts := openAfter - openBefore
		if newOpenContacts < 0 {
			newOpenContacts = 0
		}
		now := time.Now()
		state.AgencyAccounts[index].ContactCaptureCount++
		state.AgencyAccounts[index].LastContactCaptureAt = &now
		state.AgencyAccounts[index].LastContactStrategy = &strategy.Name
		state.AgencyAccounts[index].LastContactError = nil
		state.AgencyAccounts[index].LastContactErrorAt = nil
		state.AgencyAccounts[index].UpdatedAt = now
		if openAfter <= openBefore && state.AgencyAccounts[index].ContactCaptureCount >= agencyAccountContactStrategyCount(state.AgencyAccounts[index]) {
			state.AgencyAccounts[index].Status = AgencyAccountStatusExhausted
		}
		if err := store.Save(state); err != nil {
			return capturedContacts, err
		}
		capturedContacts += len(capture.Rows)
		progress.AgencyContactSearches++
		if len(capture.Rows) == 0 {
			progress.AgencyZeroContactSearches++
		}
		if newOpenContacts == 0 {
			progress.AgencyNoProgressSearches++
			progress.AgencyNoProgressStreak++
		} else {
			progress.AgencyNoProgressStreak = 0
			progress.AgencyContactsFound += newOpenContacts
		}
		state, err = store.Load()
		if err != nil {
			return capturedContacts, err
		}
		printAgencyProgress(state, *progress, nil, "contact-search-complete", fmt.Sprintf("account=%q strategy=%s rows=%d new_contacts=%d", account.Name, strategy.Name, len(capture.Rows), newOpenContacts))
		if shouldStopForAgencyNoProgress(options, progress) {
			return capturedContacts, fmt.Errorf("%w: %d consecutive account contact searches produced no new contacts", errAgencyNoProgressStop, progress.AgencyNoProgressStreak)
		}
		if readyCount(state, "agency") >= target {
			return capturedContacts, nil
		}
	}
	return capturedContacts, nil
}

func shouldStopForAgencyNoProgress(options DailyOptions, progress *dailyProgress) bool {
	if !options.StopWhenNoProgress {
		return false
	}
	threshold := options.MaxNoProgressSearches
	if threshold <= 0 {
		threshold = 12
	}
	return progress.AgencyNoProgressStreak >= threshold
}

func printAgencyProgress(state OutreachState, progress dailyProgress, actions []DailyLeadAction, phase string, detail string) {
	state.Normalize()
	sent := sentCountFromActions(actions, "agency")
	if sent == 0 {
		sent = dashboardRunCounts(actions).Sent.Agencies
	}
	fmt.Printf(
		"progress agency phase=%s sent=%d/%d ready=%d searches=%d zero_contact=%d no_progress_streak=%d contacts_found=%d qualified=%d exhausted=%d detail=%s\n",
		phase,
		sent,
		progress.AgencyTarget,
		readyCount(state, "agency"),
		progress.AgencyContactSearches,
		progress.AgencyZeroContactSearches,
		progress.AgencyNoProgressStreak,
		progress.AgencyContactsFound,
		Counts(state).ByAgencyAccountStatus[AgencyAccountStatusQualified],
		Counts(state).ByAgencyAccountStatus[AgencyAccountStatusExhausted],
		cleanText(detail),
	)
}

func recordAgencyContactCaptureError(store *Store, accountID string, strategy agencyContactSearchStrategy, cause error) error {
	state, err := store.Load()
	if err != nil {
		return err
	}
	index := findAgencyAccountByID(state.AgencyAccounts, accountID)
	if index < 0 {
		return nil
	}
	now := time.Now()
	message := cause.Error()
	if len(message) > 240 {
		message = message[:240]
	}
	account := &state.AgencyAccounts[index]
	account.LastContactCaptureAt = &now
	account.LastContactStrategy = &strategy.Name
	account.LastContactError = &message
	account.LastContactErrorAt = &now
	account.ContactErrorCount++
	account.UpdatedAt = now
	return store.Save(state)
}

func agencyAccountOpenLeadCount(state OutreachState, accountID string) int {
	count := 0
	for _, lead := range state.Leads {
		if lead.AgencyAccountID == nil || *lead.AgencyAccountID != accountID {
			continue
		}
		if lead.Status != LeadStatusEligible {
			continue
		}
		if isTerminalMessageStatus(lead.MessageStatus) && lead.MessageStatus != MessageStatusDryRunReady {
			continue
		}
		count++
	}
	return count
}

func agencyContactAccountLimit(needed int) int {
	limit := needed * 2
	if limit < 5 {
		return 5
	}
	return limit
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

func resolveDailyAccountCaptureURL(explicitURL *string, savedSearches string, source string) (string, error) {
	if explicitURL != nil && cleanText(*explicitURL) != "" {
		return app.ResolveCaptureURL(explicitURL, savedSearches, source, "--url")
	}
	if generatedURL, ok := defaultOutreachAccountSourceURL(source); ok {
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
			RunID:      options.RunID,
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
		recordLatestAction(store, options.RunID, bucket, lead.ID, "dry-run-message", actions)
		if latestAttemptIsBlankLeadPageFailure(store, lead.ID) {
			return errBlankLeadPageValidation
		}
	}
}

func sendBucket(store *Store, options DailyOptions, bucket string, target int, actions *[]DailyLeadAction) error {
	for {
		state, err := store.Load()
		if err != nil {
			return err
		}
		if sentCountFromActions(*actions, bucket) >= target {
			return nil
		}
		candidates := readyLeads(state, bucket)
		if len(candidates) == 0 {
			return nil
		}
		lead := candidates[0]
		if err := SendMessage(store, SendMessageOptions{
			LeadID:     lead.ID,
			RunID:      options.RunID,
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
		recordLatestAction(store, options.RunID, bucket, lead.ID, "send-message", actions)
	}
	return nil
}

func recordLatestAction(store *Store, runID string, bucket string, leadID string, action string, actions *[]DailyLeadAction) {
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
	at := time.Now()
	var note *string
	if len(lead.SendAttempts) > 0 {
		last := lead.SendAttempts[len(lead.SendAttempts)-1]
		result = last.Status
		note = last.Note
		if !last.At.IsZero() {
			at = last.At
		}
	}
	*actions = append(*actions, DailyLeadAction{
		At:            at,
		RunID:         runID,
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

func latestAttemptIsBlankLeadPageFailure(store *Store, leadID string) bool {
	state, err := store.Load()
	if err != nil {
		return false
	}
	index := findLeadByID(state.Leads, leadID)
	if index < 0 || len(state.Leads[index].SendAttempts) == 0 {
		return false
	}
	attempt := state.Leads[index].SendAttempts[len(state.Leads[index].SendAttempts)-1]
	if attempt.Status != "identity-mismatch" || cleanText(attempt.OutPath) == "" {
		return false
	}
	result, err := LoadMessageSendResult(attempt.OutPath)
	if err != nil {
		return false
	}
	return result.Status == "identity-mismatch" && result.Body != nil && cleanText(*result.Body) == ""
}

func normalizeDailyOptions(store *Store, options DailyOptions) DailyOptions {
	if options.RunID == "" {
		options.RunID = newRunID("daily")
	}
	if options.Command == "" {
		options.Command = "run-daily"
	}
	if options.Playwriter == "" {
		options.Playwriter = defaultPlaywriter
	}
	if options.CaptureScript == "" {
		options.CaptureScript = defaultCaptureScript
	}
	if options.AccountCaptureScript == "" {
		options.AccountCaptureScript = defaultAccountCaptureScript
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
	if options.TargetAgencies < 0 {
		options.TargetAgencies = 0
	}
	if options.TargetRecruiters < 0 {
		options.TargetRecruiters = 0
	}
	if options.PagesPerCapture == 0 {
		options.PagesPerCapture = 2
	}
	if options.AccountPagesPerCapture == 0 {
		options.AccountPagesPerCapture = 2
	}
	if options.Limit == 0 {
		options.Limit = 25
	}
	if options.AccountLimit == 0 {
		options.AccountLimit = 25
	}
	if options.RowScrollDelayMS == 0 {
		options.RowScrollDelayMS = 250
	}
	if options.MaxCaptureRounds == 0 {
		options.MaxCaptureRounds = 4
	}
	if options.CaptureOutDir == "" {
		options.CaptureOutDir = filepath.Join(defaultCaptureOutDir, options.RunID)
	}
	if options.AccountCaptureOutDir == "" {
		options.AccountCaptureOutDir = filepath.Join(defaultAccountCaptureOutDir, options.RunID)
	}
	if options.MessageOutDir == "" {
		options.MessageOutDir = filepath.Join(defaultMessageOutDir, options.RunID)
	}
	if options.DashboardPath == "" {
		options.DashboardPath = store.RunDashboardPath(options.RunID)
	}
	if options.TimeoutMS == 0 {
		options.TimeoutMS = 90000
	}
	if options.MaxNoProgressSearches <= 0 {
		options.MaxNoProgressSearches = 12
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

func sentCount(state OutreachState, bucket string) int {
	count := 0
	for _, lead := range state.Leads {
		if lead.Status == LeadStatusEligible && bucketForLead(lead) == bucket && lead.MessageStatus == MessageStatusSent {
			count++
		}
	}
	return count
}

func leadsForMessageValidation(state OutreachState, bucket string) []Lead {
	leads := []Lead{}
	for _, lead := range state.Leads {
		if !leadMatchesSendableBucket(state, lead, bucket) || lead.ProfileURL == nil || lead.Draft == nil {
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
		if leadMatchesSendableBucket(state, lead, bucket) && lead.MessageStatus == MessageStatusDryRunReady {
			leads = append(leads, lead)
		}
	}
	sortLeads(leads)
	return leads
}

func leadMatchesSendableBucket(state OutreachState, lead Lead, bucket string) bool {
	if lead.Status != LeadStatusEligible || bucketForLead(lead) != bucket {
		return false
	}
	if bucket != "agency" {
		return true
	}
	return leadHasQualifiedAgencyAccount(state, lead)
}

func leadHasQualifiedAgencyAccount(state OutreachState, lead Lead) bool {
	if lead.AgencyAccountID == nil || cleanText(*lead.AgencyAccountID) == "" {
		return false
	}
	index := findAgencyAccountByID(state.AgencyAccounts, cleanText(*lead.AgencyAccountID))
	return index >= 0 && state.AgencyAccounts[index].Status == AgencyAccountStatusQualified
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

type agencyContactSearchStrategy struct {
	Name                  string
	TitleFilter           *salesNavFilter
	Keywords              string
	IncludeRecentActivity bool
	StrongAccountOnly     bool
}

func defaultOutreachSourceURL(source string) (string, bool) {
	switch source {
	case RecruiterSource:
		return salesNavPeopleSearchURL(appendSalesNavFilters(basePeopleFilters(), contractRecruiterTitleFilter()), ""), true
	case AgencySource:
		return salesNavPeopleSearchURL(appendSalesNavFilters(basePeopleFilters(), agencyLeaderTitleFilter(), agencyIndustryFilter()), "digital agency"), true
	case AgencySoftwareConsultingSource:
		return salesNavPeopleSearchURL(appendSalesNavFilters(basePeopleFilters(), agencyLeaderTitleFilter(), agencyIndustryFilter()), "software consulting"), true
	case AgencyDevelopmentAgencySource:
		return salesNavPeopleSearchURL(appendSalesNavFilters(basePeopleFilters(), agencyLeaderTitleFilter()), "development agency"), true
	case AgencyProductStudioSource:
		return salesNavPeopleSearchURL(appendSalesNavFilters(basePeopleFilters(), agencyLeaderTitleFilter(), agencyIndustryFilter()), "product studio"), true
	default:
		return "", false
	}
}

func defaultAgencyAccountSources() []string {
	return []string{AgencyAccountDevelopmentSource, AgencyAccountSource, AgencyAccountProductSource}
}

func defaultOutreachAccountSourceURL(source string) (string, bool) {
	base := []salesNavFilter{
		{Type: "REGION", Values: []salesNavFilterValue{{ID: "103644278", Text: "United States"}}},
		agencyIndustryFilter(),
		{Type: "COMPANY_HEADCOUNT", Values: []salesNavFilterValue{
			{ID: "C", Text: "11-50"},
			{ID: "D", Text: "51-200"},
			{ID: "E", Text: "201-500"},
		}},
	}
	switch source {
	case AgencyAccountSource:
		return salesNavAccountSearchURL(base, "digital product agency"), true
	case AgencyAccountDevelopmentSource:
		return salesNavAccountSearchURL(base, "custom software development agency"), true
	case AgencyAccountProductSource:
		return salesNavAccountSearchURL(base, "product studio"), true
	default:
		return "", false
	}
}

func agencyAccountContactSearchURL(account AgencyAccount) (string, error) {
	strategy, ok := firstAgencyContactSearchStrategy(account)
	if !ok {
		return "", fmt.Errorf("agency account %s has no contact search strategy", account.ID)
	}
	return agencyAccountContactSearchURLForStrategy(account, strategy)
}

func agencyAccountContactSearchURLForStrategy(account AgencyAccount, strategy agencyContactSearchStrategy) (string, error) {
	companyID := salesNavCompanyID(account)
	if companyID == "" {
		return "", fmt.Errorf("agency account %s has no Sales Navigator company id", account.ID)
	}
	company := salesNavFilter{Type: "CURRENT_COMPANY", Values: []salesNavFilterValue{{ID: companyID, Text: account.Name}}}
	filters := appendSalesNavFilters(basePeopleFiltersForRecentActivity(strategy.IncludeRecentActivity), company)
	if strategy.TitleFilter != nil {
		filters = appendSalesNavFilters(filters, *strategy.TitleFilter)
	}
	return salesNavPeopleSearchURL(filters, strategy.Keywords), nil
}

func agencyContactSource(account AgencyAccount, strategy agencyContactSearchStrategy) string {
	return cleanText(AgencyAccountContactsSource + " - " + account.Name + " - " + strategy.Name)
}

func firstAgencyContactSearchStrategy(account AgencyAccount) (agencyContactSearchStrategy, bool) {
	strategies := agencyContactSearchStrategies(account)
	if len(strategies) == 0 {
		return agencyContactSearchStrategy{}, false
	}
	return strategies[0], true
}

func nextAgencyContactSearchStrategy(account AgencyAccount) (agencyContactSearchStrategy, bool) {
	strategies := agencyContactSearchStrategies(account)
	if account.ContactCaptureCount < 0 || account.ContactCaptureCount >= len(strategies) {
		return agencyContactSearchStrategy{}, false
	}
	return strategies[account.ContactCaptureCount], true
}

func agencyAccountContactStrategyCount(account AgencyAccount) int {
	return len(agencyContactSearchStrategies(account))
}

func agencyContactSearchStrategies(account AgencyAccount) []agencyContactSearchStrategy {
	leaderFilter := agencyLeaderTitleFilter()
	strategies := []agencyContactSearchStrategy{
		{
			Name:                  "founder_recent",
			TitleFilter:           &leaderFilter,
			IncludeRecentActivity: true,
		},
		{
			Name:     "executive_delivery_broad",
			Keywords: "CEO President Managing Director Head of Engineering VP Engineering Technical Director Head of Delivery Client Services Partnerships",
		},
	}
	if agencyAccountAllowsResourceFallback(account) {
		strategies = append(strategies, agencyContactSearchStrategy{
			Name:                  "resource_delivery_broad",
			Keywords:              "Resource Manager Resourcing Talent Manager Head of Talent Client Services Partnerships Delivery",
			StrongAccountOnly:     true,
			IncludeRecentActivity: false,
		})
	}
	return strategies
}

func agencyAccountAllowsResourceFallback(account AgencyAccount) bool {
	return account.FitScore >= 75
}

func salesNavCompanyID(account AgencyAccount) string {
	if account.AccountURL == nil {
		return ""
	}
	raw := cleanText(*account.AccountURL)
	if parsed, err := url.Parse(raw); err == nil {
		path := strings.Trim(parsed.Path, "/")
		parts := strings.Split(path, "/")
		for i := 0; i+1 < len(parts); i++ {
			if parts[i] == "sales" && parts[i+1] == "company" && i+2 < len(parts) {
				return cleanText(parts[i+2])
			}
		}
	}
	marker := "/sales/company/"
	if index := strings.Index(raw, marker); index >= 0 {
		rest := raw[index+len(marker):]
		rest = strings.Split(strings.Split(rest, "?")[0], "#")[0]
		return strings.Trim(rest, "/")
	}
	return ""
}

func findAgencyAccountByID(accounts []AgencyAccount, id string) int {
	for i, account := range accounts {
		if account.ID == id {
			return i
		}
	}
	return -1
}

func basePeopleFilters() []salesNavFilter {
	return basePeopleFiltersForRecentActivity(true)
}

func basePeopleFiltersForRecentActivity(includeRecentActivity bool) []salesNavFilter {
	filters := []salesNavFilter{
		{Type: "REGION", Values: []salesNavFilterValue{{ID: "103644278", Text: "United States"}}},
		{Type: "RELATIONSHIP", Values: []salesNavFilterValue{{ID: "S", Text: "2nd degree connections"}}},
	}
	if includeRecentActivity {
		filters = append(filters, salesNavFilter{Type: "POSTED_ON_LINKEDIN", Values: []salesNavFilterValue{{ID: "RPOL", Text: "Posted on LinkedIn"}}})
	}
	return filters
}

func contractRecruiterTitleFilter() salesNavFilter {
	return salesNavFilter{Type: "CURRENT_TITLE", Values: []salesNavFilterValue{
		{ID: "1711", Text: "Contract Recruiter"},
		{ID: "8379", Text: "Senior Contract Recruiter"},
		{ID: "16659", Text: "Contract Technical Recruiter"},
		{ID: "21060", Text: "Senior Technical Recruiter Contract"},
	}}
}

func agencyLeaderTitleFilter() salesNavFilter {
	return salesNavFilter{Type: "CURRENT_TITLE", Values: []salesNavFilterValue{
		{ID: "35", Text: "Founder"},
		{ID: "103", Text: "Co-Founder"},
		{ID: "1", Text: "Owner"},
		{ID: "18", Text: "Partner"},
		{ID: "154", Text: "Managing Partner"},
		{ID: "182", Text: "Principal Consultant"},
		{ID: "200", Text: "Technical Director"},
	}}
}

func agencyIndustryFilter() salesNavFilter {
	return salesNavFilter{Type: "INDUSTRY", Values: []salesNavFilterValue{
		{ID: "4", Text: "Software Development"},
		{ID: "96", Text: "IT Services and IT Consulting"},
		{ID: "99", Text: "Design Services"},
	}}
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

func salesNavAccountSearchURL(filters []salesNavFilter, keywords string) string {
	parts := make([]string, 0, len(filters))
	for _, filter := range filters {
		parts = append(parts, salesNavFilterExpression(filter))
	}
	body := fmt.Sprintf("filters:List(%s)", strings.Join(parts, ","))
	if cleanText(keywords) != "" {
		body += ",keywords:" + salesNavValueEscape(keywords)
	}
	query := fmt.Sprintf("(%s)", body)
	return "https://www.linkedin.com/sales/search/company?query=" + url.QueryEscape(query)
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
