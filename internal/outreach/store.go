package outreach

import (
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
	dir := strings.TrimSpace(stateDir)
	if dir == "" {
		base, err := dataLocalDir()
		if err != nil {
			return nil, err
		}
		dir = filepath.Join(base, AppDir)
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

func (s Store) StatePath() string {
	return filepath.Join(s.Dir, "outreach.json")
}

func (s Store) DefaultDraftReportPath() string {
	return filepath.Join(s.Dir, "drafts", time.Now().Format("2006-01-02")+".md")
}

func (s Store) DefaultDailyDashboardPath() string {
	return filepath.Join(s.Dir, "dashboards", time.Now().Format("2006-01-02")+".md")
}

func (s Store) Load() (OutreachState, error) {
	path := s.StatePath()
	if _, err := os.Stat(path); os.IsNotExist(err) {
		state := OutreachState{
			SchemaVersion:  1,
			Leads:          []Lead{},
			CaptureCursors: map[string]CaptureCursor{},
			UpdatedAt:      time.Now(),
		}
		return state, nil
	}
	var state OutreachState
	raw, err := os.ReadFile(path)
	if err != nil {
		return OutreachState{}, fmt.Errorf("loading %s: %w", path, err)
	}
	if err := json.Unmarshal(raw, &state); err != nil {
		return OutreachState{}, fmt.Errorf("parsing %s: %w", path, err)
	}
	state.Normalize()
	return state, nil
}

func (s Store) Save(state OutreachState) error {
	state.Normalize()
	state.UpdatedAt = time.Now()
	if err := os.MkdirAll(filepath.Dir(s.StatePath()), 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", filepath.Dir(s.StatePath()), err)
	}
	raw, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	tmp := s.StatePath() + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o644); err != nil {
		return fmt.Errorf("writing %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, s.StatePath()); err != nil {
		return fmt.Errorf("renaming %s to %s: %w", tmp, s.StatePath(), err)
	}
	return nil
}
