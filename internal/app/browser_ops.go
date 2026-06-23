package app

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

func RunPlaywriterSend(
	playwriter string,
	session string,
	script string,
	outDir string,
	candidate CandidateObservation,
	dryRun bool,
	allowSend bool,
) (string, error) {
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return "", fmt.Errorf("creating %s: %w", outDir, err)
	}
	candidatePath := filepath.Join(outDir, "next-candidate.json")
	resultPath := filepath.Join(outDir, "send-result.json")
	if err := writeJSONAtomic(candidatePath, candidate); err != nil {
		return "", err
	}
	resultPathJSON, err := json.Marshal(resultPath)
	if err != nil {
		return "", err
	}
	candidatePathJSON, err := json.Marshal(candidatePath)
	if err != nil {
		return "", err
	}
	configJS := fmt.Sprintf(
		"state.salesNavSendConfig = { out: %s, dryRun: %t, allowSend: %t, candidate: JSON.parse(require('node:fs').readFileSync(%s, 'utf8')) }; console.log(JSON.stringify(state.salesNavSendConfig));",
		string(resultPathJSON),
		dryRun || !allowSend,
		allowSend,
		string(candidatePathJSON),
	)
	if err := RunPlaywriterConfig(playwriter, session, configJS); err != nil {
		return "", err
	}
	if err := RunPlaywriterFileWithTimeout(playwriter, session, script, 90000); err != nil {
		return "", err
	}
	return resultPath, nil
}

type SendGuardedOptions struct {
	Session     *string
	Playwriter  string
	Script      string
	OutDir      string
	MaxAttempts uint32
	DryRun      bool
	SinglePass  bool
	AllowSend   bool
	NoRecord    bool
}

