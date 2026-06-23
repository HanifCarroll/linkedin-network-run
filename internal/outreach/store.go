package outreach

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	_ "modernc.org/sqlite"
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
	return s.DatabasePath()
}

func (s Store) JSONStatePath() string {
	return filepath.Join(s.Dir, "outreach.json")
}

func (s Store) DatabasePath() string {
	return filepath.Join(s.Dir, "outreach.sqlite")
}

func (s Store) DefaultDraftReportPath() string {
	return filepath.Join(s.Dir, "drafts", time.Now().Format("2006-01-02")+".md")
}

func (s Store) DefaultDailyDashboardPath() string {
	return filepath.Join(s.Dir, "dashboards", time.Now().Format("2006-01-02")+".md")
}

func (s Store) Load() (OutreachState, error) {
	if _, err := os.Stat(s.DatabasePath()); os.IsNotExist(err) {
		state, err := s.loadJSONState()
		if err != nil {
			return OutreachState{}, err
		}
		if len(state.Leads) > 0 || len(state.AgencyAccounts) > 0 || len(state.AgencyContactCandidates) > 0 || len(state.CaptureCursors) > 0 {
			if err := s.Save(state); err != nil {
				return OutreachState{}, err
			}
		}
		return state, nil
	}
	db, err := s.openDB()
	if err != nil {
		return OutreachState{}, err
	}
	defer db.Close()
	if err := ensureSQLiteSchema(db); err != nil {
		return OutreachState{}, err
	}
	state, err := loadSQLiteState(db)
	if err != nil {
		return OutreachState{}, err
	}
	state.Normalize()
	return state, nil
}

