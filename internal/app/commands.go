package app

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func StartRun(store *Store, target uint32, date Date, force bool, maxRealSends *uint32) error {
	if _, err := os.Stat(store.ActivePath()); err == nil && !force {
		return fmt.Errorf("an active run already exists; use --force to replace it")
	}
	realSends := target
	if maxRealSends != nil {
		realSends = *maxRealSends
	}
	run := NewRun(target, date, realSends)
	if err := store.Save(run); err != nil {
		return err
	}
	if err := store.AppendEvent(run, "start", map[string]any{"target": target}); err != nil {
		return err
	}
	fmt.Printf("started run %s for %s with target %d\n", run.ID, date, target)
	PrintNext(run)
	return nil
}

func RecordAudit(store *Store, peopleCount uint32, note *string) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	ApplyAudit(&run, peopleCount, note)
	if err := store.Save(run); err != nil {
		return err
	}
	if err := store.AppendEvent(run, "audit", map[string]any{"people_count": peopleCount, "delta": run.AuditedDelta()}); err != nil {
		return err
	}
	fmt.Printf("audit recorded: People (%d)%s\n", peopleCount, deltaSuffix(run.AuditedDelta()))
	return nil
}

func ImportAudit(store *Store, path string) error {
	started := time.Now()
	run, err := store.Load()
	if err != nil {
		return err
	}
	audit, err := LoadSalesNavAudit(path)
	if err != nil {
		return err
	}
	note := "imported audit; recent_names=" + strings.Join(audit.RecentNames, ", ")
	ApplyAudit(&run, audit.PeopleCount, &note)
	PushTiming(&run, "import-audit", nil, started, ptr(fmt.Sprintf("people_count=%d; path=%s", audit.PeopleCount, path)))
	if err := store.Save(run); err != nil {
		return err
	}
	if err := store.AppendEvent(run, "import-audit", map[string]any{"path": path, "people_count": audit.PeopleCount}); err != nil {
		return err
	}
	fmt.Printf("audit imported: People (%d)%s\n", audit.PeopleCount, deltaSuffix(run.AuditedDelta()))
	return nil
}

func PrintRunNext(store *Store) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	if run.State == RunStateNeedsReaudit {
		return fmt.Errorf("run is in NEEDS_REAUDIT; record a fresh sent-page audit before continuing")
	}
	PrintNext(run)
	return nil
}

func RecordCandidate(store *Store, source, name string, profileURL *string, status CandidateStatus, note *string) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	if run.State == RunStateNeedsReaudit {
		return fmt.Errorf("run is in NEEDS_REAUDIT; record a fresh sent-page audit before recording more sends")
	}
	if err := EnsureKnownSource(run, source); err != nil {
		return err
	}
	if status == CandidateStatusPending {
		for _, candidate := range run.Candidates {
			if candidate.Status == CandidateStatusPending && candidate.Name == name {
				if (candidate.ProfileURL == nil && profileURL == nil) ||
					(candidate.ProfileURL != nil && profileURL != nil && *candidate.ProfileURL == *profileURL) {
					return fmt.Errorf("candidate already recorded as pending: %s", name)
				}
			}
		}
	}
	event := CandidateEvent{
		At:         time.Now(),
		Source:     source,
		Name:       name,
		ProfileURL: profileURL,
		Status:     status,
		Note:       note,
	}
	run.Candidates = append(run.Candidates, event)
	if run.State != RunStateDone && run.State != RunStateBlocked {
		if run.VerifiedCount() >= run.Target {
			run.State = RunStateFinalReconcile
		} else {
			run.State = RunStateSending
		}
	}
	drained, err := DrainStaleConnectableCandidates(&run, nil)
	if err != nil {
		return err
	}
	run.MarkUpdated()
	if err := store.Save(run); err != nil {
		return err
	}
	if err := store.AppendEvent(run, "record", event); err != nil {
		return err
	}
	if len(drained) > 0 {
		if err := store.AppendEvent(run, "drain-stale-candidates", map[string]any{"events": drained}); err != nil {
			return err
		}
	}
	fmt.Printf("recorded %s; verified %d/%d\n", status, run.VerifiedCount(), run.Target)
	if len(drained) > 0 {
		fmt.Printf("auto-skipped %d stale queued candidates\n", len(drained))
	}
	if next := run.NextSource(); next != nil {
		fmt.Printf("next: %s (source %d/%d, run remaining %d)\n", next.Name, next.Verified, next.Quota, next.RemainingForRun)
	} else if run.VerifiedCount() >= run.Target {
		fmt.Println("target row-level verification reached; run final sent-page audit before finish")
	}
	return nil
}

