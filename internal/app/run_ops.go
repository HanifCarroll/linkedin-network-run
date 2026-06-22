package app

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

func EnsureKnownSource(run Run, source string) error {
	for _, plan := range run.Sources {
		if plan.Name == source {
			return nil
		}
	}
	return fmt.Errorf("unknown source: %s", source)
}

func ApplyAudit(run *Run, peopleCount uint32, note *string) {
	audit := AuditEvent{
		At:          time.Now(),
		PeopleCount: peopleCount,
		Note:        note,
	}
	if run.StartAudit == nil {
		run.StartAudit = &peopleCount
		run.State = RunStateStartAudited
	} else if HasBlockingSendResult(*run) {
		run.State = RunStateBlocked
	} else if run.State == RunStateNeedsReaudit {
		run.State = RunStateSending
	}
	run.LatestAudit = &peopleCount
	run.Audits = append(run.Audits, audit)
	run.MarkUpdated()
}

func RecordSendResult(run *Run, result SalesNavSendResult, path string) (CandidateEvent, error) {
	status, statusNote := result.ToCandidateStatus()
	note := fmt.Sprintf("%s; result=%s", statusNote, path)
	event := CandidateEvent{
		At:         time.Now(),
		Source:     result.Candidate.Source,
		Name:       result.Candidate.Name,
		ProfileURL: result.Candidate.ProfileURL,
		Status:     status,
		Note:       &note,
	}
	if err := EnsureKnownSource(*run, event.Source); err != nil {
		return CandidateEvent{}, err
	}
	if status == CandidateStatusPending {
		for _, candidate := range run.Candidates {
			if candidate.Status == CandidateStatusPending && candidate.Name == event.Name {
				if (candidate.ProfileURL == nil && event.ProfileURL == nil) ||
					(candidate.ProfileURL != nil && event.ProfileURL != nil && *candidate.ProfileURL == *event.ProfileURL) {
					return CandidateEvent{}, fmt.Errorf("candidate already recorded as pending: %s", event.Name)
				}
			}
		}
	}
	run.Candidates = append(run.Candidates, event)
	if run.State != RunStateDone && run.State != RunStateBlocked {
		if run.VerifiedCount() >= run.Target {
			run.State = RunStateFinalReconcile
		} else {
			run.State = RunStateSending
		}
	}
	run.MarkUpdated()
	return event, nil
}

func RecordTopUpSendResult(run *Run, result SalesNavSendResult, path string, note *string) (CandidateEvent, error) {
	status, statusNote := result.ToCandidateStatus()
	if status == CandidateStatusPending {
		status = CandidateStatusAuditTopUp
	}
	parts := []string{statusNote}
	if note != nil {
		parts = append(parts, *note)
	}
	parts = append(parts, "result="+path)
	joined := strings.Join(parts, "; ")
	event := CandidateEvent{
		At:         time.Now(),
		Source:     result.Candidate.Source,
		Name:       result.Candidate.Name,
		ProfileURL: result.Candidate.ProfileURL,
		Status:     status,
		Note:       &joined,
	}
	if err := EnsureKnownSource(*run, event.Source); err != nil {
		return CandidateEvent{}, err
	}
	run.Candidates = append(run.Candidates, event)
	run.MarkUpdated()
	return event, nil
}

func DrainStaleConnectableCandidates(run *Run, sourceFilter *string) ([]CandidateEvent, error) {
	stale := []CandidateObservation{}
	for _, observation := range run.Observations {
		if observation.MenuState != "connectable" {
			continue
		}
		if sourceFilter != nil && observation.Source != *sourceFilter {
			continue
		}
		if run.PreserveForAuditTopUp(observation) {
			continue
		}
		if !run.SourceIsFilledOrClosed(observation.Source) || run.HasCandidateEventForObservation(observation) {
			continue
		}
		stale = append(stale, observation)
	}

	events := []CandidateEvent{}
	for _, observation := range stale {
		if err := EnsureKnownSource(*run, observation.Source); err != nil {
			return nil, err
		}
		quota, _ := run.SourceQuota(observation.Source)
		verified := run.SourceVerifiedCount(observation.Source)
		note := fmt.Sprintf(
			"auto-skipped stale imported candidate after source closed or filled; source %d/%d, run %d/%d",
			verified,
			quota,
			run.VerifiedCount(),
			run.Target,
		)
		event := CandidateEvent{
			At:         time.Now(),
			Source:     observation.Source,
			Name:       observation.Name,
			ProfileURL: observation.ProfileURL,
			Status:     CandidateStatusSkipped,
			Note:       &note,
		}
		run.Candidates = append(run.Candidates, event)
		events = append(events, event)
	}
	if len(events) > 0 {
		run.MarkUpdated()
	}
	return events, nil
}

func IsUncertainSendStatus(status string) bool {
	return strings.HasPrefix(status, "unverified:") || status == "blocked"
}

func HasBlockingSendResult(run Run) bool {
	for _, event := range run.Candidates {
		if run.BlockedResumeAt != nil && !event.At.After(*run.BlockedResumeAt) {
			continue
		}
		if event.Status == CandidateStatusFailed && event.Note != nil && strings.Contains(*event.Note, "salesnav-send-one status blocked") {
			return true
		}
	}
	return false
}

func isSendNoopStatus(status string) bool {
	return status == "unverified:clicked-send" ||
		status == "unverified:send-not-accepted" ||
		status == "unverified:send-button-disabled"
}

