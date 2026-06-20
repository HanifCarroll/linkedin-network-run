package app

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

type Store struct {
	Dir string
}

func NewStore(stateDir string) (*Store, error) {
	dir := stateDir
	if strings.TrimSpace(dir) == "" {
		resolved, err := dataLocalDir()
		if err != nil {
			return nil, err
		}
		dir = filepath.Join(resolved, appDir)
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, fmt.Errorf("creating %s: %w", dir, err)
	}
	return &Store{Dir: dir}, nil
}

func dataLocalDir() (string, error) {
	if runtime.GOOS == "darwin" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("could not resolve local data directory: %w", err)
		}
		return filepath.Join(home, "Library", "Application Support"), nil
	}
	if value := os.Getenv("XDG_DATA_HOME"); strings.TrimSpace(value) != "" {
		return value, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("could not resolve local data directory: %w", err)
	}
	return filepath.Join(home, ".local", "share"), nil
}

func (s Store) ActivePath() string {
	return filepath.Join(s.Dir, "active.json")
}

func (s Store) PendingActivePath() string {
	return filepath.Join(s.Dir, "pending-cleanup-active.json")
}

func (s Store) AcceptanceLedgerPath() string {
	return filepath.Join(s.Dir, "acceptance-ledger.json")
}

func (s Store) AcceptanceFollowupLedgerPath() string {
	return filepath.Join(s.Dir, "acceptance-followups.json")
}

func (s Store) AcceptanceFollowupReportsDir() string {
	return filepath.Join(s.Dir, "acceptance-followups")
}

func (s Store) DefaultAcceptanceFollowupReportPath() string {
	return filepath.Join(s.AcceptanceFollowupReportsDir(), Today().String()+".md")
}

func (s Store) AcceptanceEventPath() string {
	return filepath.Join(s.Dir, "acceptance-events.jsonl")
}

func (s Store) ReservoirPath() string {
	return filepath.Join(s.Dir, "candidate-reservoir.json")
}

func (s Store) EventPath(run Run) string {
	return filepath.Join(s.Dir, run.ID.String()+".jsonl")
}

func (s Store) PendingEventPath(run PendingCleanupRun) string {
	return filepath.Join(s.Dir, "pending-cleanup-"+run.ID.String()+".jsonl")
}

func (s Store) Load() (Run, error) {
	var run Run
	if err := readJSONFile(s.ActivePath(), &run, "loading active run", "parsing active run"); err != nil {
		return Run{}, err
	}
	run.Normalize()
	return run, nil
}

func (s Store) Save(run Run) error {
	run.Normalize()
	return writeJSONAtomic(s.ActivePath(), run)
}

func (s Store) LoadPending() (PendingCleanupRun, error) {
	var run PendingCleanupRun
	if err := readJSONFile(s.PendingActivePath(), &run, "loading active pending-cleanup run", "parsing active pending-cleanup run"); err != nil {
		return PendingCleanupRun{}, err
	}
	run.Normalize()
	return run, nil
}

func (s Store) SavePending(run PendingCleanupRun) error {
	run.Normalize()
	return writeJSONAtomic(s.PendingActivePath(), run)
}

func (s Store) LoadAcceptanceLedger() (AcceptanceLedger, error) {
	path := s.AcceptanceLedgerPath()
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return AcceptanceLedger{Invitations: []AcceptanceInvitation{}}, nil
	}
	var ledger AcceptanceLedger
	if err := readJSONFile(path, &ledger, "loading "+path, "parsing "+path); err != nil {
		return AcceptanceLedger{}, err
	}
	ledger.Normalize()
	return ledger, nil
}

func (s Store) SaveAcceptanceLedger(ledger AcceptanceLedger) error {
	ledger.Normalize()
	return writeJSONAtomic(s.AcceptanceLedgerPath(), ledger)
}

func (s Store) LoadAcceptanceFollowupLedger() (AcceptanceFollowupLedger, error) {
	path := s.AcceptanceFollowupLedgerPath()
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return AcceptanceFollowupLedger{Drafts: []AcceptanceFollowupRecord{}}, nil
	}
	var ledger AcceptanceFollowupLedger
	if err := readJSONFile(path, &ledger, "loading "+path, "parsing "+path); err != nil {
		return AcceptanceFollowupLedger{}, err
	}
	ledger.Normalize()
	return ledger, nil
}

func (s Store) SaveAcceptanceFollowupLedger(ledger AcceptanceFollowupLedger) error {
	ledger.Normalize()
	return writeJSONAtomic(s.AcceptanceFollowupLedgerPath(), ledger)
}