func RecordSendResultCommand(store *Store, path string) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	if run.State == RunStateNeedsReaudit {
		return fmt.Errorf("run is in NEEDS_REAUDIT; record a fresh sent-page audit before recording send results")
	}
	result, err := LoadSalesNavSendResult(path)
	if err != nil {
		return err
	}
	event, err := RecordSendResult(&run, result, path)
	if err != nil {
		return err
	}
	drained, err := DrainStaleConnectableCandidates(&run, nil)
	if err != nil {
		return err
	}
	if err := store.Save(run); err != nil {
		return err
	}
	if err := store.AppendEvent(run, "record-send-result", map[string]any{"path": path, "event": event}); err != nil {
		return err
	}
	if len(drained) > 0 {
		if err := store.AppendEvent(run, "drain-stale-candidates", map[string]any{"events": drained}); err != nil {
			return err
		}
	}
	fmt.Printf("recorded send result as %s; verified %d/%d\n", event.Status, run.VerifiedCount(), run.Target)
	if len(drained) > 0 {
		fmt.Printf("auto-skipped %d stale queued candidates\n", len(drained))
	}
	return nil
}

type SendNextOptions struct {
	Session    *string
	Playwriter string
	Script     string
	OutDir     string
	DryRun     bool
	AllowSend  bool
	NoRecord   bool
}

func SendNext(store *Store, options SendNextOptions) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	if run.State == RunStateNeedsReaudit {
		return fmt.Errorf("run is in NEEDS_REAUDIT; record a fresh sent-page audit before sending")
	}
	if options.AllowSend && run.RealSendCapacityRemaining() == 0 {
		return fmt.Errorf("real-send cap reached: %d/%d verified sends", run.VerifiedCount(), run.MaxRealSends)
	}
	candidate := run.NextConnectableObservation()
	if candidate == nil {
		return fmt.Errorf("no unrecorded connectable candidate available")
	}
	if options.Session == nil {
		return fmt.Errorf("--session is required to execute Playwriter")
	}
	started := time.Now()
	resultPath, err := RunPlaywriterSend(options.Playwriter, *options.Session, options.Script, options.OutDir, *candidate, options.DryRun, options.AllowSend)
	if err != nil {
		return err
	}
	fmt.Printf("send result: %s\n", resultPath)
	if options.AllowSend && !options.DryRun && !options.NoRecord {
		run, err := store.Load()
		if err != nil {
			return err
		}
		result, err := LoadSalesNavSendResult(resultPath)
		if err != nil {
			return err
		}
		event, err := RecordSendResult(&run, result, resultPath)
		if err != nil {
			return err
		}
		drained, err := DrainStaleConnectableCandidates(&run, nil)
		if err != nil {
			return err
		}
		PushTiming(&run, "send-next", &event.Source, started, ptr(fmt.Sprintf("status=%s; path=%s", event.Status, resultPath)))
		if err := store.Save(run); err != nil {
			return err
		}
		if err := store.AppendEvent(run, "record-send-result", map[string]any{"path": resultPath, "event": event}); err != nil {
			return err
		}
		if len(drained) > 0 {
			if err := store.AppendEvent(run, "drain-stale-candidates", map[string]any{"events": drained}); err != nil {
				return err
			}
		}
		fmt.Printf("recorded send result; verified %d/%d\n", run.VerifiedCount(), run.Target)
		if len(drained) > 0 {
			fmt.Printf("auto-skipped %d stale queued candidates\n", len(drained))
		}
	}
	return nil
}

func DrainStaleCandidatesCommand(store *Store, source *string) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	drained, err := DrainStaleConnectableCandidates(&run, source)
	if err != nil {
		return err
	}
	if err := store.Save(run); err != nil {
		return err
	}
	if err := store.AppendEvent(run, "drain-stale-candidates", map[string]any{"source": source, "events": drained}); err != nil {
		return err
	}
	fmt.Printf("auto-skipped %d stale queued candidates\n", len(drained))
	PrintNext(run)
	return nil
}

func SourceExhausted(store *Store, source string, note *string) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	found := false
	for i := range run.Sources {
		if run.Sources[i].Name == source {
			run.Sources[i].Exhausted = true
			found = true
			break
		}
	}
	if !found {
		return fmt.Errorf("unknown source: %s", source)
	}
	if note != nil {
		run.Notes = append(run.Notes, fmt.Sprintf("source exhausted: %s: %s", source, *note))
	}
	run.MarkUpdated()
	if err := store.Save(run); err != nil {
		return err
	}
	if err := store.AppendEvent(run, "source-exhausted", map[string]any{"source": source}); err != nil {
		return err
	}
	fmt.Println("marked source exhausted")
	PrintNext(run)
	return nil
}

func NeedsReaudit(store *Store, reason string) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	run.State = RunStateNeedsReaudit
	run.Notes = append(run.Notes, "needs re-audit: "+reason)
	run.MarkUpdated()
	if err := store.Save(run); err != nil {
		return err
	}
	if err := store.AppendEvent(run, "needs-reaudit", map[string]any{"reason": reason}); err != nil {
		return err
	}
	fmt.Println("run paused in NEEDS_REAUDIT; record a fresh People (N) audit before sending")
	return nil
}