func HandleSendGuarded(store *Store, options SendGuardedOptions) error {
	if !options.DryRun && !options.AllowSend {
		return fmt.Errorf("real guarded sends require --allow-send")
	}
	if options.Session == nil {
		return fmt.Errorf("--session is required to execute Playwriter")
	}
	run, err := store.Load()
	if err != nil {
		return err
	}
	if run.State == RunStateNeedsReaudit {
		return fmt.Errorf("run is in NEEDS_REAUDIT; record a fresh sent-page audit before sending")
	}
	next := run.NextSource()
	if next == nil {
		return fmt.Errorf("no active source available for guarded send")
	}
	source := next.Name

	var attempts uint32
	for {
		run, err = store.Load()
		if err != nil {
			return err
		}
		if run.State == RunStateNeedsReaudit {
			return fmt.Errorf("run entered NEEDS_REAUDIT; import a fresh audit before continuing")
		}
		drained, err := DrainStaleConnectableCandidates(&run, nil)
		if err != nil {
			return err
		}
		if len(drained) > 0 {
			if err := store.Save(run); err != nil {
				return err
			}
			if err := store.AppendEvent(run, "drain-stale-candidates", map[string]any{"events": drained}); err != nil {
				return err
			}
			fmt.Printf("auto-skipped %d stale queued candidates\n", len(drained))
		}

		nextSource := run.NextSource()
		if nextSource == nil {
			fmt.Println("no active source remains; run final audit or inspect plan")
			break
		}
		if nextSource.Name != source {
			fmt.Printf("guarded source complete: %s; next source is %s\n", source, nextSource.Name)
			break
		}
		if run.RealSendCapacityRemaining() == 0 {
			return fmt.Errorf("real-send cap reached: %d/%d verified sends", run.VerifiedCount(), run.MaxRealSends)
		}
		if attempts >= options.MaxAttempts {
			fmt.Printf("guarded send stopped after %d attempts\n", attempts)
			break
		}
		candidatePtr := run.NextConnectableObservationForSource(source)
		if candidatePtr == nil {
			fmt.Printf("no unrecorded connectable candidate available for %s; capture more\n", source)
			break
		}
		candidate := *candidatePtr
		attempts++
		fmt.Printf("guarded attempt %d: %s (%s)\n", attempts, candidate.Name, source)

		if options.DryRun || !options.SinglePass {
			attemptDir := filepath.Join(options.OutDir, fmt.Sprintf("attempt-%02d-dry-run", attempts))
			dryStarted := time.Now()
			dryResultPath, err := RunPlaywriterSend(options.Playwriter, *options.Session, options.Script, attemptDir, candidate, true, false)
			if err != nil {
				return err
			}
			dryResult, err := LoadSalesNavSendResult(dryResultPath)
			if err != nil {
				return err
			}
			fmt.Printf("dry-run status: %s\n", dryResult.Status)
			if dryResult.Status != "dry-run-connectable" {
				if !options.NoRecord {
					run, err = store.Load()
					if err != nil {
						return err
					}
					event, err := RecordSendResult(&run, dryResult, dryResultPath)
					if err != nil {
						return err
					}
					PushTiming(&run, "send-guarded-dry-run", &event.Source, dryStarted, ptr(fmt.Sprintf("attempt=%d; status=%s; path=%s", attempts, event.Status, dryResultPath)))
					if err := store.Save(run); err != nil {
						return err
					}
					if err := store.AppendEvent(run, "record-send-result", map[string]any{"path": dryResultPath, "event": event}); err != nil {
						return err
					}
					fmt.Printf("recorded dry-run result as %s\n", event.Status)
				}
				continue
			}
			if options.DryRun {
				fmt.Println("dry run confirmed next guarded candidate; no real send performed")
				break
			}
		} else {
			fmt.Println("single-pass Playwriter send: sender validates Connect before clicking and Pending after sending")
		}

		run, err = store.Load()
		if err != nil {
			return err
		}
		stillActive := false
		if next := run.NextSource(); next != nil && next.Name == source {
			stillActive = true
		}
		if !stillActive {
			fmt.Println("source reached target before real send; stopped before candidate")
			break
		}
		attemptDir := filepath.Join(options.OutDir, fmt.Sprintf("attempt-%02d-send", attempts))
		sendStarted := time.Now()
		resultPath, err := RunPlaywriterSend(options.Playwriter, *options.Session, options.Script, attemptDir, candidate, false, true)
		if err != nil {
			return err
		}
		result, err := LoadSalesNavSendResult(resultPath)
		if err != nil {
			return err
		}
		status := result.Status
		fmt.Printf("send status: %s\n", status)
		if !options.NoRecord {
			run, err = store.Load()
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
			autoExhaustedSource := ""
			if status == "blocked" {
				run.State = RunStateBlocked
				run.Notes = append(run.Notes, fmt.Sprintf("guarded send blocked for %s: %s", event.Name, status))
			} else if IsUncertainSendStatus(status) {
				run.State = RunStateNeedsReaudit
				run.Notes = append(run.Notes, fmt.Sprintf("guarded send stopped after uncertain status for %s: %s", event.Name, status))
				if isSendNoopStatus(status) && SourceRepeatedSendNoop(run, event.Source, 3) {
					for i := range run.Sources {
						if run.Sources[i].Name == event.Source {
							run.Sources[i].Exhausted = true
							break
						}
					}
					autoExhaustedSource = event.Source
					run.Notes = append(run.Notes, fmt.Sprintf("source exhausted after repeated send no-op: %s; three consecutive candidates did not become pending after Send Invitation", event.Source))
				}
			}
			PushTiming(&run, "send-guarded", &event.Source, sendStarted, ptr(fmt.Sprintf("attempt=%d; status=%s; path=%s", attempts, status, resultPath)))
			if err := store.Save(run); err != nil {
				return err
			}
			if err := store.AppendEvent(run, "record-send-result", map[string]any{"path": resultPath, "event": event}); err != nil {
				return err
			}
			if autoExhaustedSource != "" {
				if err := store.AppendEvent(run, "source-exhausted", map[string]any{"source": autoExhaustedSource, "via": "send-guarded-clicked-send-noop"}); err != nil {
					return err
				}
			}
			if len(drained) > 0 {
				if err := store.AppendEvent(run, "drain-stale-candidates", map[string]any{"events": drained}); err != nil {
					return err
				}
				fmt.Printf("auto-skipped %d stale queued candidates\n", len(drained))
			}
			if IsUncertainSendStatus(status) {
				return fmt.Errorf("guarded send stopped on uncertain status %s; import a fresh sent-page audit before continuing", status)
			}
		} else {
			fmt.Println("--no-record set; stopped after one real guarded send")
			break
		}
	}
	run, err = store.Load()
	if err != nil {
		return err
	}
	fmt.Println(RenderReport(run))
	return nil
}