func (s Store) loadJSONState() (OutreachState, error) {
	path := s.JSONStatePath()
	if _, err := os.Stat(path); os.IsNotExist(err) {
		state := OutreachState{
			SchemaVersion:           1,
			Leads:                   []Lead{},
			AgencyAccounts:          []AgencyAccount{},
			AgencyContactCandidates: []AgencyContactCandidate{},
			CaptureCursors:          map[string]CaptureCursor{},
			UpdatedAt:               time.Now(),
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
	if err := os.MkdirAll(filepath.Dir(s.DatabasePath()), 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", filepath.Dir(s.DatabasePath()), err)
	}
	db, err := s.openDB()
	if err != nil {
		return err
	}
	defer db.Close()
	if err := ensureSQLiteSchema(db); err != nil {
		return err
	}
	return saveSQLiteState(db, state)
}

func (s Store) openDB() (*sql.DB, error) {
	db, err := sql.Open("sqlite", s.DatabasePath())
	if err != nil {
		return nil, fmt.Errorf("opening %s: %w", s.DatabasePath(), err)
	}
	db.SetMaxOpenConns(1)
	return db, nil
}

func ensureSQLiteSchema(db *sql.DB) error {
	statements := []string{
		`PRAGMA journal_mode = WAL`,
		`PRAGMA foreign_keys = ON`,
		`CREATE TABLE IF NOT EXISTS meta (
			key TEXT PRIMARY KEY,
			value TEXT NOT NULL
		)`,
		`CREATE TABLE IF NOT EXISTS leads (
			id TEXT PRIMARY KEY,
			data TEXT NOT NULL
		)`,
		`CREATE TABLE IF NOT EXISTS drafts (
			lead_id TEXT PRIMARY KEY,
			subject TEXT NOT NULL DEFAULT '',
			body TEXT NOT NULL,
			angle TEXT NOT NULL,
			evidence_json TEXT NOT NULL,
			generated_at TEXT NOT NULL,
			FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
		)`,
		`CREATE TABLE IF NOT EXISTS send_attempts (
			lead_id TEXT NOT NULL,
			position INTEGER NOT NULL,
			data TEXT NOT NULL,
			PRIMARY KEY (lead_id, position),
			FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
		)`,
		`CREATE TABLE IF NOT EXISTS agency_accounts (
			id TEXT PRIMARY KEY,
			data TEXT NOT NULL
		)`,
		`CREATE TABLE IF NOT EXISTS agency_contact_candidates (
			id TEXT PRIMARY KEY,
			data TEXT NOT NULL
		)`,
		`CREATE TABLE IF NOT EXISTS capture_cursors (
			source TEXT PRIMARY KEY,
			data TEXT NOT NULL
		)`,
		`CREATE TABLE IF NOT EXISTS run_events (
			position INTEGER PRIMARY KEY,
			data TEXT NOT NULL
		)`,
	}
	for _, statement := range statements {
		if _, err := db.Exec(statement); err != nil {
			return fmt.Errorf("applying sqlite schema: %w", err)
		}
	}
	if err := ensureSQLiteColumn(db, "drafts", "subject", "TEXT NOT NULL DEFAULT ''"); err != nil {
		return err
	}
	return nil
}

func ensureSQLiteColumn(db *sql.DB, table string, column string, definition string) error {
	rows, err := db.Query("PRAGMA table_info(" + table + ")")
	if err != nil {
		return fmt.Errorf("checking sqlite table %s: %w", table, err)
	}
	defer rows.Close()
	for rows.Next() {
		var cid int
		var name, columnType string
		var notNull int
		var defaultValue sql.NullString
		var primaryKey int
		if err := rows.Scan(&cid, &name, &columnType, &notNull, &defaultValue, &primaryKey); err != nil {
			return fmt.Errorf("scanning sqlite table %s: %w", table, err)
		}
		if name == column {
			return nil
		}
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("iterating sqlite table %s: %w", table, err)
	}
	if _, err := db.Exec(fmt.Sprintf("ALTER TABLE %s ADD COLUMN %s %s", table, column, definition)); err != nil {
		return fmt.Errorf("adding sqlite column %s.%s: %w", table, column, err)
	}
	return nil
}

func loadSQLiteState(db *sql.DB) (OutreachState, error) {
	state := OutreachState{
		SchemaVersion:           1,
		Leads:                   []Lead{},
		AgencyAccounts:          []AgencyAccount{},
		AgencyContactCandidates: []AgencyContactCandidate{},
		CaptureCursors:          map[string]CaptureCursor{},
		UpdatedAt:               time.Now(),
	}
	if value, ok, err := metaValue(db, "schema_version"); err != nil {
		return OutreachState{}, err
	} else if ok {
		if _, err := fmt.Sscanf(value, "%d", &state.SchemaVersion); err != nil {
			return OutreachState{}, fmt.Errorf("parsing schema_version %q: %w", value, err)
		}
	}
	if value, ok, err := metaValue(db, "updated_at"); err != nil {
		return OutreachState{}, err
	} else if ok {
		updatedAt, err := time.Parse(time.RFC3339Nano, value)
		if err != nil {
			return OutreachState{}, fmt.Errorf("parsing updated_at %q: %w", value, err)
		}
		state.UpdatedAt = updatedAt
	}
	leads, err := loadSQLiteLeads(db)
	if err != nil {
		return OutreachState{}, err
	}
	state.Leads = leads
	accounts, err := loadJSONRows[AgencyAccount](db, "SELECT data FROM agency_accounts ORDER BY id")
	if err != nil {
		return OutreachState{}, err
	}
	state.AgencyAccounts = accounts
	candidates, err := loadJSONRows[AgencyContactCandidate](db, "SELECT data FROM agency_contact_candidates ORDER BY id")
	if err != nil {
		return OutreachState{}, err
	}
	state.AgencyContactCandidates = candidates
	cursors, err := loadJSONRows[CaptureCursor](db, "SELECT data FROM capture_cursors ORDER BY source")
	if err != nil {
		return OutreachState{}, err
	}
	for _, cursor := range cursors {
		state.CaptureCursors[cursor.Source] = cursor
	}
	events, err := loadJSONRows[RunEvent](db, "SELECT data FROM run_events ORDER BY position")
	if err != nil {
		return OutreachState{}, err
	}
	state.RunEvents = events
	state.Normalize()
	return state, nil
}

func metaValue(db *sql.DB, key string) (string, bool, error) {
	var value string
	err := db.QueryRow("SELECT value FROM meta WHERE key = ?", key).Scan(&value)
	if err == sql.ErrNoRows {
		return "", false, nil
	}
	if err != nil {
		return "", false, fmt.Errorf("loading meta %q: %w", key, err)
	}
	return value, true, nil
}

func loadSQLiteLeads(db *sql.DB) ([]Lead, error) {
	leads, err := loadJSONRows[Lead](db, "SELECT data FROM leads ORDER BY id")
	if err != nil {
		return nil, err
	}
	for i := range leads {
		draft, ok, err := loadSQLiteDraft(db, leads[i].ID)
		if err != nil {
			return nil, err
		}
		if ok {
			leads[i].Draft = &draft
		}
		attempts, err := loadJSONRows[SendAttempt](db, "SELECT data FROM send_attempts WHERE lead_id = ? ORDER BY position", leads[i].ID)
		if err != nil {
			return nil, err
		}
		leads[i].SendAttempts = attempts
		leads[i].Normalize()
	}
	return leads, nil
}

func loadSQLiteDraft(db *sql.DB, leadID string) (MessageDraft, bool, error) {
	var draft MessageDraft
	var evidenceJSON, generatedAt string
	err := db.QueryRow("SELECT subject, body, angle, evidence_json, generated_at FROM drafts WHERE lead_id = ?", leadID).
		Scan(&draft.Subject, &draft.Body, &draft.Angle, &evidenceJSON, &generatedAt)
	if err == sql.ErrNoRows {
		return MessageDraft{}, false, nil
	}
	if err != nil {
		return MessageDraft{}, false, fmt.Errorf("loading draft for %s: %w", leadID, err)
	}
	if err := json.Unmarshal([]byte(evidenceJSON), &draft.Evidence); err != nil {
		return MessageDraft{}, false, fmt.Errorf("parsing draft evidence for %s: %w", leadID, err)
	}
	parsedGeneratedAt, err := time.Parse(time.RFC3339Nano, generatedAt)
	if err != nil {
		return MessageDraft{}, false, fmt.Errorf("parsing draft generated_at for %s: %w", leadID, err)
	}
	draft.GeneratedAt = parsedGeneratedAt
	return draft, true, nil
}

func loadJSONRows[T any](db *sql.DB, query string, args ...any) ([]T, error) {
	rows, err := db.Query(query, args...)
	if err != nil {
		return nil, fmt.Errorf("querying sqlite rows: %w", err)
	}
	defer rows.Close()
	items := []T{}
	for rows.Next() {
		var raw string
		if err := rows.Scan(&raw); err != nil {
			return nil, fmt.Errorf("scanning sqlite row: %w", err)
		}
		var item T
		if err := json.Unmarshal([]byte(raw), &item); err != nil {
			return nil, fmt.Errorf("parsing sqlite row JSON: %w", err)
		}
		items = append(items, item)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating sqlite rows: %w", err)
	}
	return items, nil
}

func saveSQLiteState(db *sql.DB, state OutreachState) error {
	tx, err := db.Begin()
	if err != nil {
		return fmt.Errorf("starting sqlite transaction: %w", err)
	}
	defer tx.Rollback()
	for _, statement := range []string{
		"DELETE FROM send_attempts",
		"DELETE FROM drafts",
		"DELETE FROM leads",
		"DELETE FROM agency_accounts",
		"DELETE FROM agency_contact_candidates",
		"DELETE FROM capture_cursors",
		"DELETE FROM run_events",
		"DELETE FROM meta",
	} {
		if _, err := tx.Exec(statement); err != nil {
			return fmt.Errorf("clearing sqlite table: %w", err)
		}
	}
	if _, err := tx.Exec("INSERT INTO meta (key, value) VALUES (?, ?), (?, ?)",
		"schema_version", fmt.Sprintf("%d", state.SchemaVersion),
		"updated_at", state.UpdatedAt.Format(time.RFC3339Nano),
	); err != nil {
		return fmt.Errorf("saving sqlite metadata: %w", err)
	}
	for _, lead := range state.Leads {
		if err := saveSQLiteLead(tx, lead); err != nil {
			return err
		}
	}
	for _, account := range state.AgencyAccounts {
		if err := insertJSON(tx, "INSERT INTO agency_accounts (id, data) VALUES (?, ?)", account.ID, account); err != nil {
			return fmt.Errorf("saving agency account %s: %w", account.ID, err)
		}
	}
	for _, candidate := range state.AgencyContactCandidates {
		if err := insertJSON(tx, "INSERT INTO agency_contact_candidates (id, data) VALUES (?, ?)", candidate.ID, candidate); err != nil {
			return fmt.Errorf("saving agency contact candidate %s: %w", candidate.ID, err)
		}
	}
	for source, cursor := range state.CaptureCursors {
		if cursor.Source == "" {
			cursor.Source = source
		}
		if err := insertJSON(tx, "INSERT INTO capture_cursors (source, data) VALUES (?, ?)", cursor.Source, cursor); err != nil {
			return fmt.Errorf("saving capture cursor %s: %w", cursor.Source, err)
		}
	}
	for index, event := range state.RunEvents {
		if err := insertJSON(tx, "INSERT INTO run_events (position, data) VALUES (?, ?)", index, event); err != nil {
			return fmt.Errorf("saving run event %d: %w", index, err)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("committing sqlite state: %w", err)
	}
	return nil
}

func saveSQLiteLead(tx *sql.Tx, lead Lead) error {
	draft := lead.Draft
	attempts := lead.SendAttempts
	lead.Draft = nil
	lead.SendAttempts = nil
	if err := insertJSON(tx, "INSERT INTO leads (id, data) VALUES (?, ?)", lead.ID, lead); err != nil {
		return fmt.Errorf("saving lead %s: %w", lead.ID, err)
	}
	if draft != nil {
		evidenceJSON, err := json.Marshal(draft.Evidence)
		if err != nil {
			return fmt.Errorf("serializing draft evidence for %s: %w", lead.ID, err)
		}
		if _, err := tx.Exec(
			"INSERT INTO drafts (lead_id, subject, body, angle, evidence_json, generated_at) VALUES (?, ?, ?, ?, ?, ?)",
			lead.ID,
			draft.Subject,
			draft.Body,
			draft.Angle,
			string(evidenceJSON),
			draft.GeneratedAt.Format(time.RFC3339Nano),
		); err != nil {
			return fmt.Errorf("saving draft for %s: %w", lead.ID, err)
		}
	}
	for index, attempt := range attempts {
		if err := insertJSON(tx, "INSERT INTO send_attempts (lead_id, position, data) VALUES (?, ?, ?)", lead.ID, index, attempt); err != nil {
			return fmt.Errorf("saving send attempt %d for %s: %w", index, lead.ID, err)
		}
	}
	return nil
}

func insertJSON(tx *sql.Tx, statement string, args ...any) error {
	if len(args) == 0 {
		return fmt.Errorf("insertJSON requires at least one argument")
	}
	value := args[len(args)-1]
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	args[len(args)-1] = string(raw)
	_, err = tx.Exec(statement, args...)
	return err
}