func ImportCaptureCommand(store *Store, path string, onlyConnectable bool) error {
	started := time.Now()
	run, err := store.Load()
	if err != nil {
		return err
	}
	capture, err := LoadSalesNavCapture(path)
	if err != nil {
		return err
	}
	captureSource := capture.Source
	imported, err := ImportCapture(&run, capture, ImportCaptureOptions{OnlyConnectable: onlyConnectable})
	if err != nil {
		return err
	}
	drained, err := DrainStaleConnectableCandidates(&run, nil)
	if err != nil {
		return err
	}
	PushTiming(&run, "import-capture", captureSource, started, ptr(fmt.Sprintf("imported=%d; drained=%d; only_connectable=%t; path=%s", imported, len(drained), onlyConnectable, path)))
	run.MarkUpdated()
	if err := store.Save(run); err != nil {
		return err
	}
	if err := store.AppendEvent(run, "import-capture", map[string]any{"path": path, "imported": imported, "only_connectable": onlyConnectable}); err != nil {
		return err
	}
	if len(drained) > 0 {
		if err := store.AppendEvent(run, "drain-stale-candidates", map[string]any{"events": drained}); err != nil {
			return err
		}
	}
	fmt.Printf("imported %d candidate observations\n", imported)
	if len(drained) > 0 {
		fmt.Printf("auto-skipped %d stale queued candidates\n", len(drained))
	}
	if candidate := run.NextConnectableObservation(); candidate != nil {
		profile := "no profile url captured"
		if candidate.ProfileURL != nil {
			profile = *candidate.ProfileURL
		}
		fmt.Printf("next connectable: %s (%s)\n", candidate.Name, profile)
	} else if candidate := run.NextTopUpObservation(); candidate != nil {
		profile := "no profile url captured"
		if candidate.ProfileURL != nil {
			profile = *candidate.ProfileURL
		}
		fmt.Printf("next top-up connectable: %s (%s)\n", candidate.Name, profile)
	} else {
		fmt.Println("no unrecorded connectable candidate in imported captures")
	}
	return nil
}

func RecordTopUpResult(store *Store, path string, note *string) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	result, err := LoadSalesNavSendResult(path)
	if err != nil {
		return err
	}
	event, err := RecordTopUpSendResult(&run, result, path, note)
	if err != nil {
		return err
	}
	if err := store.Save(run); err != nil {
		return err
	}
	if err := store.AppendEvent(run, "record-top-up-result", map[string]any{"path": path, "event": event}); err != nil {
		return err
	}
	fmt.Printf("recorded top-up result as %s; row-level verified remains %d/%d\n", event.Status, run.VerifiedCount(), run.Target)
	return nil
}

func PrintNextCandidate(store *Store, asJSON bool) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	candidate := run.NextConnectableObservation()
	if asJSON {
		if candidate == nil {
			fmt.Println("null")
			return nil
		}
		text, err := PrettyJSON(candidate)
		if err != nil {
			return err
		}
		fmt.Println(text)
		return nil
	}
	if candidate == nil {
		fmt.Println("no unrecorded connectable candidate available")
		return nil
	}
	fmt.Printf("source: %s\n", candidate.Source)
	fmt.Printf("name: %s\n", candidate.Name)
	fmt.Printf("profile_url: %v\n", candidate.ProfileURL)
	fmt.Printf("menu_state: %s\n", candidate.MenuState)
	fmt.Printf("menu_labels: %s\n", strings.Join(candidate.MenuLabels, ", "))
	return nil
}

func PrintCandidates(store *Store, asJSON bool, status *string) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	observations := []CandidateObservation{}
	for _, observation := range run.Observations {
		if status == nil || observation.MenuState == *status {
			observations = append(observations, observation)
		}
	}
	if asJSON {
		text, err := PrettyJSON(observations)
		if err != nil {
			return err
		}
		fmt.Println(text)
		return nil
	}
	for _, observation := range observations {
		profile := ""
		if observation.ProfileURL != nil {
			profile = *observation.ProfileURL
		}
		fmt.Printf("%s\t%s\t%s\t%s\n", observation.MenuState, observation.Source, observation.Name, profile)
	}
	return nil
}

