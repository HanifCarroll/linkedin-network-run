package outreach

import (
	"fmt"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type RunSummary struct {
	RunID            string             `json:"run_id"`
	Command          string             `json:"command"`
	Args             []string           `json:"args,omitempty"`
	StartedAt        time.Time          `json:"started_at"`
	CompletedAt      time.Time          `json:"completed_at,omitempty"`
	Status           string             `json:"status"`
	Blocker          string             `json:"blocker,omitempty"`
	DashboardPath    string             `json:"dashboard_path,omitempty"`
	StatePath        string             `json:"state_path,omitempty"`
	TargetAgencies   int                `json:"target_agencies"`
	TargetRecruiters int                `json:"target_recruiters"`
	AllowSend        bool               `json:"allow_send"`
	Counts           DashboardRunCounts `json:"counts"`
	Actions          []DailyLeadAction  `json:"actions,omitempty"`
	Recommendation   RunRecommendation  `json:"recommendation"`
}

type RunRecommendation struct {
	ShouldRetry bool   `json:"should_retry"`
	Command     string `json:"command,omitempty"`
	Reason      string `json:"reason"`
	Blocker     string `json:"blocker,omitempty"`
}

func normalizeRunID(value string, prefix string) string {
	cleaned := cleanText(value)
	if cleaned != "" {
		return cleaned
	}
	return newRunID(prefix)
}

func newRunID(prefix string) string {
	cleanPrefix := strings.ToLower(cleanText(prefix))
	if cleanPrefix == "" {
		cleanPrefix = "run"
	}
	cleanPrefix = strings.NewReplacer(" ", "-", "_", "-").Replace(cleanPrefix)
	return fmt.Sprintf("%s-%s", cleanPrefix, time.Now().Format("20060102T150405.000000000"))
}

func (s Store) RunDashboardPath(runID string) string {
	return filepath.Join(s.Dir, "dashboards", "runs", normalizeRunID(runID, "run")+".md")
}

func (s Store) LatestRunDashboardPath() string {
	return filepath.Join(s.Dir, "dashboards", "latest-run.md")
}

func (s Store) LatestRenderDashboardPath() string {
	return filepath.Join(s.Dir, "dashboards", "latest-render.md")
}

func appendRunLifecycleEvent(store *Store, event RunEvent) error {
	state, err := store.Load()
	if err != nil {
		return err
	}
	appendRunEvent(&state, event)
	return store.Save(state)
}

func LatestRunSummary(state OutreachState, statePath string) (RunSummary, bool) {
	state.Normalize()
	runs := runSummariesFromEvents(state.RunEvents, statePath)
	if len(runs) == 0 {
		return legacyRunSummaryFromEvents(state.RunEvents, statePath)
	}
	sort.SliceStable(runs, func(i, j int) bool {
		return effectiveRunTime(runs[i]).After(effectiveRunTime(runs[j]))
	})
	return runs[0], true
}

func legacyRunSummaryFromEvents(events []RunEvent, statePath string) (RunSummary, bool) {
	actions := []DailyLeadAction{}
	var startedAt time.Time
	var completedAt time.Time
	for _, event := range events {
		if event.Phase != "send-message" {
			continue
		}
		if startedAt.IsZero() || event.At.Before(startedAt) {
			startedAt = event.At
		}
		if completedAt.IsZero() || event.At.After(completedAt) {
			completedAt = event.At
		}
		actions = append(actions, runEventAction(event))
	}
	if len(actions) == 0 {
		return RunSummary{}, false
	}
	summary := RunSummary{
		RunID:            "legacy-" + completedAt.Format("20060102T150405"),
		Command:          "legacy-run-events",
		StartedAt:        startedAt,
		CompletedAt:      completedAt,
		Status:           "completed",
		StatePath:        statePath,
		TargetAgencies:   5,
		TargetRecruiters: 5,
		AllowSend:        true,
		Actions:          actions,
		Counts:           dashboardRunCounts(actions),
	}
	summary.Recommendation = RecommendNextRunSummary(summary)
	return summary, true
}

func runSummariesFromEvents(events []RunEvent, statePath string) []RunSummary {
	byID := map[string]*RunSummary{}
	for _, event := range events {
		if cleanText(event.RunID) == "" {
			continue
		}
		summary := byID[event.RunID]
		if summary == nil {
			summary = &RunSummary{
				RunID:     event.RunID,
				Status:    "running",
				StatePath: statePath,
			}
			byID[event.RunID] = summary
		}
		if !event.StartedAt.IsZero() {
			summary.StartedAt = event.StartedAt
		}
		if summary.StartedAt.IsZero() && event.Phase == "run-start" {
			summary.StartedAt = event.At
		}
		if !event.CompletedAt.IsZero() {
			summary.CompletedAt = event.CompletedAt
		}
		if event.Command != "" {
			summary.Command = event.Command
		}
		if len(event.Args) > 0 {
			summary.Args = append([]string(nil), event.Args...)
		}
		if event.DashboardPath != "" {
			summary.DashboardPath = event.DashboardPath
		}
		if event.StatePath != "" {
			summary.StatePath = event.StatePath
		}
		if event.TargetAgencies != 0 {
			summary.TargetAgencies = event.TargetAgencies
		}
		if event.TargetRecruiters != 0 {
			summary.TargetRecruiters = event.TargetRecruiters
		}
		if event.AllowSend {
			summary.AllowSend = true
		}
		if event.Blocker != "" {
			summary.Blocker = event.Blocker
		}
		if event.Phase == "send-message" {
			action := runEventAction(event)
			summary.Actions = append(summary.Actions, action)
		}
		if event.Phase == "run-finish" {
			if event.Result != "" {
				summary.Status = event.Result
			} else {
				summary.Status = "completed"
			}
		}
	}
	runs := make([]RunSummary, 0, len(byID))
	for _, summary := range byID {
		summary.Counts = dashboardRunCounts(summary.Actions)
		summary.Recommendation = RecommendNextRunSummary(*summary)
		runs = append(runs, *summary)
	}
	return runs
}

func effectiveRunTime(summary RunSummary) time.Time {
	if !summary.CompletedAt.IsZero() {
		return summary.CompletedAt
	}
	return summary.StartedAt
}

func runEventAction(event RunEvent) DailyLeadAction {
	return DailyLeadAction{
		At:         event.At,
		RunID:      event.RunID,
		Bucket:     event.Bucket,
		LeadID:     event.LeadID,
		Name:       event.Name,
		Action:     "send-message",
		Result:     event.Result,
		Note:       optionalString(event.Note),
		ProfileURL: nil,
	}
}

func optionalString(value string) *string {
	if cleanText(value) == "" {
		return nil
	}
	return &value
}

func RecommendNextRun(state OutreachState, statePath string, targetAgencies int, targetRecruiters int, allowSend bool) RunRecommendation {
	if summary, ok := LatestRunSummary(state, statePath); ok {
		if summary.TargetAgencies == 0 {
			summary.TargetAgencies = targetAgencies
		}
		if summary.TargetRecruiters == 0 {
			summary.TargetRecruiters = targetRecruiters
		}
		if allowSend {
			summary.AllowSend = true
		}
		return RecommendNextRunSummary(summary)
	}
	if targetAgencies > 0 {
		return RunRecommendation{
			ShouldRetry: true,
			Command:     retryCommand(targetAgencies, 0, allowSend),
			Reason:      "No previous run summary is available. Start with an agency-focused run if agency coverage is the open question.",
		}
	}
	return RunRecommendation{Reason: "No previous run summary is available."}
}

func RecommendNextRunSummary(summary RunSummary) RunRecommendation {
	agencyGap := nonZero(summary.TargetAgencies, 0) - summary.Counts.Sent.Agencies
	recruiterGap := nonZero(summary.TargetRecruiters, 0) - summary.Counts.Sent.Recruiters
	if summary.Status == "failed" || cleanText(summary.Blocker) != "" {
		targetAgencies := positiveOrFallback(agencyGap, nonZero(summary.TargetAgencies, 5))
		targetRecruiters := positiveOrFallback(recruiterGap, nonZero(summary.TargetRecruiters, 5))
		if agencyGap > 0 && recruiterGap <= 0 {
			targetRecruiters = 0
		} else if recruiterGap > 0 && agencyGap <= 0 {
			targetAgencies = 0
		}
		return RunRecommendation{
			ShouldRetry: true,
			Command:     retryCommand(targetAgencies, targetRecruiters, summary.AllowSend),
			Reason:      "The latest run did not finish cleanly.",
			Blocker:     summary.Blocker,
		}
	}
	if summary.AllowSend {
		if agencyGap > 0 {
			return RunRecommendation{
				ShouldRetry: true,
				Command:     retryCommand(agencyGap, 0, true),
				Reason:      fmt.Sprintf("Agency target is still short by %d sends; validate the fixed agency lane without spending time on recruiters.", agencyGap),
			}
		}
		if recruiterGap > 0 {
			return RunRecommendation{
				ShouldRetry: true,
				Command:     retryCommand(0, recruiterGap, true),
				Reason:      fmt.Sprintf("Recruiter target is still short by %d sends.", recruiterGap),
			}
		}
	}
	return RunRecommendation{Reason: "Latest run reached its requested send target; no retry is needed for the same target."}
}

func retryCommand(targetAgencies int, targetRecruiters int, allowSend bool) string {
	parts := []string{
		"/Users/hanifcarroll/.local/bin/recruiter-agency-outreach",
		"run-daily",
		"--session", "auto",
		"--target-agencies", fmt.Sprintf("%d", targetAgencies),
		"--target-recruiters", fmt.Sprintf("%d", targetRecruiters),
		"--refresh-saved-searches",
		"--print-markdown",
	}
	if allowSend {
		parts = append(parts, "--allow-send")
	}
	if targetAgencies > 0 {
		parts = append(parts, "--timeout-ms", "240000", "--stop-when-no-progress", "--max-no-progress-searches", "12")
	}
	return strings.Join(parts, " ")
}

func nonZero(value int, fallback int) int {
	if value != 0 {
		return value
	}
	return fallback
}

func positiveOrFallback(value int, fallback int) int {
	if value > 0 {
		return value
	}
	return fallback
}

func RenderRunSummaryText(summary RunSummary) string {
	lines := []string{
		fmt.Sprintf("run_id=%s", summary.RunID),
		fmt.Sprintf("status=%s", summary.Status),
		fmt.Sprintf("state=%s", summary.StatePath),
		fmt.Sprintf("dashboard=%s", summary.DashboardPath),
		fmt.Sprintf("target=%d agencies,%d recruiters", summary.TargetAgencies, summary.TargetRecruiters),
		fmt.Sprintf("sent=%d agencies,%d recruiters", summary.Counts.Sent.Agencies, summary.Counts.Sent.Recruiters),
		fmt.Sprintf("checked_skipped=conversation_exists %d agencies,%d recruiters; not_messageable %d agencies,%d recruiters; blocked %d agencies,%d recruiters; send_failed %d agencies,%d recruiters",
			summary.Counts.ConversationExists.Agencies,
			summary.Counts.ConversationExists.Recruiters,
			summary.Counts.NotMessageable.Agencies,
			summary.Counts.NotMessageable.Recruiters,
			summary.Counts.Blocked.Agencies,
			summary.Counts.Blocked.Recruiters,
			summary.Counts.SendFailed.Agencies,
			summary.Counts.SendFailed.Recruiters,
		),
	}
	if cleanText(summary.Blocker) != "" {
		lines = append(lines, "blocker="+cleanText(summary.Blocker))
	}
	if summary.Recommendation.ShouldRetry {
		lines = append(lines, "recommendation="+summary.Recommendation.Reason)
		lines = append(lines, "next_command="+summary.Recommendation.Command)
	} else if cleanText(summary.Recommendation.Reason) != "" {
		lines = append(lines, "recommendation="+summary.Recommendation.Reason)
	}
	return strings.Join(lines, "\n")
}
