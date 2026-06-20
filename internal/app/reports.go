package app

import (
	"fmt"
	"sort"
	"strings"
)

func PrintNext(run Run) {
	if next := run.NextSource(); next != nil {
		fmt.Printf("next source: %s\n", next.Name)
		fmt.Printf("source verified: %d/%d\n", next.Verified, next.Quota)
		fmt.Printf("source remaining: %d\n", next.RemainingForSource)
		fmt.Printf("run remaining: %d\n", next.RemainingForRun)
		if next.Fallback {
			fmt.Println("fallback: true")
		}
		return
	}
	if run.State == RunStateNeedsReaudit {
		fmt.Println("next action: re-audit sent invitations People (N)")
	} else if run.VerifiedCount() >= run.Target {
		fmt.Println("next action: final sent-page audit")
	} else {
		fmt.Println("next action: no available source; inspect sources or finish with blocker")
	}
}

func PrintStatus(run Run) {
	fmt.Printf("run: %s\n", run.ID)
	fmt.Printf("date: %s\n", run.Date)
	fmt.Printf("state: %s\n", run.State)
	fmt.Printf("target: %d\n", run.Target)
	fmt.Printf("row-level verified: %d\n", run.VerifiedCount())
	fmt.Printf("audit: start %s, latest %s, delta %s\n", FormatU32Option(run.StartAudit), FormatU32Option(run.LatestAudit), FormatDelta(run.AuditedDelta()))
	if next := run.NextSource(); next != nil {
		fmt.Printf("next: %s (%d/%d, run remaining %d)\n", next.Name, next.Verified, next.Quota, next.RemainingForRun)
	}
}

func RenderReport(run Run) string {
	lines := []string{}
	lines = append(lines, fmt.Sprintf("# LinkedIn Network Run %s", run.Date))
	lines = append(lines, "")
	lines = append(lines, fmt.Sprintf("- Run id: `%s`", run.ID))
	lines = append(lines, fmt.Sprintf("- State: `%s`", run.State))
	lines = append(lines, fmt.Sprintf("- Target: %d", run.Target))
	lines = append(lines, fmt.Sprintf("- Start audit: %s", FormatU32Option(run.StartAudit)))
	lines = append(lines, fmt.Sprintf("- Final/latest audit: %s", FormatU32Option(run.LatestAudit)))
	lines = append(lines, fmt.Sprintf("- Audited delta: %s", FormatDelta(run.AuditedDelta())))
	lines = append(lines, fmt.Sprintf("- Row-level verified pending: %d", run.VerifiedCount()))
	lines = append(lines, fmt.Sprintf("- Imported candidate observations: %d", len(run.Observations)))
	lines = append(lines, "")
	lines = append(lines, "## Source Counts")
	for _, source := range run.Sources {
		verified := run.SourceVerifiedCount(source.Name)
		targetText := ""
		if source.Target > 0 {
			targetText = fmt.Sprintf(" / target %d", source.Target)
		}
		exhaustedText := ""
		if source.Exhausted {
			exhaustedText = " (exhausted)"
		}
		lines = append(lines, fmt.Sprintf("- %s: %d verified%s%s", source.Name, verified, targetText, exhaustedText))
	}
	lines = append(lines, "")
	lines = append(lines, "## Source Yield")
	for _, stats := range SourceYieldReport(run) {
		yieldText := "n/a"
		if stats.ConnectableYield != nil {
			yieldText = fmt.Sprintf("%.1f%%", *stats.ConnectableYield*100.0)
		}
		lines = append(lines, fmt.Sprintf(
			"- %s: %d connectable / %d rows (%s); already pending %d; email-required skips %d; %s",
			stats.Source,
			stats.ConnectableCount,
			stats.RawRowCount,
			yieldText,
			stats.AlreadyPendingCount,
			stats.EmailRequiredSkips,
			stats.Recommendation,
		))
	}
	if len(run.Timings) > 0 {
		lines = append(lines, "")
		lines = append(lines, "## Phase Timing")
		var total uint64
		byPhase := map[string]uint64{}
		for _, event := range run.Timings {
			total += event.DurationMS
			byPhase[event.Phase] += event.DurationMS
		}
		lines = append(lines, "- Total recorded: "+FormatDurationMS(total))
		for _, phase := range sortedKeys(byPhase) {
			lines = append(lines, fmt.Sprintf("- %s: %s", phase, FormatDurationMS(byPhase[phase])))
		}
	}
	if len(run.Notes) > 0 {
		lines = append(lines, "")
		lines = append(lines, "## Notes")
		for _, note := range run.Notes {
			lines = append(lines, "- "+note)
		}
	}
	lines = append(lines, "")
	lines = append(lines, "## Verified Names")
	names := map[string]bool{}
	for _, candidate := range run.Candidates {
		if candidate.Status == CandidateStatusPending {
			names[candidate.Name] = true
		}
	}
	if len(names) == 0 {
		lines = append(lines, "- None recorded")
	} else {
		for _, name := range sortedKeys(names) {
			lines = append(lines, "- "+name)
		}
	}
	topUpNames := map[string]bool{}
	for _, candidate := range run.Candidates {
		if candidate.Status == CandidateStatusAuditTopUp {
			topUpNames[candidate.Name] = true
		}
	}
	if len(topUpNames) > 0 {
		lines = append(lines, "")
		lines = append(lines, "## Audit Top-Up Names")
		for _, name := range sortedKeys(topUpNames) {
			lines = append(lines, "- "+name)
		}
	}
	return strings.Join(lines, "\n")
}