func PrintPlan(store *Store, asJSON bool) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	reservoir, err := store.LoadReservoir()
	if err != nil {
		return err
	}
	plan := run.OperatorPlanWithReservoir(&reservoir)
	if asJSON {
		text, err := PrettyJSON(plan)
		if err != nil {
			return err
		}
		fmt.Println(text)
		return nil
	}
	switch plan.Action {
	case "use-reservoir":
		fmt.Printf("use reservoir: %s (%d available, %d needed)\n", valueOrEmpty(plan.Source), intValue(plan.Available), uintValue(plan.Remaining))
		fmt.Printf("run: linkedin-network-run reservoir fill-run --source \"%s\"\n", valueOrEmpty(plan.Source))
	case "capture-source":
		fmt.Printf("capture source: %s (%d needed)\n", valueOrEmpty(plan.Source), uintValue(plan.Remaining))
		if plan.Capture != nil {
			fmt.Printf(
				"recommended capture: pages=%d, stopAfterConnectable=%d, playwriterTimeoutMs=%d (buffer=%d, reason=%s)\n",
				plan.Capture.Pages,
				plan.Capture.StopAfterConnectable,
				plan.Capture.PlaywriterTimeoutMS,
				plan.Capture.Buffer,
				plan.Capture.Reason,
			)
		}
		if plan.ResumeURL != nil {
			fmt.Printf("resume_url: %s\n", *plan.ResumeURL)
		}
		if plan.Cursor != nil {
			page := "unknown"
			if plan.Cursor.PageLabel != nil {
				page = *plan.Cursor.PageLabel
			}
			fmt.Printf("last capture: %d rows, %d connectable, page %s\n", plan.Cursor.RawRowCount, plan.Cursor.ConnectableCount, page)
		}
	case "send-candidate":
		fmt.Printf("send next candidate: %s\n", valueOrEmpty(plan.Name))
		fmt.Printf("source: %s\n", valueOrEmpty(plan.Source))
		profile := "not captured"
		if plan.ProfileURL != nil {
			profile = *plan.ProfileURL
		}
		fmt.Printf("profile_url: %s\n", profile)
		fmt.Printf("real-send capacity remaining: %d\n", uintValue(plan.RealSendCapacityRemaining))
	case "reaudit":
		fmt.Printf("re-audit: %s\n", valueOrEmpty(plan.Reason))
	case "final-audit":
		fmt.Println("final audit")
	case "blocked":
		fmt.Printf("blocked: %s\n", valueOrEmpty(plan.Reason))
	}
	return nil
}

func PrintRunStatus(store *Store, asJSON bool) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	if asJSON {
		text, err := PrettyJSON(run)
		if err != nil {
			return err
		}
		fmt.Println(text)
		return nil
	}
	PrintStatus(run)
	return nil
}

func FinishRun(store *Store, force bool) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	delta := run.AuditedDelta()
	if !force && (delta == nil || *delta != int64(run.Target)) {
		return fmt.Errorf("final audit delta is %s, expected %d; run audit <people-count> or use --force", FormatDelta(delta), run.Target)
	}
	run.State = RunStateDone
	run.MarkUpdated()
	if err := store.Save(run); err != nil {
		return err
	}
	ledger, err := store.LoadAcceptanceLedger()
	if err != nil {
		return err
	}
	seeded := ledger.UpsertFromRun(run)
	if err := store.SaveAcceptanceLedger(ledger); err != nil {
		return err
	}
	if err := store.AppendEvent(run, "finish", map[string]any{"audited_delta": delta, "acceptance_seeded": seeded}); err != nil {
		return err
	}
	if err := store.AppendAcceptanceEvent("seed-from-finish", map[string]any{"run_id": run.ID, "seeded": seeded}); err != nil {
		return err
	}
	fmt.Println(RenderReport(run))
	fmt.Printf("acceptance ledger seeded: %d new invitations\n", seeded)
	return nil
}

func TuneSources(store *Store, minRawRows uint32, maxConnectableYield float64, apply bool) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	stats := SourceYieldReport(run)
	fmt.Println("# Source Yield")
	for _, item := range stats {
		yieldText := "n/a"
		if item.ConnectableYield != nil {
			yieldText = fmt.Sprintf("%.1f%%", *item.ConnectableYield*100.0)
		}
		fmt.Printf("- %s: %d connectable / %d rows (%s); pending %d, email-required skips %d, recommendation: %s\n",
			item.Source,
			item.ConnectableCount,
			item.RawRowCount,
			yieldText,
			item.PendingSends,
			item.EmailRequiredSkips,
			item.Recommendation,
		)
	}
	lowYield := LowYieldSourceNames(run, minRawRows, maxConnectableYield)
	if len(lowYield) == 0 {
		fmt.Println("no source met the low-yield threshold")
		return nil
	}
	fmt.Println("low-yield sources: " + strings.Join(lowYield, ", "))
	if apply {
		lowYieldSet := map[string]bool{}
		for _, source := range lowYield {
			lowYieldSet[source] = true
		}
		for i := range run.Sources {
			if lowYieldSet[run.Sources[i].Name] {
				run.Sources[i].Exhausted = true
			}
		}
		for _, source := range lowYield {
			run.Notes = append(run.Notes, fmt.Sprintf("source tuned low-yield: %s; threshold raw>=%d, connectable_yield<=%.3f", source, minRawRows, maxConnectableYield))
		}
		run.MarkUpdated()
		if err := store.Save(run); err != nil {
			return err
		}
		if err := store.AppendEvent(run, "tune-sources", map[string]any{
			"min_raw_rows":          minRawRows,
			"max_connectable_yield": maxConnectableYield,
			"exhausted":             lowYield,
		}); err != nil {
			return err
		}
		fmt.Println("marked low-yield sources exhausted")
	} else {
		fmt.Println("dry run only; pass --apply to mark low-yield sources exhausted")
	}
	return nil
}