func RunPlaywriterAudit(playwriter, session, script, outPath string) error {
	if err := os.MkdirAll(filepath.Dir(outPath), 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", filepath.Dir(outPath), err)
	}
	outJSON, err := json.Marshal(outPath)
	if err != nil {
		return err
	}
	configJS := fmt.Sprintf("state.salesNavAuditConfig = { out: %s, loadMore: 0 }; console.log(JSON.stringify(state.salesNavAuditConfig));", string(outJSON))
	if err := RunPlaywriterConfig(playwriter, session, configJS); err != nil {
		return err
	}
	return RunPlaywriterFile(playwriter, session, script)
}

func RunPlaywriterCapture(
	playwriter string,
	session string,
	script string,
	outDir string,
	source string,
	url string,
	options CaptureRunOptions,
) (string, error) {
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
	urlJSON, err := json.Marshal(url)
	if err != nil {
		return "", err
	}
	configJS := fmt.Sprintf(
		"state.salesNavCaptureConfig = { out: %s, source: %s, url: %s, limit: %d, pages: %d, stopAfterConnectable: %d, rowScrollDelayMs: %d, openMenus: true, onlyConnectable: %t, saveHtml: false }; console.log(JSON.stringify(state.salesNavCaptureConfig));",
		string(outJSON),
		string(sourceJSON),
		string(urlJSON),
		options.Limit,
		options.Pages,
		options.StopAfterConnectable,
		options.RowScrollDelayMS,
		options.OnlyConnectable,
	)
	if err := RunPlaywriterConfig(playwriter, session, configJS); err != nil {
		return "", err
	}
	timeoutMS := options.TimeoutMS
	if timeoutMS == 0 {
		timeoutMS = 90000
	}
	if err := RunPlaywriterFileWithTimeout(playwriter, session, script, timeoutMS); err != nil {
		return "", err
	}
	return filepath.Join(outDir, "page.json"), nil
}

func RunPlaywriterAcceptedResearch(
	playwriter string,
	session string,
	script string,
	candidatesPath string,
	outPath string,
	publicWeb bool,
	maxWebResults uint32,
	delayMS uint64,
	timeoutMS uint32,
) error {
	if err := os.MkdirAll(filepath.Dir(outPath), 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", filepath.Dir(outPath), err)
	}
	candidatesJSON, err := json.Marshal(candidatesPath)
	if err != nil {
		return err
	}
	outJSON, err := json.Marshal(outPath)
	if err != nil {
		return err
	}
	configJS := fmt.Sprintf(
		"state.salesNavAcceptedResearchConfig = { in: %s, out: %s, publicWeb: %t, maxWebResults: %d, delayMs: %d }; console.log(JSON.stringify(state.salesNavAcceptedResearchConfig));",
		string(candidatesJSON),
		string(outJSON),
		publicWeb,
		maxWebResults,
		delayMS,
	)
	if err := RunPlaywriterConfig(playwriter, session, configJS); err != nil {
		return err
	}
	return RunPlaywriterFileWithTimeout(playwriter, session, script, timeoutMS)
}

func ResolveSavedSearchURL(path string, source string) (*string, error) {
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return nil, nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("reading saved searches %s: %w", path, err)
	}
	var value map[string]json.RawMessage
	if err := json.Unmarshal(raw, &value); err != nil {
		return nil, fmt.Errorf("parsing saved searches %s: %w", path, err)
	}
	searchesRaw, ok := value["searches"]
	if !ok {
		searchesRaw, ok = value["savedSearches"]
	}
	if !ok {
		return nil, fmt.Errorf("saved searches artifact has no searches array: %s", path)
	}
	var searches []map[string]any
	if err := json.Unmarshal(searchesRaw, &searches); err != nil {
		return nil, fmt.Errorf("parsing saved searches %s: %w", path, err)
	}
	for _, row := range searches {
		name, _ := row["name"].(string)
		if name != source {
			continue
		}
		if viewURL, ok := row["viewUrl"].(string); ok {
			return &viewURL, nil
		}
		if viewURL, ok := row["view_url"].(string); ok {
			return &viewURL, nil
		}
	}
	return nil, nil
}

