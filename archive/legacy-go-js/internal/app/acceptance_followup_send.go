package app

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type AcceptanceFollowupSendOptions struct {
	ID          string
	Session     *string
	Playwriter  string
	Script      string
	OutDir      string
	DryRun      bool
	PreviewFill bool
	AllowSend   bool
	TimeoutMS   uint32
}

type AcceptanceFollowupSendReadyOptions struct {
	Session    *string
	Playwriter string
	Script     string
	OutDir     string
	Limit      int
	AllowSend  bool
	TimeoutMS  uint32
}

type AcceptanceFollowupDryRunOptions struct {
	Session    *string
	Playwriter string
	Script     string
	OutDir     string
	Limit      int
	TimeoutMS  uint32
}

type AcceptanceFollowupMessageCandidate struct {
	ID         string `json:"id"`
	Key        string `json:"key"`
	Name       string `json:"name"`
	ProfileURL string `json:"profileUrl"`
	Source     string `json:"source"`
}

type AcceptanceFollowupSendResult struct {
	Candidate           AcceptanceFollowupMessageCandidate `json:"candidate"`
	DryRun              bool                               `json:"dryRun"`
	URL                 *string                            `json:"url"`
	MessageLength       int                                `json:"messageLength"`
	Status              string                             `json:"status"`
	Reason              *string                            `json:"reason"`
	Action              json.RawMessage                    `json:"action"`
	SearchRowAction     json.RawMessage                    `json:"searchRowAction"`
	ConversationCheck   json.RawMessage                    `json:"conversationCheck"`
	SubjectFill         json.RawMessage                    `json:"subjectFill"`
	BodyFill            json.RawMessage                    `json:"bodyFill"`
	Send                json.RawMessage                    `json:"send"`
	SendButtons         json.RawMessage                    `json:"sendButtons"`
	ProfileAPIResponses json.RawMessage                    `json:"profileApiResponses"`
	ComposerSelector    *string                            `json:"composerSelector"`
	Body                *string                            `json:"body"`
}

func HandleAcceptanceSendFollowup(store *Store, options AcceptanceFollowupSendOptions) error {
	if options.ID == "" {
		return fmt.Errorf("--id is required")
	}
	if options.Session == nil {
		return fmt.Errorf("--session is required")
	}
	if options.Script == "" {
		return fmt.Errorf("--script is required")
	}
	if options.OutDir == "" {
		return fmt.Errorf("--out-dir is required")
	}
	dryRun := options.DryRun || options.PreviewFill || !options.AllowSend

	ledger, err := store.LoadAcceptanceFollowupLedger()
	if err != nil {
		return err
	}
	index, ok := ledger.FindByID(options.ID)
	if !ok {
		return fmt.Errorf("unknown acceptance follow-up id %q", options.ID)
	}
	if err := validateAcceptanceFollowupCanSend(ledger.Drafts[index], dryRun, options.AllowSend); err != nil {
		return err
	}
	result, outPath, err := runAcceptanceFollowupSend(options, ledger.Drafts[index], dryRun)
	if err != nil {
		return err
	}
	ApplyAcceptanceFollowupSendResult(&ledger.Drafts[index], result, outPath)
	if err := store.SaveAcceptanceFollowupLedger(ledger); err != nil {
		return err
	}
	if err := store.AppendAcceptanceEvent("send-followup", map[string]any{
		"id":           options.ID,
		"name":         ledger.Drafts[index].Name,
		"status":       result.Status,
		"dry_run":      dryRun,
		"preview_fill": options.PreviewFill,
		"out":          outPath,
	}); err != nil {
		return err
	}
	fmt.Printf("accepted_followup=%s status=%s dry_run=%t preview_fill=%t out=%s\n", options.ID, result.Status, result.DryRun, options.PreviewFill, outPath)
	return nil
}

