package app

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

func ApplyPendingAudit(run *PendingCleanupRun, peopleCount uint32, note *string) {
	audit := AuditEvent{At: time.Now(), PeopleCount: peopleCount, Note: note}
	if run.StartAudit == nil {
		run.StartAudit = &peopleCount
		run.State = PendingCleanupStateAudited
	} else if run.State == PendingCleanupStateNeedsReaudit {
		run.State = PendingCleanupStateWithdrawing
	}
	run.LatestAudit = &peopleCount
	run.Audits = append(run.Audits, audit)
	run.MarkUpdated()
}

type PendingCapture struct {
	CapturedAt *string             `json:"capturedAt"`
	Rows       []PendingCaptureRow `json:"rows"`
}

func LoadPendingCapture(path string) (PendingCapture, error) {
	var capture PendingCapture
	if err := readJSONFile(path, &capture, "reading pending capture "+path, "parsing pending capture "+path); err != nil {
		return PendingCapture{}, err
	}
	if capture.Rows == nil {
		capture.Rows = []PendingCaptureRow{}
	}
	return capture, nil
}

type PendingCaptureRow struct {
	Index      uint32  `json:"index"`
	Name       *string `json:"name"`
	ProfileURL *string `json:"profileUrl"`
	AgeText    *string `json:"ageText"`
	AgeMonths  *uint32 `json:"ageMonths"`
	AgeDays    *uint32 `json:"ageDays"`
	Eligible   *bool   `json:"eligible"`
	RowText    *string `json:"rowText"`
}

type PendingWithdrawResult struct {
	Candidate PendingWithdrawCandidate `json:"candidate"`
	Status    string                   `json:"status"`
	Detail    json.RawMessage          `json:"detail"`
}

func LoadPendingWithdrawResult(path string) (PendingWithdrawResult, error) {
	var result PendingWithdrawResult
	if err := readJSONFile(path, &result, "reading withdraw result "+path, "parsing withdraw result "+path); err != nil {
		return PendingWithdrawResult{}, err
	}
	return result, nil
}

func (r PendingWithdrawResult) ToWithdrawStatus() (PendingWithdrawStatus, string) {
	switch r.Status {
	case "withdrawn-verified":
		return PendingWithdrawStatusWithdrawn, "salesnav-pending-withdraw-one verified row removed or count decreased"
	case "dry-run-withdrawable":
		return PendingWithdrawStatusSkipped, "dry run found eligible stale invitation"
	case "not-eligible", "row-not-found":
		return PendingWithdrawStatusSkipped, "salesnav-pending-withdraw-one status " + r.Status
	default:
		detail := "no detail"
		if len(r.Detail) > 0 && string(r.Detail) != "null" {
			detail = string(r.Detail)
		}
		return PendingWithdrawStatusFailed, fmt.Sprintf("salesnav-pending-withdraw-one status %s; %s", r.Status, detail)
	}
}

type PendingWithdrawCandidate struct {
	Name       string  `json:"name"`
	ProfileURL *string `json:"profileUrl"`
	AgeText    string  `json:"age_text"`
}

func (c *PendingWithdrawCandidate) UnmarshalJSON(data []byte) error {
	type candidate struct {
		Name            string  `json:"name"`
		ProfileURL      *string `json:"profileUrl"`
		ProfileURLSnake *string `json:"profile_url"`
		AgeText         string  `json:"age_text"`
		AgeTextCamel    string  `json:"ageText"`
	}
	var value candidate
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	c.Name = value.Name
	c.ProfileURL = value.ProfileURL
	if c.ProfileURL == nil {
		c.ProfileURL = value.ProfileURLSnake
	}
	c.AgeText = value.AgeText
	if c.AgeText == "" {
		c.AgeText = value.AgeTextCamel
	}
	return nil
}