func HandleAcceptanceSeed(store *Store, includeUnfinished bool) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	if !includeUnfinished && run.State != RunStateDone {
		return fmt.Errorf("active run is not Done; pass --include-unfinished to seed provisional sends")
	}
	ledger, err := store.LoadAcceptanceLedger()
	if err != nil {
		return err
	}
	seeded := ledger.UpsertFromRun(run)
	if err := store.SaveAcceptanceLedger(ledger); err != nil {
		return err
	}
	if err := store.AppendAcceptanceEvent("seed", map[string]any{"run_id": run.ID, "seeded": seeded, "include_unfinished": includeUnfinished}); err != nil {
		return err
	}
	fmt.Printf("acceptance ledger seeded: %d new invitations\n", seeded)
	return nil
}

func HandleAcceptanceSeedHistory(store *Store) error {
	ledger, err := store.LoadAcceptanceLedger()
	if err != nil {
		return err
	}
	summary, err := store.SeedAcceptanceFromHistory(&ledger)
	if err != nil {
		return err
	}
	if err := store.SaveAcceptanceLedger(ledger); err != nil {
		return err
	}
	if err := store.AppendAcceptanceEvent("seed-history", summary); err != nil {
		return err
	}
	fmt.Printf("acceptance ledger history seeded: %d new invitations from %d run logs (%d sent events scanned)\n", summary.Seeded, summary.RunLogs, summary.SentEvents)
	return nil
}

func HandleAcceptanceExport(store *Store, minAgeDays int64, maxAgeDays *int64, out string) error {
	ledger, err := store.LoadAcceptanceLedger()
	if err != nil {
		return err
	}
	invitations := ledger.EligibleForCheck(minAgeDays, maxAgeDays)
	candidates := make([]AcceptanceCheckCandidate, 0, len(invitations))
	for _, invitation := range invitations {
		candidates = append(candidates, NewAcceptanceCheckCandidate(invitation))
	}
	if err := writeJSONAtomic(out, candidates); err != nil {
		return err
	}
	if err := store.AppendAcceptanceEvent("export", map[string]any{"path": out, "min_age_days": minAgeDays, "max_age_days": maxAgeDays, "count": len(candidates)}); err != nil {
		return err
	}
	fmt.Printf("exported %d acceptance-check candidates to %s\n", len(candidates), out)
	return nil
}

func HandleAcceptanceImport(store *Store, path string) error {
	artifact, err := LoadAcceptanceOutcomeArtifact(path)
	if err != nil {
		return err
	}
	ledger, err := store.LoadAcceptanceLedger()
	if err != nil {
		return err
	}
	summary := ledger.ImportOutcomes(artifact)
	if err := store.SaveAcceptanceLedger(ledger); err != nil {
		return err
	}
	if err := store.AppendAcceptanceEvent("import", map[string]any{"path": path, "summary": summary}); err != nil {
		return err
	}
	fmt.Printf("imported acceptance outcomes: %d rows, %d matched, %d unmatched\n", summary.Rows, summary.Matched, summary.Unmatched)
	return nil
}

func HandleAcceptanceReport(store *Store, minAgeDays int64, maxAgeDays *int64, asJSON bool) error {
	ledger, err := store.LoadAcceptanceLedger()
	if err != nil {
		return err
	}
	report := ledger.Report(minAgeDays, maxAgeDays)
	if asJSON {
		text, err := PrettyJSON(report)
		if err != nil {
			return err
		}
		fmt.Println(text)
	} else {
		fmt.Println(RenderAcceptanceReport(report))
	}
	return nil
}

type AcceptanceDraftFollowupsOptions struct {
	Session             *string
	Playwriter          string
	ResearchScript      string
	Research            *string
	Out                 *string
	OutDir              string
	Strategy            DraftStrategy
	IncludeDrafted      bool
	PublicWeb           bool
	MaxWebResults       uint32
	DelayMS             uint64
	PlaywriterTimeoutMS uint32
}