func ResolveCaptureURL(explicitURL *string, savedSearches string, source string, flagName string) (string, error) {
	if explicitURL != nil && *explicitURL != "" {
		return *explicitURL, nil
	}
	url, err := ResolveSavedSearchURL(savedSearches, source)
	if err != nil {
		return "", err
	}
	if url == nil {
		if flagName == "" {
			flagName = "--url"
		}
		return "", fmt.Errorf("no URL for source %s; pass %s/--fallback-url or resolve saved searches into %s", source, flagName, savedSearches)
	}
	return *url, nil
}

type ReconcileAuditOptions struct {
	Session    *string
	Playwriter string
	Script     string
	OutDir     string
	Attempts   uint32
	DelayMS    uint64
	Finish     bool
}

func HandleReconcileAudit(store *Store, options ReconcileAuditOptions) error {
	if options.Session == nil {
		return fmt.Errorf("--session is required to execute Playwriter")
	}
	if options.Attempts == 0 {
		options.Attempts = 1
	}
	var latestDelta *int64
	for attempt := uint32(1); attempt <= options.Attempts; attempt++ {
		started := time.Now()
		outPath := filepath.Join(options.OutDir, fmt.Sprintf("audit-%02d.json", attempt))
		if err := RunPlaywriterAudit(options.Playwriter, *options.Session, options.Script, outPath); err != nil {
			return err
		}
		audit, err := LoadSalesNavAudit(outPath)
		if err != nil {
			return err
		}
		run, err := store.Load()
		if err != nil {
			return err
		}
		ApplyAudit(&run, audit.PeopleCount, ptr(fmt.Sprintf("reconcile audit attempt %d/%d", attempt, options.Attempts)))
		latestDelta = run.AuditedDelta()
		shouldFinish := options.Finish && latestDelta != nil && *latestDelta == int64(run.Target)
		if shouldFinish {
			run.State = RunStateDone
		}
		PushTiming(&run, "reconcile-audit", nil, started, ptr(fmt.Sprintf("attempt %d/%d; people %d", attempt, options.Attempts, audit.PeopleCount)))
		if err := store.Save(run); err != nil {
			return err
		}
		if err := store.AppendEvent(run, "reconcile-audit", map[string]any{
			"attempt":      attempt,
			"path":         outPath,
			"people_count": audit.PeopleCount,
			"delta":        latestDelta,
			"finished":     shouldFinish,
		}); err != nil {
			return err
		}
		if shouldFinish {
			if err := store.AppendEvent(run, "finish", map[string]any{"audited_delta": latestDelta}); err != nil {
				return err
			}
		}
		fmt.Printf("reconcile audit %d/%d: People (%d), delta %s\n", attempt, options.Attempts, audit.PeopleCount, FormatDelta(latestDelta))
		if latestDelta != nil && *latestDelta == int64(run.Target) {
			break
		}
		if attempt < options.Attempts {
			time.Sleep(time.Duration(options.DelayMS) * time.Millisecond)
		}
	}
	if options.Finish && latestDelta != nil {
		run, err := store.Load()
		if err != nil {
			return err
		}
		if run.State != RunStateDone && *latestDelta != int64(run.Target) {
			return fmt.Errorf("final audit delta is %s, expected %d; top up or re-run reconcile-audit", FormatDelta(latestDelta), run.Target)
		}
	}
	run, err := store.Load()
	if err != nil {
		return err
	}
	fmt.Println(RenderReport(run))
	return nil
}

type TopUpReconcileOptions struct {
	Session     *string
	Playwriter  string
	SendScript  string
	AuditScript string
	Fallback    TopUpFallbackOptions
	OutDir      string
	MaxAttempts uint32
	DelayMS     uint64
	AllowSend   bool
	Finish      bool
}