func HandleAcceptanceSendReadyFollowups(store *Store, options AcceptanceFollowupSendReadyOptions) error {
	if !options.AllowSend {
		return fmt.Errorf("send-ready-followups requires --allow-send")
	}
	if options.Session == nil {
		return fmt.Errorf("--session is required")
	}
	ledger, err := store.LoadAcceptanceFollowupLedger()
	if err != nil {
		return err
	}
	ready := ledger.Ready(options.Limit)
	if len(ready) == 0 {
		fmt.Println("no accepted follow-ups are ready to send")
		return nil
	}
	for _, record := range ready {
		if err := HandleAcceptanceSendFollowup(store, AcceptanceFollowupSendOptions{
			ID:         record.ID,
			Session:    options.Session,
			Playwriter: options.Playwriter,
			Script:     options.Script,
			OutDir:     options.OutDir,
			DryRun:     false,
			AllowSend:  true,
			TimeoutMS:  options.TimeoutMS,
		}); err != nil {
			return err
		}
	}
	return nil
}

func HandleAcceptanceDryRunFollowups(store *Store, options AcceptanceFollowupDryRunOptions) error {
	if options.Session == nil {
		return fmt.Errorf("--session is required")
	}
	ledger, err := store.LoadAcceptanceFollowupLedger()
	if err != nil {
		return err
	}
	pending := ledger.NeedsDryRun(options.Limit)
	if len(pending) == 0 {
		fmt.Println("no accepted follow-ups need a dry-run check")
		return nil
	}
	for _, record := range pending {
		if err := HandleAcceptanceSendFollowup(store, AcceptanceFollowupSendOptions{
			ID:         record.ID,
			Session:    options.Session,
			Playwriter: options.Playwriter,
			Script:     options.Script,
			OutDir:     options.OutDir,
			DryRun:     true,
			AllowSend:  false,
			TimeoutMS:  options.TimeoutMS,
		}); err != nil {
			return err
		}
	}
	return nil
}

func validateAcceptanceFollowupCanSend(record AcceptanceFollowupRecord, dryRun bool, allowSend bool) error {
	if record.Terminal() {
		return fmt.Errorf("accepted follow-up %s is already %s", record.ID, record.Status)
	}
	if strings.TrimSpace(record.Draft) == "" {
		return fmt.Errorf("accepted follow-up %s has no stored draft; rerun `acceptance draft-followups --include-drafted` first", record.ID)
	}
	if record.ProfileURL == nil || strings.TrimSpace(*record.ProfileURL) == "" {
		return fmt.Errorf("accepted follow-up %s has no profile URL", record.ID)
	}
	if !dryRun && !allowSend {
		return fmt.Errorf("real send requires --allow-send")
	}
	if !dryRun && record.Status != AcceptanceFollowupStatusDryRunReady {
		return fmt.Errorf("accepted follow-up %s is %s; real sends require %s", record.ID, record.Status, AcceptanceFollowupStatusDryRunReady)
	}
	return nil
}

func runAcceptanceFollowupSend(options AcceptanceFollowupSendOptions, record AcceptanceFollowupRecord, dryRun bool) (AcceptanceFollowupSendResult, string, error) {
	if err := os.MkdirAll(options.OutDir, 0o755); err != nil {
		return AcceptanceFollowupSendResult{}, "", fmt.Errorf("creating %s: %w", options.OutDir, err)
	}
	outPath := filepath.Join(options.OutDir, record.ID+".json")
	candidate := AcceptanceFollowupMessageCandidate{
		ID:         record.ID,
		Key:        record.Key,
		Name:       record.Name,
		ProfileURL: *record.ProfileURL,
		Source:     record.Source,
	}
	config := map[string]any{
		"candidate":   candidate,
		"message":     record.Draft,
		"subject":     "",
		"out":         outPath,
		"dryRun":      dryRun,
		"previewFill": options.PreviewFill,
		"allowSend":   options.AllowSend,
	}
	rawConfig, err := json.Marshal(config)
	if err != nil {
		return AcceptanceFollowupSendResult{}, "", err
	}
	configJS := fmt.Sprintf("state.acceptanceFollowupMessageConfig = %s; console.log(JSON.stringify(state.acceptanceFollowupMessageConfig));", string(rawConfig))
	if err := RunPlaywriterConfig(options.Playwriter, *options.Session, configJS); err != nil {
		return AcceptanceFollowupSendResult{}, "", err
	}
	if err := RunPlaywriterFileWithTimeout(options.Playwriter, *options.Session, options.Script, options.TimeoutMS); err != nil {
		return AcceptanceFollowupSendResult{}, "", err
	}
	result, err := LoadAcceptanceFollowupSendResult(outPath)
	if err != nil {
		return AcceptanceFollowupSendResult{}, "", err
	}
	return result, outPath, nil
}