func HandleAcceptanceDraftFollowups(store *Store, options AcceptanceDraftFollowupsOptions) error {
	ledger, err := store.LoadAcceptanceLedger()
	if err != nil {
		return err
	}
	followups, err := store.LoadAcceptanceFollowupLedger()
	if err != nil {
		return err
	}
	candidates := ledger.AcceptedForFollowup(followups, options.IncludeDrafted)
	reportPath := store.DefaultAcceptanceFollowupReportPath()
	if options.Out != nil {
		reportPath = *options.Out
	}
	var researchPath *string
	if len(candidates) == 0 {
		researchPath = options.Research
	} else if options.Research != nil {
		researchPath = options.Research
	} else {
		if options.Session == nil {
			return fmt.Errorf("--session is required when --research is not provided")
		}
		if err := os.MkdirAll(options.OutDir, 0o755); err != nil {
			return fmt.Errorf("creating %s: %w", options.OutDir, err)
		}
		candidatesPath := filepath.Join(options.OutDir, "accepted-candidates.json")
		research := filepath.Join(options.OutDir, "accepted-research.json")
		if err := writeJSONAtomic(candidatesPath, candidates); err != nil {
			return err
		}
		if err := RunPlaywriterAcceptedResearch(
			options.Playwriter,
			*options.Session,
			options.ResearchScript,
			candidatesPath,
			research,
			options.PublicWeb,
			options.MaxWebResults,
			options.DelayMS,
			options.PlaywriterTimeoutMS,
		); err != nil {
			return err
		}
		researchPath = &research
	}
	var artifact *AcceptedResearchArtifact
	if researchPath != nil {
		loaded, err := LoadAcceptedResearchArtifact(*researchPath)
		if err != nil {
			return err
		}
		artifact = &loaded
	}
	report := BuildDraftReport(candidates, artifact, options.Strategy, researchPath)
	if err := WriteDraftReport(reportPath, report); err != nil {
		return err
	}
	recorded := followups.RecordReport(report, reportPath, researchPath)
	if err := store.SaveAcceptanceFollowupLedger(followups); err != nil {
		return err
	}
	if err := store.AppendAcceptanceEvent("draft-followups", map[string]any{
		"report_path":     reportPath,
		"research_path":   researchPath,
		"draft_count":     len(report.Items),
		"recorded":        recorded,
		"strategy":        options.Strategy,
		"include_drafted": options.IncludeDrafted,
		"public_web":      options.PublicWeb,
		"max_web_results": options.MaxWebResults,
	}); err != nil {
		return err
	}
	fmt.Printf("accepted follow-up drafts: %d written to %s\n", len(report.Items), reportPath)
	if researchPath != nil {
		fmt.Printf("research artifact: %s\n", *researchPath)
	}
	return nil
}

func HandleReservoirCapture(store *Store, session *string, playwriter, script, savedSearches, source string, url *string, outDir string, options CaptureRunOptions) error {
	if session == nil {
		return fmt.Errorf("--session is required to execute Playwriter")
	}
	resolvedURL, err := ResolveCaptureURL(url, savedSearches, source, "--url")
	if err != nil {
		return err
	}
	capturePath, err := RunPlaywriterCapture(playwriter, *session, script, outDir, source, resolvedURL, options)
	if err != nil {
		return err
	}
	capture, err := LoadSalesNavCapture(capturePath)
	if err != nil {
		return err
	}
	reservoir, err := store.LoadReservoir()
	if err != nil {
		return err
	}
	imported, err := ImportCaptureIntoReservoir(&reservoir, capture, ImportCaptureOptions{OnlyConnectable: options.OnlyConnectable})
	if err != nil {
		return err
	}
	if err := store.SaveReservoir(reservoir); err != nil {
		return err
	}
	fmt.Printf("reservoir captured %d candidate observations from %s; total %d\n", imported, source, len(reservoir.Observations))
	return nil
}

func HandleReservoirImportCapture(store *Store, path string, onlyConnectable bool) error {
	capture, err := LoadSalesNavCapture(path)
	if err != nil {
		return err
	}
	reservoir, err := store.LoadReservoir()
	if err != nil {
		return err
	}
	imported, err := ImportCaptureIntoReservoir(&reservoir, capture, ImportCaptureOptions{OnlyConnectable: onlyConnectable})
	if err != nil {
		return err
	}
	if err := store.SaveReservoir(reservoir); err != nil {
		return err
	}
	fmt.Printf("reservoir imported %d candidate observations; total %d\n", imported, len(reservoir.Observations))
	return nil
}

func HandleReservoirFillRun(store *Store, source *string, limit *int) error {
	run, err := store.Load()
	if err != nil {
		return err
	}
	reservoir, err := store.LoadReservoir()
	if err != nil {
		return err
	}
	fillSource := ""
	if source != nil {
		fillSource = *source
	} else if next := run.NextSource(); next != nil {
		fillSource = next.Name
	} else {
		return fmt.Errorf("no source provided and no active run source available")
	}
	quota, _ := run.SourceQuota(fillSource)
	remaining := quota - minUint32(quota, run.SourceVerifiedCount(fillSource))
	fillLimit := int(remaining + 3)
	if limit != nil {
		fillLimit = *limit
	}
	imported, err := FillRunFromReservoir(&run, &reservoir, fillSource, fillLimit)
	if err != nil {
		return err
	}
	if err := store.Save(run); err != nil {
		return err
	}
	if err := store.SaveReservoir(reservoir); err != nil {
		return err
	}
	if err := store.AppendEvent(run, "reservoir-fill-run", map[string]any{"source": fillSource, "imported": imported}); err != nil {
		return err
	}
	fmt.Printf("filled active run with %d reservoir candidates\n", imported)
	if candidate := run.NextConnectableObservation(); candidate != nil {
		profile := "no profile url captured"
		if candidate.ProfileURL != nil {
			profile = *candidate.ProfileURL
		}
		fmt.Printf("next connectable: %s (%s)\n", candidate.Name, profile)
	}
	return nil
}