func ImportPendingCapture(run *PendingCleanupRun, capture PendingCapture) (int, error) {
	imported := 0
	for _, row := range capture.Rows {
		if row.Name == nil || strings.TrimSpace(*row.Name) == "" {
			continue
		}
		ageText := ""
		if row.AgeText != nil {
			ageText = *row.AgeText
		}
		ageMonths := row.AgeMonths
		if ageMonths == nil {
			ageMonths = ParseSentAgeMonths(ageText)
		}
		ageDays := row.AgeDays
		if ageDays == nil {
			ageDays = ParseSentAgeDays(ageText)
		}
		eligible := false
		if ageDays != nil && run.ThresholdDays > 0 {
			eligible = *ageDays >= run.ThresholdDays
		} else if ageMonths != nil {
			eligible = *ageMonths >= run.ThresholdMonths
		} else if row.Eligible != nil {
			eligible = *row.Eligible
		}
		rowText := ""
		if row.RowText != nil {
			rowText = *row.RowText
		}
		observation := PendingCandidateObservation{
			ImportedAt: time.Now(),
			CapturedAt: capture.CapturedAt,
			Index:      row.Index,
			Name:       *row.Name,
			ProfileURL: row.ProfileURL,
			AgeText:    ageText,
			AgeMonths:  ageMonths,
			AgeDays:    ageDays,
			Eligible:   eligible,
			RowText:    rowText,
		}
		existingIndex := -1
		for i, existing := range run.Observations {
			if existing.ProfileURL != nil && observation.ProfileURL != nil {
				if *existing.ProfileURL == *observation.ProfileURL {
					existingIndex = i
					break
				}
			} else if existing.Name == observation.Name && existing.AgeText == observation.AgeText {
				existingIndex = i
				break
			}
		}
		if existingIndex >= 0 {
			run.Observations[existingIndex] = observation
		} else {
			run.Observations = append(run.Observations, observation)
			imported++
		}
	}
	return imported, nil
}

func ParseSentAgeMonths(ageText string) *uint32 {
	lower := strings.ToLower(ageText)
	if strings.Contains(lower, "year") {
		count := FirstNumber(lower)
		if count == nil {
			value := uint32(1)
			count = &value
		}
		value := *count * 12
		return &value
	}
	if strings.Contains(lower, "month") {
		count := FirstNumber(lower)
		if count == nil {
			value := uint32(1)
			count = &value
		}
		return count
	}
	if strings.Contains(lower, "today") ||
		strings.Contains(lower, "minute") ||
		strings.Contains(lower, "hour") ||
		strings.Contains(lower, "day") ||
		strings.Contains(lower, "week") {
		value := uint32(0)
		return &value
	}
	return nil
}

func ParseSentAgeDays(ageText string) *uint32 {
	lower := strings.ToLower(ageText)
	if strings.Contains(lower, "today") ||
		strings.Contains(lower, "minute") ||
		strings.Contains(lower, "hour") {
		value := uint32(0)
		return &value
	}
	count := FirstNumber(lower)
	if count == nil {
		value := uint32(1)
		count = &value
	}
	if strings.Contains(lower, "year") {
		value := *count * 365
		return &value
	}
	if strings.Contains(lower, "month") {
		value := *count * 30
		return &value
	}
	if strings.Contains(lower, "week") {
		value := *count * 7
		return &value
	}
	if strings.Contains(lower, "yesterday") {
		value := uint32(1)
		return &value
	}
	if strings.Contains(lower, "day") {
		return count
	}
	return nil
}

func FirstNumber(value string) *uint32 {
	parts := strings.FieldsFunc(value, func(r rune) bool { return r < '0' || r > '9' })
	for _, part := range parts {
		if part == "" {
			continue
		}
		parsed, err := strconv.ParseUint(part, 10, 32)
		if err == nil {
			value := uint32(parsed)
			return &value
		}
	}
	return nil
}

func RecordPendingWithdrawResult(run *PendingCleanupRun, result PendingWithdrawResult, path string) (PendingWithdrawEvent, error) {
	status, statusNote := result.ToWithdrawStatus()
	note := fmt.Sprintf("%s; result=%s", statusNote, path)
	event := PendingWithdrawEvent{
		At:         time.Now(),
		Name:       result.Candidate.Name,
		ProfileURL: result.Candidate.ProfileURL,
		AgeText:    result.Candidate.AgeText,
		Status:     status,
		Note:       &note,
	}
	if status == PendingWithdrawStatusWithdrawn {
		for _, withdrawal := range run.Withdrawals {
			if withdrawal.Status == PendingWithdrawStatusWithdrawn && withdrawal.Name == event.Name {
				if (withdrawal.ProfileURL == nil && event.ProfileURL == nil) ||
					(withdrawal.ProfileURL != nil && event.ProfileURL != nil && *withdrawal.ProfileURL == *event.ProfileURL) {
					return PendingWithdrawEvent{}, fmt.Errorf("candidate already recorded as withdrawn: %s", event.Name)
				}
			}
		}
	}
	run.Withdrawals = append(run.Withdrawals, event)
	if run.State != PendingCleanupStateDone && run.State != PendingCleanupStateBlocked {
		if run.WithdrawCapacityRemaining() == 0 {
			run.State = PendingCleanupStateFinalReconcile
		} else {
			run.State = PendingCleanupStateWithdrawing
		}
	}
	run.MarkUpdated()
	return event, nil
}

func writePendingCandidate(path string, candidate PendingCandidateObservation) error {
	raw, err := json.MarshalIndent(candidate, "", "  ")
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	return os.WriteFile(path, raw, 0o644)
}