func LoadAcceptanceFollowupSendResult(path string) (AcceptanceFollowupSendResult, error) {
	var result AcceptanceFollowupSendResult
	raw, err := os.ReadFile(path)
	if err != nil {
		return AcceptanceFollowupSendResult{}, fmt.Errorf("reading acceptance follow-up result %s: %w", path, err)
	}
	if err := json.Unmarshal(raw, &result); err != nil {
		return AcceptanceFollowupSendResult{}, fmt.Errorf("parsing acceptance follow-up result %s: %w", path, err)
	}
	return result, nil
}

func ApplyAcceptanceFollowupSendResult(record *AcceptanceFollowupRecord, result AcceptanceFollowupSendResult, outPath string) {
	now := time.Now()
	record.Attempts = append(record.Attempts, AcceptanceFollowupAttempt{
		At:          now,
		DryRun:      result.DryRun,
		Status:      result.Status,
		ResultURL:   result.URL,
		Note:        acceptanceFollowupResultNote(result),
		OutPath:     outPath,
		Diagnostics: acceptanceFollowupDiagnostics(result),
	})
	record.Status = acceptanceFollowupStatusForResult(result)
	record.UpdatedAt = now
	if record.Status == AcceptanceFollowupStatusSent {
		record.SentAt = &now
	}
}

func acceptanceFollowupStatusForResult(result AcceptanceFollowupSendResult) AcceptanceFollowupStatus {
	switch result.Status {
	case "dry-run-messageable", "preview-filled":
		return AcceptanceFollowupStatusDryRunReady
	case "sent-clicked":
		return AcceptanceFollowupStatusSent
	case "not-messageable":
		return AcceptanceFollowupStatusNotMessageable
	case "conversation-exists":
		return AcceptanceFollowupStatusConversationExists
	case "blocked":
		return AcceptanceFollowupStatusBlocked
	default:
		return AcceptanceFollowupStatusSendFailed
	}
}

func acceptanceFollowupDiagnostics(result AcceptanceFollowupSendResult) map[string]string {
	diagnostics := map[string]string{}
	if result.ComposerSelector != nil && strings.TrimSpace(*result.ComposerSelector) != "" {
		diagnostics["composer"] = strings.TrimSpace(*result.ComposerSelector)
	}
	addCompactJSONDiagnostic(diagnostics, "subject", result.SubjectFill)
	addCompactJSONDiagnostic(diagnostics, "body", result.BodyFill)
	addCompactJSONDiagnostic(diagnostics, "send", result.Send)
	addCompactJSONDiagnostic(diagnostics, "send_buttons", result.SendButtons)
	addCompactJSONDiagnostic(diagnostics, "conversation", result.ConversationCheck)
	addCompactJSONDiagnostic(diagnostics, "action", result.Action)
	return diagnostics
}

func acceptanceFollowupResultNote(result AcceptanceFollowupSendResult) *string {
	parts := []string{}
	if result.Reason != nil && strings.TrimSpace(*result.Reason) != "" {
		parts = append(parts, strings.TrimSpace(*result.Reason))
	}
	if result.ComposerSelector != nil && strings.TrimSpace(*result.ComposerSelector) != "" {
		parts = append(parts, "composer "+strings.TrimSpace(*result.ComposerSelector))
	}
	if len(result.BodyFill) > 0 && string(result.BodyFill) != "null" {
		parts = append(parts, "body "+compactRawJSON(result.BodyFill))
	}
	if len(result.Send) > 0 && string(result.Send) != "null" {
		parts = append(parts, "send "+compactRawJSON(result.Send))
	}
	if len(parts) == 0 {
		return nil
	}
	note := truncateString(strings.Join(parts, "; "), 1000)
	return &note
}

func addCompactJSONDiagnostic(target map[string]string, key string, raw json.RawMessage) {
	if len(raw) == 0 || string(raw) == "null" {
		return
	}
	target[key] = compactRawJSON(raw)
}

func compactRawJSON(raw json.RawMessage) string {
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return truncateString(string(raw), 1000)
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return truncateString(string(raw), 1000)
	}
	return truncateString(string(encoded), 1000)
}

func truncateString(value string, limit int) string {
	if limit <= 0 || len(value) <= limit {
		return value
	}
	return value[:limit]
}
