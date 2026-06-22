package outreach

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/hanifcarroll/linkedin-network-run/internal/app"
)

type SendMessageOptions struct {
	LeadID     string
	Session    string
	Playwriter string
	Script     string
	OutDir     string
	DryRun     bool
	AllowSend  bool
	TimeoutMS  uint32
}

type MessageCandidate struct {
	ID         string  `json:"id"`
	Name       string  `json:"name"`
	ProfileURL string  `json:"profileUrl"`
	SearchURL  *string `json:"searchUrl,omitempty"`
	Source     string  `json:"source"`
	LeadType   string  `json:"leadType"`
	Title      *string `json:"title,omitempty"`
	Company    *string `json:"company,omitempty"`
}

type MessageSendResult struct {
	Candidate     MessageCandidate `json:"candidate"`
	DryRun        bool             `json:"dryRun"`
	URL           *string          `json:"url"`
	MessageLength int              `json:"messageLength"`
	Status        string           `json:"status"`
	Reason        *string          `json:"reason"`
	Action        json.RawMessage  `json:"action"`
	Send          json.RawMessage  `json:"send"`
	Body          *string          `json:"body"`
}

func SendMessage(store *Store, options SendMessageOptions) error {
	if options.Session == "" {
		return fmt.Errorf("--session is required")
	}
	if options.LeadID == "" {
		return fmt.Errorf("--lead-id is required")
	}
	if options.Script == "" {
		return fmt.Errorf("--script is required")
	}
	if options.OutDir == "" {
		return fmt.Errorf("--out-dir is required")
	}
	dryRun := options.DryRun || !options.AllowSend

	state, err := store.Load()
	if err != nil {
		return err
	}
	index := findLeadByID(state.Leads, options.LeadID)
	if index < 0 {
		return fmt.Errorf("unknown lead id %q", options.LeadID)
	}
	lead := state.Leads[index]
	if lead.Draft == nil || cleanText(lead.Draft.Body) == "" {
		return fmt.Errorf("lead %s has no draft; run draft first", lead.ID)
	}
	if lead.ProfileURL == nil || cleanText(*lead.ProfileURL) == "" {
		return fmt.Errorf("lead %s has no profile URL", lead.ID)
	}
	if !dryRun && lead.MessageStatus != MessageStatusDryRunReady {
		return fmt.Errorf("lead %s is %s; real sends require %s", lead.ID, lead.MessageStatus, MessageStatusDryRunReady)
	}
	if !dryRun && !options.AllowSend {
		return fmt.Errorf("real send requires --allow-send")
	}
	if err := os.MkdirAll(options.OutDir, 0o755); err != nil {
		return fmt.Errorf("creating %s: %w", options.OutDir, err)
	}
	outPath := filepath.Join(options.OutDir, lead.ID+".json")
	candidate := MessageCandidate{
		ID:         lead.ID,
		Name:       lead.Name,
		ProfileURL: *lead.ProfileURL,
		Source:     lead.Source,
		LeadType:   string(lead.LeadType),
		Title:      lead.Title,
		Company:    lead.Company,
	}
	if cursor, ok := state.CaptureCursors[lead.Source]; ok && cursor.ResumeURL != nil && cleanText(*cursor.ResumeURL) != "" {
		candidate.SearchURL = cursor.ResumeURL
	}
	config := map[string]any{
		"candidate": candidate,
		"message":   lead.Draft.Body,
		"subject":   draftSubject(lead),
		"out":       outPath,
		"dryRun":    dryRun,
		"allowSend": options.AllowSend,
	}
	rawConfig, err := json.Marshal(config)
	if err != nil {
		return err
	}
	configJS := fmt.Sprintf("state.recruiterAgencyMessageConfig = %s; console.log(JSON.stringify(state.recruiterAgencyMessageConfig));", string(rawConfig))
	if err := app.RunPlaywriterConfig(options.Playwriter, options.Session, configJS); err != nil {
		return err
	}
	if err := app.RunPlaywriterFileWithTimeout(options.Playwriter, options.Session, options.Script, options.TimeoutMS); err != nil {
		return err
	}
	result, err := LoadMessageSendResult(outPath)
	if err != nil {
		return err
	}
	ApplyMessageSendResult(&state.Leads[index], result, outPath)
	if err := store.Save(state); err != nil {
		return err
	}
	fmt.Printf("lead=%s status=%s dry_run=%t out=%s\n", lead.ID, result.Status, result.DryRun, outPath)
	return nil
}

func messageSubject(lead Lead) string {
	switch lead.LeadType {
	case LeadTypeAgencyResource, LeadTypeAgencyDelivery, LeadTypeAgencyFounder:
		return "Full-Stack Product Engineer Available for Project Work"
	default:
		return "Full-Stack + AI Product Engineer | Open to Contract Work"
	}
}

func draftSubject(lead Lead) string {
	if lead.Draft != nil && cleanText(lead.Draft.Subject) != "" {
		return strings.TrimSpace(lead.Draft.Subject)
	}
	return messageSubject(lead)
}

func LoadMessageSendResult(path string) (MessageSendResult, error) {
	var result MessageSendResult
	raw, err := os.ReadFile(path)
	if err != nil {
		return MessageSendResult{}, fmt.Errorf("reading message result %s: %w", path, err)
	}
	if err := json.Unmarshal(raw, &result); err != nil {
		return MessageSendResult{}, fmt.Errorf("parsing message result %s: %w", path, err)
	}
	return result, nil
}

func ApplyMessageSendResult(lead *Lead, result MessageSendResult, outPath string) {
	now := time.Now()
	note := resultNote(result)
	lead.SendAttempts = append(lead.SendAttempts, SendAttempt{
		At:        now,
		DryRun:    result.DryRun,
		Status:    result.Status,
		ResultURL: result.URL,
		Note:      note,
		OutPath:   outPath,
	})
	lead.MessageStatus = messageStatusForResult(result)
	lead.UpdatedAt = now
}

func messageStatusForResult(result MessageSendResult) MessageStatus {
	switch result.Status {
	case "dry-run-messageable":
		return MessageStatusDryRunReady
	case "sent-clicked":
		return MessageStatusSent
	case "not-messageable":
		return MessageStatusNotMessageable
	case "conversation-exists":
		return MessageStatusConversationExists
	case "blocked":
		return MessageStatusBlocked
	default:
		return MessageStatusSendFailed
	}
}

func resultNote(result MessageSendResult) *string {
	if result.Reason != nil && cleanText(*result.Reason) != "" {
		return result.Reason
	}
	if len(result.Action) > 0 && string(result.Action) != "null" {
		value := string(result.Action)
		return &value
	}
	return nil
}