func HandleTopUpReconcile(store *Store, options TopUpReconcileOptions) error {
	if !options.AllowSend {
		return fmt.Errorf("top-up reconciliation can send real invites; pass --allow-send to continue")
	}
	if options.Session == nil {
		return fmt.Errorf("--session is required to execute Playwriter")
	}
	if err := os.MkdirAll(options.OutDir, 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", options.OutDir, err)
	}
	if options.MaxAttempts == 0 {
		options.MaxAttempts = 1
	}
	for attempt := uint32(1); attempt <= options.MaxAttempts; attempt++ {
		run, err := store.Load()
		if err != nil {
			return err
		}
		if delta := run.AuditedDelta(); delta != nil && *delta == int64(run.Target) {
			if options.Finish && run.State != RunStateDone {
				run.State = RunStateDone
				run.MarkUpdated()
				if err := store.Save(run); err != nil {
					return err
				}
				if err := store.AppendEvent(run, "finish", map[string]any{"audited_delta": run.AuditedDelta(), "via": "top-up-reconcile"}); err != nil {
					return err
				}
			}
			fmt.Println("audited delta already matches target; no top-up needed")
			break
		}
		if delta := run.AuditedDelta(); delta != nil && *delta > int64(run.Target) {
			return fmt.Errorf("audited delta %s already exceeds target %d; stopping", FormatDelta(delta), run.Target)
		}
		if run.VerifiedCount() < run.Target {
			return fmt.Errorf("row-level verified sends are %d/%d; continue normal guarded sends before audit top-up", run.VerifiedCount(), run.Target)
		}
		var candidate CandidateObservation
		if next := run.NextTopUpObservation(); next != nil {
			candidate = *next
		} else {
			prepared, err := PrepareTopUpCandidate(store, options.Playwriter, *options.Session, options.OutDir, options.Fallback, attempt)
			if err != nil {
				return err
			}
			if prepared == nil {
				return fmt.Errorf("no distinct connectable candidate available for top-up")
			}
			candidate = *prepared
		}
		fmt.Printf("top-up attempt %d/%d: %s (%s)\n", attempt, options.MaxAttempts, candidate.Name, candidate.Source)
		sendStarted := time.Now()
		sendDir := filepath.Join(options.OutDir, fmt.Sprintf("attempt-%02d-send", attempt))
		resultPath, err := RunPlaywriterSend(options.Playwriter, *options.Session, options.SendScript, sendDir, candidate, false, true)
		if err != nil {
			return err
		}
		result, err := LoadSalesNavSendResult(resultPath)
		if err != nil {
			return err
		}
		status := result.Status
		run, err = store.Load()
		if err != nil {
			return err
		}
		event, err := RecordTopUpSendResult(&run, result, resultPath, ptr("controller top-up reconciliation"))
		if err != nil {
			return err
		}
		PushTiming(&run, "top-up-send", &candidate.Source, sendStarted, ptr(fmt.Sprintf("attempt %d; status %s", attempt, status)))
		if err := store.Save(run); err != nil {
			return err
		}
		if err := store.AppendEvent(run, "record-top-up-result", map[string]any{"path": resultPath, "event": event, "via": "top-up-reconcile"}); err != nil {
			return err
		}
		fmt.Printf("top-up send status: %s\n", status)
		if event.Status != CandidateStatusAuditTopUp {
			fmt.Println("top-up did not send a verified invite; trying next distinct candidate")
			continue
		}
		if options.DelayMS > 0 {
			time.Sleep(time.Duration(options.DelayMS) * time.Millisecond)
		}
		auditStarted := time.Now()
		auditPath := filepath.Join(options.OutDir, fmt.Sprintf("attempt-%02d-audit.json", attempt))
		if err := RunPlaywriterAudit(options.Playwriter, *options.Session, options.AuditScript, auditPath); err != nil {
			return err
		}
		audit, err := LoadSalesNavAudit(auditPath)
		if err != nil {
			return err
		}
		run, err = store.Load()
		if err != nil {
			return err
		}
		ApplyAudit(&run, audit.PeopleCount, ptr(fmt.Sprintf("top-up reconcile audit attempt %d/%d", attempt, options.MaxAttempts)))
		PushTiming(&run, "top-up-audit", nil, auditStarted, ptr(fmt.Sprintf("attempt %d; people %d", attempt, audit.PeopleCount)))
		latestDelta := run.AuditedDelta()
		shouldFinish := options.Finish && latestDelta != nil && *latestDelta == int64(run.Target)
		if shouldFinish {
			run.State = RunStateDone
		}
		if err := store.Save(run); err != nil {
			return err
		}
		if err := store.AppendEvent(run, "top-up-reconcile-audit", map[string]any{
			"attempt":      attempt,
			"path":         auditPath,
			"people_count": audit.PeopleCount,
			"delta":        latestDelta,
			"finished":     shouldFinish,
		}); err != nil {
			return err
		}
		if shouldFinish {
			if err := store.AppendEvent(run, "finish", map[string]any{"audited_delta": latestDelta, "via": "top-up-reconcile"}); err != nil {
				return err
			}
		}
		fmt.Printf("top-up audit %d/%d: People (%d), delta %s\n", attempt, options.MaxAttempts, audit.PeopleCount, FormatDelta(latestDelta))
		if latestDelta != nil && *latestDelta == int64(run.Target) {
			break
		}
	}
	run, err := store.Load()
	if err != nil {
		return err
	}
	if options.Finish && run.State != RunStateDone {
		return fmt.Errorf("final audit delta is %s, expected %d; top-up reconciliation did not finish", FormatDelta(run.AuditedDelta()), run.Target)
	}
	fmt.Println(RenderReport(run))
	return nil
}