func RenderAcceptanceReport(report AcceptanceReport) string {
	lines := []string{}
	lines = append(lines, "# LinkedIn Acceptance Report")
	lines = append(lines, "")
	lines = append(lines, fmt.Sprintf("- Min age days: %d", report.MinAgeDays))
	lines = append(lines, fmt.Sprintf("- Max age days: %v", report.MaxAgeDays))
	lines = append(lines, fmt.Sprintf("- Total sent in window: %d", report.TotalSent))
	lines = append(lines, fmt.Sprintf("- Checked: %d", report.Checked))
	lines = append(lines, fmt.Sprintf("- Unchecked: %d", report.Unchecked))
	lines = append(lines, fmt.Sprintf("- Accepted: %d%s", report.Accepted, PercentageSuffix(report.Accepted, report.Checked)))
	lines = append(lines, fmt.Sprintf("- Pending: %d", report.Pending))
	lines = append(lines, fmt.Sprintf("- Connectable/not pending: %d", report.Connectable))
	lines = append(lines, fmt.Sprintf("- Unknown: %d", report.Unknown))
	lines = append(lines, fmt.Sprintf("- Blocked: %d", report.Blocked))
	lines = append(lines, fmt.Sprintf("- Failed: %d", report.Failed))
	lines = append(lines, fmt.Sprintf("- Withdrawn: %d", report.Withdrawn))
	lines = append(lines, "")
	lines = append(lines, "## By Source")
	if len(report.BySource) == 0 {
		lines = append(lines, "- No invitations in window")
	} else {
		for _, source := range sortedKeys(report.BySource) {
			sourceReport := report.BySource[source]
			lines = append(lines, fmt.Sprintf(
				"- %s: accepted %d%s / checked %d, pending %d, connectable %d, unknown %d, unchecked %d",
				source,
				sourceReport.Accepted,
				PercentageSuffix(sourceReport.Accepted, sourceReport.Checked),
				sourceReport.Checked,
				sourceReport.Pending,
				sourceReport.Connectable,
				sourceReport.Unknown,
				sourceReport.Unchecked,
			))
		}
	}
	return strings.Join(lines, "\n")
}

func PrintPendingPlan(plan PendingCleanupPlan) {
	switch plan.Action {
	case "capture-more":
		fmt.Printf("capture more: %s\n", valueOrEmpty(plan.Reason))
	case "withdraw-candidate":
		fmt.Printf("withdraw next stale invitation: %s\n", valueOrEmpty(plan.Name))
		fmt.Printf("age: %s\n", valueOrEmpty(plan.AgeText))
		profileURL := "not captured"
		if plan.ProfileURL != nil {
			profileURL = *plan.ProfileURL
		}
		fmt.Printf("profile_url: %s\n", profileURL)
		if plan.WithdrawCapacityRemaining != nil {
			fmt.Printf("withdraw capacity remaining: %d\n", *plan.WithdrawCapacityRemaining)
		}
	case "reaudit":
		fmt.Printf("re-audit: %s\n", valueOrEmpty(plan.Reason))
	case "final-audit":
		fmt.Println("final audit")
	}
}

func PrintPendingStatus(run PendingCleanupRun) {
	fmt.Printf("run: %s\n", run.ID)
	fmt.Printf("date: %s\n", run.Date)
	fmt.Printf("state: %s\n", run.State)
	fmt.Printf("threshold months: %d\n", run.ThresholdMonths)
	fmt.Printf("withdrawn: %d/%d\n", run.WithdrawnCount(), run.MaxWithdrawals)
	fmt.Printf("audit: start %s, latest %s, delta %s\n", FormatU32Option(run.StartAudit), FormatU32Option(run.LatestAudit), FormatDelta(run.AuditedDelta()))
	fmt.Printf("imported observations: %d\n", len(run.Observations))
}

func RenderPendingReport(run PendingCleanupRun) string {
	lines := []string{}
	lines = append(lines, fmt.Sprintf("# LinkedIn Pending Cleanup %s", run.Date))
	lines = append(lines, "")
	lines = append(lines, fmt.Sprintf("- Run id: `%s`", run.ID))
	lines = append(lines, fmt.Sprintf("- State: `%s`", run.State))
	lines = append(lines, fmt.Sprintf("- Threshold: %d months", run.ThresholdMonths))
	lines = append(lines, fmt.Sprintf("- Safety cap: %d", run.MaxWithdrawals))
	lines = append(lines, fmt.Sprintf("- Start audit: %s", FormatU32Option(run.StartAudit)))
	lines = append(lines, fmt.Sprintf("- Final/latest audit: %s", FormatU32Option(run.LatestAudit)))
	lines = append(lines, fmt.Sprintf("- Audited delta: %s", FormatDelta(run.AuditedDelta())))
	lines = append(lines, fmt.Sprintf("- Withdrawn: %d", run.WithdrawnCount()))
	lines = append(lines, fmt.Sprintf("- Imported pending observations: %d", len(run.Observations)))
	lines = append(lines, "")
	lines = append(lines, "## Withdrawn Names")
	names := []string{}
	for _, event := range run.Withdrawals {
		if event.Status == PendingWithdrawStatusWithdrawn {
			names = append(names, fmt.Sprintf("%s (%s)", event.Name, event.AgeText))
		}
	}
	sort.Strings(names)
	if len(names) == 0 {
		lines = append(lines, "- None recorded")
	} else {
		for _, name := range names {
			lines = append(lines, "- "+name)
		}
	}
	return strings.Join(lines, "\n")
}

func valueOrEmpty(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}