func (s Store) LoadReservoir() (CandidateReservoir, error) {
	path := s.ReservoirPath()
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return CandidateReservoir{Observations: []CandidateObservation{}}, nil
	}
	var reservoir CandidateReservoir
	if err := readJSONFile(path, &reservoir, "loading "+path, "parsing "+path); err != nil {
		return CandidateReservoir{}, err
	}
	if reservoir.Observations == nil {
		reservoir.Observations = []CandidateObservation{}
	}
	for i := range reservoir.Observations {
		if len(reservoir.Observations[i].VisibleState) == 0 {
			reservoir.Observations[i].VisibleState = json.RawMessage("null")
		}
		if reservoir.Observations[i].MenuLabels == nil {
			reservoir.Observations[i].MenuLabels = []string{}
		}
	}
	return reservoir, nil
}

func (s Store) SaveReservoir(reservoir CandidateReservoir) error {
	if reservoir.Observations == nil {
		reservoir.Observations = []CandidateObservation{}
	}
	return writeJSONAtomic(s.ReservoirPath(), reservoir)
}

func (s Store) SeedAcceptanceFromHistory(ledger *AcceptanceLedger) (AcceptanceHistorySeedSummary, error) {
	summary := AcceptanceHistorySeedSummary{}
	entries, err := os.ReadDir(s.Dir)
	if err != nil {
		return summary, fmt.Errorf("reading %s: %w", s.Dir, err)
	}
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".jsonl" {
			continue
		}
		stem := strings.TrimSuffix(entry.Name(), filepath.Ext(entry.Name()))
		runID, err := parseUUID(stem)
		if err != nil {
			continue
		}
		path := filepath.Join(s.Dir, entry.Name())
		runDate, events, ok, err := SentEventsFromControllerLog(path, runID)
		if err != nil {
			return summary, err
		}
		if !ok {
			continue
		}
		summary.RunLogs++
		summary.SentEvents += uint32(len(events))
		summary.Seeded += ledger.UpsertFromEvents(runID, runDate, events)
	}
	return summary, nil
}

func (s Store) AppendEvent(run Run, kind string, payload any) error {
	event := map[string]any{
		"at":      time.Now(),
		"run_id":  run.ID,
		"kind":    kind,
		"payload": payload,
	}
	return appendJSONLine(s.EventPath(run), event, "opening event log", "writing event log")
}

func (s Store) AppendAcceptanceEvent(kind string, payload any) error {
	event := map[string]any{
		"at":      time.Now(),
		"kind":    kind,
		"payload": payload,
	}
	return appendJSONLine(s.AcceptanceEventPath(), event, "opening acceptance event log", "writing acceptance event log")
}

func (s Store) AppendPendingEvent(run PendingCleanupRun, kind string, payload any) error {
	event := map[string]any{
		"at":      time.Now(),
		"run_id":  run.ID,
		"kind":    kind,
		"payload": payload,
	}
	return appendJSONLine(s.PendingEventPath(run), event, "opening pending-cleanup event log", "writing pending-cleanup event log")
}

func readJSONFile(path string, target any, readContext string, parseContext string) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("%s: %w", readContext, err)
	}
	if err := json.Unmarshal(raw, target); err != nil {
		return fmt.Errorf("%s: %w", parseContext, err)
	}
	return nil
}

func writeJSONAtomic(path string, value any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", filepath.Dir(path), err)
	}
	tmp := strings.TrimSuffix(path, filepath.Ext(path)) + filepath.Ext(path) + ".tmp"
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	if err := os.WriteFile(tmp, raw, 0o644); err != nil {
		return fmt.Errorf("writing %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		return fmt.Errorf("replacing %s: %w", path, err)
	}
	return nil
}

func appendJSONLine(path string, value any, openContext string, writeContext string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", filepath.Dir(path), err)
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return fmt.Errorf("%s: %w", openContext, err)
	}
	defer file.Close()
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	if _, err := file.Write(append(raw, '\n')); err != nil {
		return fmt.Errorf("%s: %w", writeContext, err)
	}
	return nil
}

func readJSONLines(path string, fn func(lineNumber int, raw []byte) error) error {
	file, err := os.Open(path)
	if err != nil {
		return fmt.Errorf("opening %s: %w", path, err)
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	lineNumber := 0
	for scanner.Scan() {
		lineNumber++
		raw := strings.TrimSpace(scanner.Text())
		if raw == "" {
			continue
		}
		if err := fn(lineNumber, []byte(raw)); err != nil {
			return err
		}
	}
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("reading %s: %w", path, err)
	}
	return nil
}