func HandleReservoirReport(store *Store, asJSON bool) error {
	reservoir, err := store.LoadReservoir()
	if err != nil {
		return err
	}
	if asJSON {
		text, err := PrettyJSON(reservoir)
		if err != nil {
			return err
		}
		fmt.Println(text)
		return nil
	}
	fmt.Println("# LinkedIn Candidate Reservoir")
	fmt.Printf("- Total candidates: %d\n", len(reservoir.Observations))
	fmt.Printf("- Updated at: %v\n", reservoir.UpdatedAt)
	fmt.Println()
	fmt.Println("## Source Counts")
	bySource := map[string]int{}
	for _, observation := range reservoir.Observations {
		bySource[observation.Source]++
	}
	for _, source := range sortedKeys(bySource) {
		fmt.Printf("- %s: %d\n", source, bySource[source])
	}
	return nil
}

func HandleReservoirClear(store *Store, source *string) error {
	reservoir, err := store.LoadReservoir()
	if err != nil {
		return err
	}
	before := len(reservoir.Observations)
	if source != nil {
		kept := reservoir.Observations[:0]
		for _, observation := range reservoir.Observations {
			if observation.Source != *source {
				kept = append(kept, observation)
			}
		}
		reservoir.Observations = kept
	} else {
		reservoir.Observations = []CandidateObservation{}
	}
	now := time.Now()
	reservoir.UpdatedAt = &now
	if err := store.SaveReservoir(reservoir); err != nil {
		return err
	}
	fmt.Printf("removed %d reservoir candidates\n", before-len(reservoir.Observations))
	return nil
}

func PendingCleanupStart(store *Store, maxWithdrawals, thresholdMonths uint32, date Date, force bool) error {
	if _, err := os.Stat(store.PendingActivePath()); err == nil && !force {
		return fmt.Errorf("an active pending-cleanup run already exists; use --force to replace it")
	}
	run := NewPendingCleanupRun(maxWithdrawals, thresholdMonths, date)
	if err := store.SavePending(run); err != nil {
		return err
	}
	if err := store.AppendPendingEvent(run, "start", map[string]any{"max_withdrawals": maxWithdrawals, "threshold_months": thresholdMonths}); err != nil {
		return err
	}
	fmt.Printf("started pending cleanup %s for %s; cap %d, threshold %d months\n", run.ID, date, maxWithdrawals, thresholdMonths)
	return nil
}

func PendingCleanupImportAudit(store *Store, path string) error {
	run, err := store.LoadPending()
	if err != nil {
		return err
	}
	audit, err := LoadSalesNavAudit(path)
	if err != nil {
		return err
	}
	note := "imported audit; recent_names=" + strings.Join(audit.RecentNames, ", ")
	ApplyPendingAudit(&run, audit.PeopleCount, &note)
	if err := store.SavePending(run); err != nil {
		return err
	}
	if err := store.AppendPendingEvent(run, "import-audit", map[string]any{"path": path, "people_count": audit.PeopleCount}); err != nil {
		return err
	}
	fmt.Printf("pending audit imported: People (%d)%s\n", audit.PeopleCount, deltaSuffix(run.AuditedDelta()))
	return nil
}

func PendingCleanupImportCapture(store *Store, path string) error {
	run, err := store.LoadPending()
	if err != nil {
		return err
	}
	capture, err := LoadPendingCapture(path)
	if err != nil {
		return err
	}
	imported, err := ImportPendingCapture(&run, capture)
	if err != nil {
		return err
	}
	run.State = PendingCleanupStateWithdrawing
	run.MarkUpdated()
	if err := store.SavePending(run); err != nil {
		return err
	}
	if err := store.AppendPendingEvent(run, "import-capture", map[string]any{"path": path, "imported": imported}); err != nil {
		return err
	}
	fmt.Printf("imported %d pending invitation observations\n", imported)
	if candidate := run.NextEligibleObservation(); candidate != nil {
		fmt.Printf("next stale invitation: %s (%s)\n", candidate.Name, candidate.AgeText)
	} else {
		fmt.Println("no unrecorded eligible stale invitation in imported capture")
	}
	return nil
}

func PendingCleanupPlanCommand(store *Store, asJSON bool) error {
	run, err := store.LoadPending()
	if err != nil {
		return err
	}
	plan := run.OperatorPlan()
	if asJSON {
		text, err := PrettyJSON(plan)
		if err != nil {
			return err
		}
		fmt.Println(text)
	} else {
		PrintPendingPlan(plan)
	}
	return nil
}

func PendingCleanupNext(store *Store, asJSON bool) error {
	run, err := store.LoadPending()
	if err != nil {
		return err
	}
	candidate := run.NextEligibleObservation()
	if asJSON {
		if candidate == nil {
			fmt.Println("null")
			return nil
		}
		text, err := PrettyJSON(candidate)
		if err != nil {
			return err
		}
		fmt.Println(text)
		return nil
	}
	if candidate == nil {
		fmt.Println("no unrecorded eligible stale invitation available")
		return nil
	}
	fmt.Printf("name: %s\n", candidate.Name)
	fmt.Printf("age_text: %s\n", candidate.AgeText)
	fmt.Printf("profile_url: %v\n", candidate.ProfileURL)
	return nil
}