func SourceRepeatedSendNoop(run Run, source string, threshold uint32) bool {
	if threshold == 0 {
		return false
	}
	var consecutive uint32
	for i := len(run.Candidates) - 1; i >= 0; i-- {
		event := run.Candidates[i]
		if event.Source != source {
			continue
		}
		if event.Status == CandidateStatusPending || event.Status == CandidateStatusAuditTopUp {
			return false
		}
		if event.Status == CandidateStatusFailed && event.Note != nil && isSendNoopNote(*event.Note) {
			consecutive++
			if consecutive >= threshold {
				return true
			}
			continue
		}
		return false
	}
	return false
}

func isSendNoopNote(note string) bool {
	return strings.Contains(note, "unverified:clicked-send") ||
		strings.Contains(note, "unverified:send-not-accepted") ||
		strings.Contains(note, "unverified:send-button-disabled")
}

type AcceptanceCheckCandidate struct {
	RunID           string           `json:"run_id"`
	RunDate         Date             `json:"run_date"`
	Source          string           `json:"source"`
	Name            string           `json:"name"`
	ProfileURL      *string          `json:"profile_url"`
	SentAt          time.Time        `json:"sent_at"`
	LatestStatus    AcceptanceStatus `json:"latest_status"`
	LatestCheckedAt *time.Time       `json:"latest_checked_at"`
}

func NewAcceptanceCheckCandidate(invitation AcceptanceInvitation) AcceptanceCheckCandidate {
	return AcceptanceCheckCandidate{
		RunID:           invitation.RunID.String(),
		RunDate:         invitation.RunDate,
		Source:          invitation.Source,
		Name:            invitation.Name,
		ProfileURL:      invitation.ProfileURL,
		SentAt:          invitation.SentAt,
		LatestStatus:    invitation.LatestStatus,
		LatestCheckedAt: invitation.LatestCheckedAt,
	}
}

type AcceptanceOutcomeArtifact struct {
	Rows []AcceptanceOutcomeRow `json:"rows"`
}

func LoadAcceptanceOutcomeArtifact(path string) (AcceptanceOutcomeArtifact, error) {
	var artifact AcceptanceOutcomeArtifact
	if err := readJSONFile(path, &artifact, "reading acceptance outcome "+path, "parsing acceptance outcome "+path); err != nil {
		return AcceptanceOutcomeArtifact{}, err
	}
	if artifact.Rows == nil {
		artifact.Rows = []AcceptanceOutcomeRow{}
	}
	return artifact, nil
}

type AcceptanceOutcomeRow struct {
	Source       string           `json:"source"`
	Name         string           `json:"name"`
	ProfileURL   *string          `json:"profileUrl"`
	Status       AcceptanceStatus `json:"status"`
	CheckedAt    *time.Time       `json:"checkedAt"`
	Relationship *string          `json:"relationship"`
	Evidence     *string          `json:"evidence"`
	Note         *string          `json:"note"`
}

func (r *AcceptanceOutcomeRow) UnmarshalJSON(data []byte) error {
	type row struct {
		Source          string           `json:"source"`
		Name            string           `json:"name"`
		ProfileURL      *string          `json:"profileUrl"`
		ProfileURLSnake *string          `json:"profile_url"`
		Status          AcceptanceStatus `json:"status"`
		CheckedAt       *time.Time       `json:"checkedAt"`
		CheckedAtSnake  *time.Time       `json:"checked_at"`
		Relationship    *string          `json:"relationship"`
		Evidence        *string          `json:"evidence"`
		Note            *string          `json:"note"`
	}
	var value row
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	r.Source = value.Source
	r.Name = value.Name
	r.ProfileURL = value.ProfileURL
	if r.ProfileURL == nil {
		r.ProfileURL = value.ProfileURLSnake
	}
	r.Status = value.Status
	r.CheckedAt = value.CheckedAt
	if r.CheckedAt == nil {
		r.CheckedAt = value.CheckedAtSnake
	}
	r.Relationship = value.Relationship
	r.Evidence = value.Evidence
	r.Note = value.Note
	return nil
}

func SentEventsFromControllerLog(path string, runID uuidLike) (Date, []CandidateEvent, bool, error) {
	var runDate *Date
	events := []CandidateEvent{}
	err := readJSONLines(path, func(lineNumber int, raw []byte) error {
		var entry ControllerEventLogEntry
		if err := json.Unmarshal(raw, &entry); err != nil {
			return fmt.Errorf("parsing %s line %d: %w", path, lineNumber, err)
		}
		if entry.RunID.String() != runID.String() {
			return nil
		}
		if runDate == nil {
			date := Date{Time: time.Date(entry.At.Year(), entry.At.Month(), entry.At.Day(), 0, 0, 0, 0, entry.At.Location())}
			runDate = &date
		}
		if entry.Kind != "record-send-result" && entry.Kind != "record-top-up-result" {
			return nil
		}
		var payload struct {
			Event *CandidateEvent `json:"event"`
		}
		if err := json.Unmarshal(entry.Payload, &payload); err != nil {
			return fmt.Errorf("parsing payload from %s line %d: %w", path, lineNumber, err)
		}
		if payload.Event == nil {
			return nil
		}
		if payload.Event.Status == CandidateStatusPending || payload.Event.Status == CandidateStatusAuditTopUp {
			if runDate == nil {
				date := Date{Time: time.Date(payload.Event.At.Year(), payload.Event.At.Month(), payload.Event.At.Day(), 0, 0, 0, 0, payload.Event.At.Location())}
				runDate = &date
			}
			events = append(events, *payload.Event)
		}
		return nil
	})
	if err != nil {
		return Date{}, nil, false, err
	}
	if runDate == nil || len(events) == 0 {
		return Date{}, nil, false, nil
	}
	return *runDate, events, true, nil
}

type uuidLike interface {
	String() string
}