func PrepareTopUpCandidate(
	store *Store,
	playwriter string,
	session string,
	outDir string,
	fallback TopUpFallbackOptions,
	attempt uint32,
) (*CandidateObservation, error) {
	run, err := store.Load()
	if err != nil {
		return nil, err
	}
	if !run.FinalAuditIsShort() {
		return nil, nil
	}
	if err := EnsureKnownSource(run, fallback.Source); err != nil {
		return nil, err
	}
	if !run.SourceIsFallback(fallback.Source) {
		return nil, fmt.Errorf("top-up fallback source is not marked fallback: %s", fallback.Source)
	}
	reservoir, err := store.LoadReservoir()
	if err != nil {
		return nil, err
	}
	imported, err := FillRunFromReservoirForTopUp(&run, &reservoir, fallback.Source, int(fallback.Limit))
	if err != nil {
		return nil, err
	}
	if imported > 0 {
		if err := store.Save(run); err != nil {
			return nil, err
		}
		if err := store.SaveReservoir(reservoir); err != nil {
			return nil, err
		}
		if err := store.AppendEvent(run, "top-up-reservoir-fill", map[string]any{"source": fallback.Source, "imported": imported}); err != nil {
			return nil, err
		}
		fmt.Printf("filled final top-up queue from reservoir: %d candidates from %s\n", imported, fallback.Source)
		if candidate := run.NextTopUpObservation(); candidate != nil {
			copy := *candidate
			return &copy, nil
		}
	}
	if !fallback.CaptureEnabled {
		return nil, nil
	}
	url, err := ResolveCaptureURL(fallback.URL, fallback.SavedSearches, fallback.Source, "--fallback-url")
	if err != nil {
		return nil, err
	}
	captureDir := filepath.Join(outDir, fmt.Sprintf("attempt-%02d-fallback-capture", attempt))
	captureStarted := time.Now()
	capturePath, err := RunPlaywriterCapture(playwriter, session, fallback.CaptureScript, captureDir, fallback.Source, url, CaptureRunOptions{
		Pages:                fallback.Pages,
		StopAfterConnectable: fallback.StopAfterConnectable,
		Limit:                fallback.Limit,
		RowScrollDelayMS:     fallback.RowScrollDelayMS,
		OnlyConnectable:      true,
	})
	if err != nil {
		return nil, err
	}
	capture, err := LoadSalesNavCapture(capturePath)
	if err != nil {
		return nil, err
	}
	run, err = store.Load()
	if err != nil {
		return nil, err
	}
	imported, err = ImportCapture(&run, capture, ImportCaptureOptions{OnlyConnectable: true})
	if err != nil {
		return nil, err
	}
	PushTiming(&run, "top-up-fallback-capture", &fallback.Source, captureStarted, ptr(fmt.Sprintf("imported=%d; path=%s", imported, capturePath)))
	run.MarkUpdated()
	if err := store.Save(run); err != nil {
		return nil, err
	}
	if err := store.AppendEvent(run, "top-up-fallback-capture", map[string]any{"source": fallback.Source, "path": capturePath, "imported": imported}); err != nil {
		return nil, err
	}
	fmt.Printf("captured fallback top-up queue: %d candidates from %s\n", imported, fallback.Source)
	if candidate := run.NextTopUpObservation(); candidate != nil {
		copy := *candidate
		return &copy, nil
	}
	return nil, nil
}