func PendingCleanupRecordWithdrawResult(store *Store, path string) error {
	run, err := store.LoadPending()
	if err != nil {
		return err
	}
	result, err := LoadPendingWithdrawResult(path)
	if err != nil {
		return err
	}
	event, err := RecordPendingWithdrawResult(&run, result, path)
	if err != nil {
		return err
	}
	if err := store.SavePending(run); err != nil {
		return err
	}
	if err := store.AppendPendingEvent(run, "record-withdraw-result", map[string]any{"path": path, "event": event}); err != nil {
		return err
	}
	fmt.Printf("recorded withdraw result as %s; withdrawn %d/%d\n", event.Status, run.WithdrawnCount(), run.MaxWithdrawals)
	return nil
}

type PendingWithdrawNextOptions struct {
	Session       *string
	Playwriter    string
	Script        string
	OutDir        string
	DryRun        bool
	AllowWithdraw bool
	NoRecord      bool
}

func PendingCleanupWithdrawNext(store *Store, options PendingWithdrawNextOptions) error {
	run, err := store.LoadPending()
	if err != nil {
		return err
	}
	if options.AllowWithdraw && run.WithdrawCapacityRemaining() == 0 {
		return fmt.Errorf("withdrawal cap reached: %d/%d withdrawals", run.WithdrawnCount(), run.MaxWithdrawals)
	}
	candidate := run.NextEligibleObservation()
	if candidate == nil {
		return fmt.Errorf("no unrecorded eligible stale invitation available")
	}
	if options.Session == nil {
		return fmt.Errorf("--session is required to execute Playwriter")
	}
	if err := os.MkdirAll(options.OutDir, 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", options.OutDir, err)
	}
	candidatePath := filepath.Join(options.OutDir, "pending-candidate.json")
	resultPath := filepath.Join(options.OutDir, "withdraw-result.json")
	if err := writePendingCandidate(candidatePath, *candidate); err != nil {
		return err
	}
	resultJSON, _ := json.Marshal(resultPath)
	candidateJSON, _ := json.Marshal(candidatePath)
	configJS := fmt.Sprintf(
		"state.salesNavPendingWithdrawConfig = { out: %s, dryRun: %t, allowWithdraw: %t, candidate: JSON.parse(require('node:fs').readFileSync(%s, 'utf8')) }; console.log(JSON.stringify(state.salesNavPendingWithdrawConfig));",
		string(resultJSON),
		options.DryRun || !options.AllowWithdraw,
		options.AllowWithdraw,
		string(candidateJSON),
	)
	if err := RunPlaywriterConfig(options.Playwriter, *options.Session, configJS); err != nil {
		return err
	}
	if err := RunPlaywriterFile(options.Playwriter, *options.Session, options.Script); err != nil {
		return err
	}
	fmt.Printf("withdraw result: %s\n", resultPath)
	if options.AllowWithdraw && !options.DryRun && !options.NoRecord {
		run, err := store.LoadPending()
		if err != nil {
			return err
		}
		result, err := LoadPendingWithdrawResult(resultPath)
		if err != nil {
			return err
		}
		if _, err := RecordPendingWithdrawResult(&run, result, resultPath); err != nil {
			return err
		}
		if err := store.SavePending(run); err != nil {
			return err
		}
		if err := store.AppendPendingEvent(run, "record-withdraw-result", map[string]any{"path": resultPath}); err != nil {
			return err
		}
		fmt.Printf("recorded withdraw result; withdrawn %d/%d\n", run.WithdrawnCount(), run.MaxWithdrawals)
	}
	return nil
}

func PendingCleanupStatus(store *Store, asJSON bool) error {
	run, err := store.LoadPending()
	if err != nil {
		return err
	}
	if asJSON {
		text, err := PrettyJSON(run)
		if err != nil {
			return err
		}
		fmt.Println(text)
	} else {
		PrintPendingStatus(run)
	}
	return nil
}

func PendingCleanupFinish(store *Store, force bool) error {
	run, err := store.LoadPending()
	if err != nil {
		return err
	}
	expectedDelta := -int64(run.WithdrawnCount())
	delta := run.AuditedDelta()
	if !force && (delta == nil || *delta != expectedDelta) {
		return fmt.Errorf("final audit delta is %s, expected %d; import a fresh audit or use --force", FormatDelta(delta), expectedDelta)
	}
	run.State = PendingCleanupStateDone
	run.MarkUpdated()
	if err := store.SavePending(run); err != nil {
		return err
	}
	if err := store.AppendPendingEvent(run, "finish", map[string]any{"audited_delta": delta}); err != nil {
		return err
	}
	fmt.Println(RenderPendingReport(run))
	return nil
}

func intValue(value *int) int {
	if value == nil {
		return 0
	}
	return *value
}

func uintValue(value *uint32) uint32 {
	if value == nil {
		return 0
	}
	return *value
}

func deltaSuffix(delta *int64) string {
	if delta == nil {
		return ""
	}
	return fmt.Sprintf(", audited delta %d", *delta)
}
