package app

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
)

const appDir = "linkedin-network-run"

type Date struct {
	time.Time
}

func Today() Date {
	now := time.Now()
	return Date{Time: time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, now.Location())}
}

func ParseDate(value string) (Date, error) {
	parsed, err := time.ParseInLocation("2006-01-02", value, time.Local)
	if err != nil {
		return Date{}, err
	}
	return Date{Time: parsed}, nil
}

func (d Date) MarshalJSON() ([]byte, error) {
	return json.Marshal(d.String())
}

func (d *Date) UnmarshalJSON(data []byte) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	parsed, err := ParseDate(value)
	if err != nil {
		return err
	}
	*d = parsed
	return nil
}

func (d Date) String() string {
	return d.Format("2006-01-02")
}

type RunState string

const (
	RunStateStarted        RunState = "Started"
	RunStateStartAudited   RunState = "StartAudited"
	RunStateSending        RunState = "Sending"
	RunStateNeedsReaudit   RunState = "NeedsReaudit"
	RunStateFinalReconcile RunState = "FinalReconcile"
	RunStateDone           RunState = "Done"
	RunStateBlocked        RunState = "Blocked"
)

type CandidateStatus string

const (
	CandidateStatusPending        CandidateStatus = "pending"
	CandidateStatusAlreadyPending CandidateStatus = "already-pending"
	CandidateStatusAuditTopUp     CandidateStatus = "audit-top-up"
	CandidateStatusSkipped        CandidateStatus = "skipped"
	CandidateStatusFailed         CandidateStatus = "failed"
)

func ParseCandidateStatus(value string) (CandidateStatus, error) {
	switch CandidateStatus(value) {
	case CandidateStatusPending, CandidateStatusAlreadyPending, CandidateStatusAuditTopUp, CandidateStatusSkipped, CandidateStatusFailed:
		return CandidateStatus(value), nil
	default:
		return "", fmt.Errorf("invalid candidate status %q", value)
	}
}

type AcceptanceStatus string

const (
	AcceptanceStatusSent        AcceptanceStatus = "sent"
	AcceptanceStatusPending     AcceptanceStatus = "pending"
	AcceptanceStatusAccepted    AcceptanceStatus = "accepted"
	AcceptanceStatusConnectable AcceptanceStatus = "connectable"
	AcceptanceStatusWithdrawn   AcceptanceStatus = "withdrawn"
	AcceptanceStatusUnknown     AcceptanceStatus = "unknown"
	AcceptanceStatusBlocked     AcceptanceStatus = "blocked"
	AcceptanceStatusFailed      AcceptanceStatus = "failed"
)

type PendingCleanupState string

const (
	PendingCleanupStateStarted        PendingCleanupState = "Started"
	PendingCleanupStateAudited        PendingCleanupState = "Audited"
	PendingCleanupStateCapturing      PendingCleanupState = "Capturing"
	PendingCleanupStateWithdrawing    PendingCleanupState = "Withdrawing"
	PendingCleanupStateNeedsReaudit   PendingCleanupState = "NeedsReaudit"
	PendingCleanupStateFinalReconcile PendingCleanupState = "FinalReconcile"
	PendingCleanupStateDone           PendingCleanupState = "Done"
	PendingCleanupStateBlocked        PendingCleanupState = "Blocked"
)

type PendingWithdrawStatus string

const (
	PendingWithdrawStatusWithdrawn PendingWithdrawStatus = "Withdrawn"
	PendingWithdrawStatusSkipped   PendingWithdrawStatus = "Skipped"
	PendingWithdrawStatusFailed    PendingWithdrawStatus = "Failed"
)

type DraftStrategy string

const DraftStrategyAsapContractV1 DraftStrategy = "asap-contract-v1"

func ParseDraftStrategy(value string) (DraftStrategy, error) {
	if value == "" || value == string(DraftStrategyAsapContractV1) {
		return DraftStrategyAsapContractV1, nil
	}
	return "", fmt.Errorf("invalid draft strategy %q", value)
}

func (s DraftStrategy) DebugString() string {
	switch s {
	case DraftStrategyAsapContractV1:
		return "AsapContractV1"
	default:
		return string(s)
	}
}

type SourcePlan struct {
	Name      string `json:"name"`
	Target    uint32 `json:"target"`
	Fallback  bool   `json:"fallback"`
	Exhausted bool   `json:"exhausted"`
}

type CandidateEvent struct {
	At         time.Time       `json:"at"`
	Source     string          `json:"source"`
	Name       string          `json:"name"`
	ProfileURL *string         `json:"profile_url"`
	Status     CandidateStatus `json:"status"`
	Note       *string         `json:"note"`
}

type CandidateObservation struct {
	ImportedAt      time.Time       `json:"imported_at"`
	CapturedAt      *string         `json:"captured_at"`
	Source          string          `json:"source"`
	Index           uint32          `json:"index"`
	Name            string          `json:"name"`
	ProfileURL      *string         `json:"profile_url"`
	SalesProfileURN *string         `json:"sales_profile_urn"`
	VisibleState    json.RawMessage `json:"visible_state"`
	MenuState       string          `json:"menu_state"`
	MenuLabels      []string        `json:"menu_labels"`
	RowHTMLPath     *string         `json:"row_html_path"`
}

type SourceCaptureCursor struct {
	Source              string            `json:"source"`
	UpdatedAt           time.Time         `json:"updated_at"`
	CapturedAt          *string           `json:"captured_at"`
	ResumeURL           *string           `json:"resume_url"`
	PageLabel           *string           `json:"page_label"`
	CapturedPages       uint32            `json:"captured_pages"`
	RawRowCount         uint32            `json:"raw_row_count"`
	OutputRowCount      uint32            `json:"output_row_count"`
	ConnectableCount    uint32            `json:"connectable_count"`
	AlreadyPendingCount uint32            `json:"already_pending_count"`
	MissingTriggerCount uint32            `json:"missing_trigger_count"`
	StateCounts         map[string]uint32 `json:"state_counts"`
}

type RunTimingEvent struct {
	At         time.Time `json:"at"`
	Phase      string    `json:"phase"`
	Source     *string   `json:"source"`
	DurationMS uint64    `json:"duration_ms"`
	Detail     *string   `json:"detail"`
}

type CandidateReservoir struct {
	Observations []CandidateObservation `json:"observations"`
	UpdatedAt    *time.Time             `json:"updated_at"`
}

type SourceYieldStats struct {
	Source              string   `json:"source"`
	RawRowCount         uint32   `json:"raw_row_count"`
	ConnectableCount    uint32   `json:"connectable_count"`
	AlreadyPendingCount uint32   `json:"already_pending_count"`
	EmailRequiredSkips  uint32   `json:"email_required_skips"`
	PendingSends        uint32   `json:"pending_sends"`
	ConnectableYield    *float64 `json:"connectable_yield"`
	Recommendation      string   `json:"recommendation"`
}

type CaptureRecommendation struct {
	Pages                uint32 `json:"pages"`
	StopAfterConnectable uint32 `json:"stop_after_connectable"`
	Buffer               uint32 `json:"buffer"`
	Reason               string `json:"reason"`
	PlaywriterTimeoutMS  uint32 `json:"playwriter_timeout_ms"`
}

type CaptureRunOptions struct {
	Pages                uint32
	StopAfterConnectable uint32
	Limit                uint32
	RowScrollDelayMS     uint32
	OnlyConnectable      bool
}

type TopUpFallbackOptions struct {
	CaptureScript        string
	SavedSearches        string
	Source               string
	URL                  *string
	Pages                uint32
	StopAfterConnectable uint32
	Limit                uint32
	RowScrollDelayMS     uint32
	CaptureEnabled       bool
}

type AuditEvent struct {
	At          time.Time `json:"at"`
	PeopleCount uint32    `json:"people_count"`
	Note        *string   `json:"note"`
}

type Run struct {
	ID              uuid.UUID                      `json:"id"`
	Date            Date                           `json:"date"`
	Target          uint32                         `json:"target"`
	MaxRealSends    uint32                         `json:"max_real_sends"`
	State           RunState                       `json:"state"`
	Sources         []SourcePlan                   `json:"sources"`
	StartAudit      *uint32                        `json:"start_audit"`
	LatestAudit     *uint32                        `json:"latest_audit"`
	Audits          []AuditEvent                   `json:"audits"`
	Candidates      []CandidateEvent               `json:"candidates"`
	Observations    []CandidateObservation         `json:"observations"`
	CaptureCursors  map[string]SourceCaptureCursor `json:"capture_cursors"`
	Timings         []RunTimingEvent               `json:"timings"`
	Notes           []string                       `json:"notes"`
	BlockedResumeAt *time.Time                     `json:"blocked_resume_at,omitempty"`
	CreatedAt       time.Time                      `json:"created_at"`
	UpdatedAt       time.Time                      `json:"updated_at"`
}

type PendingCandidateObservation struct {
	ImportedAt time.Time `json:"imported_at"`
	CapturedAt *string   `json:"captured_at"`
	Index      uint32    `json:"index"`
	Name       string    `json:"name"`
	ProfileURL *string   `json:"profile_url"`
	AgeText    string    `json:"age_text"`
	AgeMonths  *uint32   `json:"age_months"`
	AgeDays    *uint32   `json:"age_days"`
	Eligible   bool      `json:"eligible"`
	RowText    string    `json:"row_text"`
}

type PendingWithdrawEvent struct {
	At         time.Time             `json:"at"`
	Name       string                `json:"name"`
	ProfileURL *string               `json:"profile_url"`
	AgeText    string                `json:"age_text"`
	Status     PendingWithdrawStatus `json:"status"`
	Note       *string               `json:"note"`
}

type PendingCleanupRun struct {
	ID              uuid.UUID                     `json:"id"`
	Date            Date                          `json:"date"`
	MaxWithdrawals  uint32                        `json:"max_withdrawals"`
	ThresholdMonths uint32                        `json:"threshold_months"`
	ThresholdDays   uint32                        `json:"threshold_days"`
	State           PendingCleanupState           `json:"state"`
	StartAudit      *uint32                       `json:"start_audit"`
	LatestAudit     *uint32                       `json:"latest_audit"`
	Audits          []AuditEvent                  `json:"audits"`
	Observations    []PendingCandidateObservation `json:"observations"`
	Withdrawals     []PendingWithdrawEvent        `json:"withdrawals"`
	Notes           []string                      `json:"notes"`
	CreatedAt       time.Time                     `json:"created_at"`
	UpdatedAt       time.Time                     `json:"updated_at"`
}

type AcceptanceLedger struct {
	Invitations []AcceptanceInvitation `json:"invitations"`
}

type AcceptanceInvitation struct {
	RunID           uuid.UUID                `json:"run_id"`
	RunDate         Date                     `json:"run_date"`
	Source          string                   `json:"source"`
	Name            string                   `json:"name"`
	ProfileURL      *string                  `json:"profile_url"`
	SentAt          time.Time                `json:"sent_at"`
	LatestStatus    AcceptanceStatus         `json:"latest_status"`
	LatestCheckedAt *time.Time               `json:"latest_checked_at"`
	History         []AcceptanceOutcomeEvent `json:"history"`
}

type AcceptanceOutcomeEvent struct {
	At           time.Time        `json:"at"`
	Status       AcceptanceStatus `json:"status"`
	Note         *string          `json:"note"`
	Relationship *string          `json:"relationship"`
	Evidence     *string          `json:"evidence"`
}

type AcceptanceKey struct {
	Source     string
	Name       string
	ProfileURL *string
}

type AcceptanceImportSummary struct {
	Rows      uint32 `json:"rows"`
	Matched   uint32 `json:"matched"`
	Unmatched uint32 `json:"unmatched"`
}

type AcceptanceHistorySeedSummary struct {
	RunLogs    uint32 `json:"run_logs"`
	SentEvents uint32 `json:"sent_events"`
	Seeded     int    `json:"seeded"`
}

type ControllerEventLogEntry struct {
	At      time.Time       `json:"at"`
	RunID   uuid.UUID       `json:"run_id"`
	Kind    string          `json:"kind"`
	Payload json.RawMessage `json:"payload"`
}

type AcceptanceReport struct {
	MinAgeDays  int64                             `json:"min_age_days"`
	MaxAgeDays  *int64                            `json:"max_age_days"`
	TotalSent   uint32                            `json:"total_sent"`
	Checked     uint32                            `json:"checked"`
	Accepted    uint32                            `json:"accepted"`
	Pending     uint32                            `json:"pending"`
	Connectable uint32                            `json:"connectable"`
	Unknown     uint32                            `json:"unknown"`
	Blocked     uint32                            `json:"blocked"`
	Failed      uint32                            `json:"failed"`
	Withdrawn   uint32                            `json:"withdrawn"`
	Unchecked   uint32                            `json:"unchecked"`
	BySource    map[string]AcceptanceSourceReport `json:"by_source"`
}

type AcceptanceSourceReport struct {
	TotalSent   uint32 `json:"total_sent"`
	Checked     uint32 `json:"checked"`
	Accepted    uint32 `json:"accepted"`
	Pending     uint32 `json:"pending"`
	Connectable uint32 `json:"connectable"`
	Unknown     uint32 `json:"unknown"`
	Blocked     uint32 `json:"blocked"`
	Failed      uint32 `json:"failed"`
	Withdrawn   uint32 `json:"withdrawn"`
	Unchecked   uint32 `json:"unchecked"`
}

type PendingCleanupPlan struct {
	Action                    string  `json:"action"`
	Reason                    *string `json:"reason,omitempty"`
	Name                      *string `json:"name,omitempty"`
	ProfileURL                *string `json:"profile_url,omitempty"`
	AgeText                   *string `json:"age_text,omitempty"`
	WithdrawCapacityRemaining *uint32 `json:"withdraw_capacity_remaining,omitempty"`
}

type OperatorPlan struct {
	Action                    string                 `json:"action"`
	Source                    *string                `json:"source,omitempty"`
	Remaining                 *uint32                `json:"remaining,omitempty"`
	Available                 *int                   `json:"available,omitempty"`
	Capture                   *CaptureRecommendation `json:"capture,omitempty"`
	ResumeURL                 *string                `json:"resume_url,omitempty"`
	Cursor                    *SourceCaptureCursor   `json:"cursor,omitempty"`
	Name                      *string                `json:"name,omitempty"`
	ProfileURL                *string                `json:"profile_url,omitempty"`
	RealSendCapacityRemaining *uint32                `json:"real_send_capacity_remaining,omitempty"`
	Reason                    *string                `json:"reason,omitempty"`
}

type NextSource struct {
	Name               string `json:"name"`
	Quota              uint32 `json:"quota"`
	Verified           uint32 `json:"verified"`
	RemainingForSource uint32 `json:"remaining_for_source"`
	RemainingForRun    uint32 `json:"remaining_for_run"`
	Fallback           bool   `json:"fallback"`
}

var defaultSourceMix = []struct {
	Name   string
	Weight uint32
}{
	{"ASAP - Agency Owners Delivery", 9},
	{"ASAP - Contract Recruiters Staffing", 7},
	{"ASAP - Startup CTO Eng Leaders", 6},
	{"ASAP - High-Intent SaaS AI Founders", 5},
	{"ASAP - Vertical Proof Buyers", 3},
}

func NewRun(target uint32, date Date, maxRealSends uint32) Run {
	now := time.Now()
	return Run{
		ID:             uuid.New(),
		Date:           date,
		Target:         target,
		MaxRealSends:   maxRealSends,
		State:          RunStateStarted,
		Sources:        DefaultSources(target),
		Audits:         []AuditEvent{},
		Candidates:     []CandidateEvent{},
		Observations:   []CandidateObservation{},
		CaptureCursors: map[string]SourceCaptureCursor{},
		Timings:        []RunTimingEvent{},
		Notes:          []string{},
		CreatedAt:      now,
		UpdatedAt:      now,
	}
}

func NewRunDefault(target uint32, date Date) Run {
	return NewRun(target, date, target)
}

func NewPendingCleanupRun(maxWithdrawals, thresholdMonths uint32, date Date) PendingCleanupRun {
	run := NewPendingCleanupRunWithThresholdDays(maxWithdrawals, thresholdMonths*30, date)
	run.ThresholdMonths = thresholdMonths
	return run
}

func NewPendingCleanupRunWithThresholdDays(maxWithdrawals, thresholdDays uint32, date Date) PendingCleanupRun {
	now := time.Now()
	return PendingCleanupRun{
		ID:              uuid.New(),
		Date:            date,
		MaxWithdrawals:  maxWithdrawals,
		ThresholdMonths: thresholdDays / 30,
		ThresholdDays:   thresholdDays,
		State:           PendingCleanupStateStarted,
		Audits:          []AuditEvent{},
		Observations:    []PendingCandidateObservation{},
		Withdrawals:     []PendingWithdrawEvent{},
		Notes:           []string{},
		CreatedAt:       now,
		UpdatedAt:       now,
	}
}

func DefaultSources(target uint32) []SourcePlan {
	var defaultTarget uint32
	for _, item := range defaultSourceMix {
		defaultTarget += item.Weight
	}

	allocated := make([]struct {
		Name   string
		Target uint32
	}, 0, len(defaultSourceMix))
	if target == defaultTarget {
		for _, item := range defaultSourceMix {
			allocated = append(allocated, struct {
				Name   string
				Target uint32
			}{Name: item.Name, Target: item.Weight})
		}
	} else {
		var total uint32
		for _, item := range defaultSourceMix {
			count := uint32(math.Floor(float64(target) * float64(item.Weight) / float64(defaultTarget)))
			allocated = append(allocated, struct {
				Name   string
				Target uint32
			}{Name: item.Name, Target: count})
			total += count
		}
		remaining := target - total
		for i := range allocated {
			if remaining == 0 {
				break
			}
			allocated[i].Target++
			remaining--
		}
	}

	sources := make([]SourcePlan, 0, len(allocated)+1)
	for _, item := range allocated {
		sources = append(sources, SourcePlan{
			Name:      item.Name,
			Target:    item.Target,
			Fallback:  false,
			Exhausted: false,
		})
	}
	sources = append(sources, SourcePlan{
		Name:      "FO - Founders - Urgent",
		Target:    0,
		Fallback:  true,
		Exhausted: false,
	})
	return sources
}

func (r *Run) Normalize() {
	if r.MaxRealSends == 0 {
		r.MaxRealSends = r.Target
	}
	if r.Sources == nil {
		r.Sources = []SourcePlan{}
	}
	if r.Audits == nil {
		r.Audits = []AuditEvent{}
	}
	if r.Candidates == nil {
		r.Candidates = []CandidateEvent{}
	}
	if r.Observations == nil {
		r.Observations = []CandidateObservation{}
	}
	if r.CaptureCursors == nil {
		r.CaptureCursors = map[string]SourceCaptureCursor{}
	}
	if r.Timings == nil {
		r.Timings = []RunTimingEvent{}
	}
	if r.Notes == nil {
		r.Notes = []string{}
	}
	for i := range r.Observations {
		if len(r.Observations[i].VisibleState) == 0 {
			r.Observations[i].VisibleState = json.RawMessage("null")
		}
		if r.Observations[i].MenuLabels == nil {
			r.Observations[i].MenuLabels = []string{}
		}
	}
}

func (r *Run) MarkUpdated() {
	r.UpdatedAt = time.Now()
}

func (r Run) VerifiedCount() uint32 {
	var count uint32
	for _, candidate := range r.Candidates {
		if candidate.Status == CandidateStatusPending {
			count++
		}
	}
	return count
}

func (r Run) AuditedDelta() *int64 {
	if r.StartAudit == nil || r.LatestAudit == nil {
		return nil
	}
	delta := int64(*r.LatestAudit) - int64(*r.StartAudit)
	return &delta
}

func (r Run) SourceVerifiedCount(source string) uint32 {
	var count uint32
	for _, candidate := range r.Candidates {
		if candidate.Source == source && candidate.Status == CandidateStatusPending {
			count++
		}
	}
	return count
}

func (r Run) SourceIndex(source string) (int, bool) {
	for i, plan := range r.Sources {
		if plan.Name == source {
			return i, true
		}
	}
	return 0, false
}

func (r Run) SourceQuota(source string) (uint32, bool) {
	index, ok := r.SourceIndex(source)
	if !ok {
		return 0, false
	}
	return r.SourceQuotaWithCarryover(index), true
}

func (r Run) SourceIsFilledOrClosed(source string) bool {
	if r.VerifiedCount() >= r.Target {
		return true
	}
	index, ok := r.SourceIndex(source)
	if !ok {
		return false
	}
	plan := r.Sources[index]
	return plan.Exhausted || r.SourceVerifiedCount(source) >= r.SourceQuotaWithCarryover(index)
}

func (r Run) PrimaryShortfallBefore(sourceIndex int) uint32 {
	var total uint32
	for i := 0; i < sourceIndex && i < len(r.Sources); i++ {
		source := r.Sources[i]
		if source.Fallback {
			continue
		}
		verified := r.SourceVerifiedCount(source.Name)
		if source.Target > verified {
			total += source.Target - verified
		}
	}
	return total
}

func (r Run) SourceQuotaWithCarryover(sourceIndex int) uint32 {
	source := r.Sources[sourceIndex]
	if source.Fallback {
		remaining := r.Target - minUint32(r.Target, r.VerifiedCount())
		if remaining > source.Target {
			return remaining
		}
		return source.Target
	}
	return source.Target + r.PrimaryShortfallBefore(sourceIndex)
}

func (r Run) NextSource() *NextSource {
	if r.State == RunStateNeedsReaudit || r.State == RunStateDone || r.State == RunStateBlocked {
		return nil
	}
	totalRemaining := r.Target - minUint32(r.Target, r.VerifiedCount())
	if totalRemaining == 0 {
		return nil
	}
	for index, source := range r.Sources {
		if source.Exhausted {
			continue
		}
		quota := r.SourceQuotaWithCarryover(index)
		verified := r.SourceVerifiedCount(source.Name)
		if source.Fallback || verified < quota {
			remainingForSource := quota - minUint32(quota, verified)
			if remainingForSource > totalRemaining {
				remainingForSource = totalRemaining
			}
			return &NextSource{
				Name:               source.Name,
				Quota:              quota,
				Verified:           verified,
				RemainingForSource: remainingForSource,
				RemainingForRun:    totalRemaining,
				Fallback:           source.Fallback,
			}
		}
	}
	return nil
}

func (r Run) HasCandidateEventForObservation(observation CandidateObservation) bool {
	for _, candidate := range r.Candidates {
		if CandidateMatchesObservation(candidate, observation) {
			return true
		}
	}
	return false
}

func (r Run) NextConnectableObservation() *CandidateObservation {
	next := r.NextSource()
	if next == nil {
		return nil
	}
	return r.NextConnectableObservationForSource(next.Name)
}

func (r Run) NextConnectableObservationForSource(source string) *CandidateObservation {
	if r.SourceIsFilledOrClosed(source) {
		return nil
	}
	for i := range r.Observations {
		observation := r.Observations[i]
		if observation.Source == source && observation.MenuState == "connectable" && !r.HasCandidateEventForObservation(observation) {
			return &r.Observations[i]
		}
	}
	return nil
}

func (r Run) NextTopUpObservation() *CandidateObservation {
	for i := range r.Observations {
		observation := r.Observations[i]
		if r.SourceIsFallback(observation.Source) && observation.MenuState == "connectable" && !r.HasTopUpBlockingEventForObservation(observation) {
			return &r.Observations[i]
		}
	}
	for i := range r.Observations {
		observation := r.Observations[i]
		if observation.MenuState == "connectable" && !r.HasTopUpBlockingEventForObservation(observation) {
			return &r.Observations[i]
		}
	}
	return nil
}

func (r Run) RealSendCapacityRemaining() uint32 {
	verified := r.VerifiedCount()
	if verified >= r.MaxRealSends {
		return 0
	}
	return r.MaxRealSends - verified
}

func (r Run) SourceIsFallback(source string) bool {
	for _, plan := range r.Sources {
		if plan.Name == source && plan.Fallback {
			return true
		}
	}
	return false
}

func (r Run) FinalAuditIsShort() bool {
	if r.VerifiedCount() < r.Target || r.State == RunStateDone || r.State == RunStateBlocked {
		return false
	}
	delta := r.AuditedDelta()
	return delta == nil || *delta < int64(r.Target)
}

func (r Run) PreserveForAuditTopUp(observation CandidateObservation) bool {
	return r.FinalAuditIsShort() && r.SourceIsFallback(observation.Source) && observation.MenuState == "connectable"
}

func (r Run) HasTopUpBlockingEventForObservation(observation CandidateObservation) bool {
	for _, candidate := range r.Candidates {
		if CandidateMatchesObservation(candidate, observation) && !IsAutoStaleSkip(candidate) {
			return true
		}
	}
	return false
}

func (r Run) CaptureRecommendation(source string, remaining uint32) CaptureRecommendation {
	var sourcePlan *SourcePlan
	for i := range r.Sources {
		if r.Sources[i].Name == source {
			sourcePlan = &r.Sources[i]
			break
		}
	}
	if sourcePlan == nil {
		return StandardCaptureRecommendation(remaining)
	}
	stats := SourceYieldStatsForRun(r, *sourcePlan)
	attempted := stats.PendingSends + stats.EmailRequiredSkips
	highEmailRequired := attempted >= 3 && float64(stats.EmailRequiredSkips)/float64(attempted) >= 0.30
	thinCaptureYield := stats.RawRowCount >= 25 && stats.ConnectableYield != nil && *stats.ConnectableYield <= 0.10
	_, hasCursor := r.CaptureCursors[source]
	hasResumeURL := false
	if hasCursor {
		cursor := r.CaptureCursors[source]
		hasResumeURL = cursor.ResumeURL != nil && *cursor.ResumeURL != ""
	}

	var recommendation CaptureRecommendation
	switch {
	case highEmailRequired:
		recommendation = ExpandedCaptureRecommendation(remaining, "high-email-required")
	case thinCaptureYield:
		recommendation = ExpandedCaptureRecommendation(remaining, "thin-capture-yield")
	default:
		recommendation = StandardCaptureRecommendation(remaining)
	}
	if hasResumeURL || recommendation.Pages >= 5 {
		recommendation.PlaywriterTimeoutMS = 90000
	}
	return recommendation
}

func StandardCaptureRecommendation(remaining uint32) CaptureRecommendation {
	var buffer uint32
	if remaining > 0 {
		buffer = 3
	}
	pages := uint32(3)
	if remaining+buffer > 10 {
		pages = 5
	}
	return CaptureRecommendation{
		Pages:                pages,
		StopAfterConnectable: minUint32(remaining+buffer, 25),
		Buffer:               buffer,
		Reason:               "standard-buffer",
		PlaywriterTimeoutMS:  45000,
	}
}

func ExpandedCaptureRecommendation(remaining uint32, reason string) CaptureRecommendation {
	buffer := remaining
	if buffer < 5 {
		buffer = 5
	}
	return CaptureRecommendation{
		Pages:                5,
		StopAfterConnectable: minUint32(remaining+buffer, 25),
		Buffer:               buffer,
		Reason:               reason,
		PlaywriterTimeoutMS:  90000,
	}
}

func (r Run) OperatorPlanWithReservoir(reservoir *CandidateReservoir) OperatorPlan {
	if r.State == RunStateNeedsReaudit {
		reason := "run is paused in NEEDS_REAUDIT"
		return OperatorPlan{Action: "reaudit", Reason: &reason}
	}
	if r.State == RunStateBlocked {
		reason := "run is blocked by the latest guarded send result"
		return OperatorPlan{Action: "blocked", Reason: &reason}
	}
	if r.VerifiedCount() >= r.Target {
		return OperatorPlan{Action: "final-audit"}
	}
	if candidate := r.NextConnectableObservation(); candidate != nil {
		if r.RealSendCapacityRemaining() == 0 {
			reason := fmt.Sprintf("real-send cap reached: %d/%d verified sends", r.VerifiedCount(), r.MaxRealSends)
			return OperatorPlan{Action: "blocked", Reason: &reason}
		}
		capacity := r.RealSendCapacityRemaining()
		return OperatorPlan{
			Action:                    "send-candidate",
			Source:                    &candidate.Source,
			Name:                      &candidate.Name,
			ProfileURL:                candidate.ProfileURL,
			RealSendCapacityRemaining: &capacity,
		}
	}
	if next := r.NextSource(); next != nil {
		source := next.Name
		if reservoir != nil {
			available := len(reservoir.AvailableForRunSource(r, source))
			if available > 0 {
				remaining := next.RemainingForSource
				return OperatorPlan{Action: "use-reservoir", Source: &source, Remaining: &remaining, Available: &available}
			}
		}
		remaining := next.RemainingForSource
		capture := r.CaptureRecommendation(source, next.RemainingForSource)
		var resumeURL *string
		var cursor *SourceCaptureCursor
		if stored, ok := r.CaptureCursors[source]; ok {
			resumeURL = stored.ResumeURL
			copy := stored
			cursor = &copy
		}
		return OperatorPlan{
			Action:    "capture-source",
			Source:    &source,
			Remaining: &remaining,
			Capture:   &capture,
			ResumeURL: resumeURL,
			Cursor:    cursor,
		}
	}
	reason := "no connectable candidate and no available source"
	return OperatorPlan{Action: "blocked", Reason: &reason}
}

func (r Run) OperatorPlan() OperatorPlan {
	return r.OperatorPlanWithReservoir(nil)
}

func (r Run) SentInvitationEvents() []CandidateEvent {
	events := []CandidateEvent{}
	for _, candidate := range r.Candidates {
		if candidate.Status == CandidateStatusPending || candidate.Status == CandidateStatusAuditTopUp {
			events = append(events, candidate)
		}
	}
	return events
}

func (r CandidateReservoir) AvailableForRunSource(run Run, source string) []CandidateObservation {
	result := []CandidateObservation{}
	for _, observation := range r.Observations {
		if observation.Source != source || observation.MenuState != "connectable" {
			continue
		}
		if run.HasCandidateEventForObservation(observation) {
			continue
		}
		existsInRun := false
		for _, existing := range run.Observations {
			if SameObservationIdentity(existing, observation) {
				existsInRun = true
				break
			}
		}
		if !existsInRun {
			result = append(result, observation)
		}
	}
	return result
}

func (l *AcceptanceLedger) Normalize() {
	if l.Invitations == nil {
		l.Invitations = []AcceptanceInvitation{}
	}
	for i := range l.Invitations {
		if l.Invitations[i].History == nil {
			l.Invitations[i].History = []AcceptanceOutcomeEvent{}
		}
	}
}

func (l *AcceptanceLedger) UpsertFromRun(run Run) int {
	inserted := 0
	for _, event := range run.SentInvitationEvents() {
		if l.UpsertInvitation(run.ID, run.Date, event) {
			inserted++
		}
	}
	return inserted
}

func (l *AcceptanceLedger) UpsertFromEvents(runID uuid.UUID, runDate Date, events []CandidateEvent) int {
	inserted := 0
	for _, event := range events {
		if event.Status != CandidateStatusPending && event.Status != CandidateStatusAuditTopUp {
			continue
		}
		if l.UpsertInvitation(runID, runDate, event) {
			inserted++
		}
	}
	return inserted
}

func (l *AcceptanceLedger) UpsertInvitation(runID uuid.UUID, runDate Date, event CandidateEvent) bool {
	key := NewAcceptanceKey(event.Source, event.Name, event.ProfileURL)
	for i := range l.Invitations {
		if l.Invitations[i].Key().Equal(key) {
			if l.Invitations[i].RunID != runID && l.Invitations[i].SentAt.After(event.At) {
				l.Invitations[i].RunID = runID
				l.Invitations[i].RunDate = runDate
				l.Invitations[i].SentAt = event.At
			}
			return false
		}
	}
	l.Invitations = append(l.Invitations, AcceptanceInvitation{
		RunID:           runID,
		RunDate:         runDate,
		Source:          event.Source,
		Name:            event.Name,
		ProfileURL:      event.ProfileURL,
		SentAt:          event.At,
		LatestStatus:    AcceptanceStatusSent,
		LatestCheckedAt: nil,
		History:         []AcceptanceOutcomeEvent{},
	})
	return true
}

func (l *AcceptanceLedger) ImportOutcomes(artifact AcceptanceOutcomeArtifact) AcceptanceImportSummary {
	summary := AcceptanceImportSummary{}
	for _, row := range artifact.Rows {
		summary.Rows++
		key := NewAcceptanceKey(row.Source, row.Name, row.ProfileURL)
		found := false
		for i := range l.Invitations {
			if !l.Invitations[i].Key().Equal(key) {
				continue
			}
			checkedAt := time.Now()
			if row.CheckedAt != nil {
				checkedAt = *row.CheckedAt
			}
			event := AcceptanceOutcomeEvent{
				At:           checkedAt,
				Status:       row.Status,
				Note:         row.Note,
				Relationship: row.Relationship,
				Evidence:     row.Evidence,
			}
			l.Invitations[i].LatestStatus = row.Status
			l.Invitations[i].LatestCheckedAt = &checkedAt
			l.Invitations[i].History = append(l.Invitations[i].History, event)
			summary.Matched++
			found = true
			break
		}
		if !found {
			summary.Unmatched++
		}
	}
	return summary
}

func (l AcceptanceLedger) EligibleForCheck(minAgeDays int64, maxAgeDays *int64) []AcceptanceInvitation {
	now := time.Now()
	result := []AcceptanceInvitation{}
	for _, invitation := range l.Invitations {
		if invitation.LatestStatus == AcceptanceStatusAccepted || invitation.LatestStatus == AcceptanceStatusWithdrawn {
			continue
		}
		if invitation.ProfileURL == nil {
			continue
		}
		ageDays := int64(now.Sub(invitation.SentAt).Hours() / 24)
		if ageDays >= minAgeDays && (maxAgeDays == nil || ageDays <= *maxAgeDays) {
			result = append(result, invitation)
		}
	}
	return result
}

func (l AcceptanceLedger) Report(minAgeDays int64, maxAgeDays *int64) AcceptanceReport {
	now := time.Now()
	report := AcceptanceReport{
		MinAgeDays: minAgeDays,
		MaxAgeDays: maxAgeDays,
		BySource:   map[string]AcceptanceSourceReport{},
	}
	for _, invitation := range l.Invitations {
		ageDays := int64(now.Sub(invitation.SentAt).Hours() / 24)
		if ageDays < minAgeDays || (maxAgeDays != nil && ageDays > *maxAgeDays) {
			continue
		}
		report.Add(invitation.Source, invitation.LatestStatus, invitation.LatestCheckedAt != nil)
	}
	return report
}

func (l AcceptanceLedger) AcceptedForFollowup(followups AcceptanceFollowupLedger, includeDrafted bool) []AcceptedDraftCandidate {
	result := []AcceptedDraftCandidate{}
	for _, invitation := range l.Invitations {
		if invitation.LatestStatus != AcceptanceStatusAccepted {
			continue
		}
		var acceptedEvent *AcceptanceOutcomeEvent
		for i := len(invitation.History) - 1; i >= 0; i-- {
			if invitation.History[i].Status == AcceptanceStatusAccepted {
				acceptedEvent = &invitation.History[i]
				break
			}
		}
		var acceptedAt time.Time
		if acceptedEvent != nil {
			acceptedAt = acceptedEvent.At
		} else if invitation.LatestCheckedAt != nil {
			acceptedAt = *invitation.LatestCheckedAt
		} else {
			continue
		}
		candidate := AcceptedDraftCandidate{
			RunID:      invitation.RunID,
			RunDate:    invitation.RunDate,
			Source:     invitation.Source,
			Name:       invitation.Name,
			ProfileURL: invitation.ProfileURL,
			SentAt:     invitation.SentAt,
			AcceptedAt: acceptedAt,
		}
		if acceptedEvent != nil {
			candidate.Relationship = acceptedEvent.Relationship
			candidate.AcceptanceNote = acceptedEvent.Note
			candidate.AcceptanceEvidence = acceptedEvent.Evidence
		}
		if includeDrafted || !followups.HasDraftFor(candidate) {
			result = append(result, candidate)
		}
	}
	return result
}

func NewAcceptanceKey(source, name string, profileURL *string) AcceptanceKey {
	var normalized *string
	if profileURL != nil {
		value := NormalizeLinkedInURL(*profileURL)
		normalized = &value
	}
	return AcceptanceKey{Source: source, Name: name, ProfileURL: normalized}
}

func (k AcceptanceKey) Equal(other AcceptanceKey) bool {
	if k.Source != other.Source || k.Name != other.Name {
		return false
	}
	if k.ProfileURL == nil || other.ProfileURL == nil {
		return k.ProfileURL == nil && other.ProfileURL == nil
	}
	return *k.ProfileURL == *other.ProfileURL
}

func (i AcceptanceInvitation) Key() AcceptanceKey {
	return NewAcceptanceKey(i.Source, i.Name, i.ProfileURL)
}

func (r *AcceptanceReport) Add(source string, status AcceptanceStatus, checked bool) {
	r.TotalSent++
	sourceReport := r.BySource[source]
	sourceReport.TotalSent++
	if checked {
		r.Checked++
		sourceReport.Checked++
	} else {
		r.Unchecked++
		sourceReport.Unchecked++
	}
	switch status {
	case AcceptanceStatusSent:
	case AcceptanceStatusPending:
		r.Pending++
		sourceReport.Pending++
	case AcceptanceStatusAccepted:
		r.Accepted++
		sourceReport.Accepted++
	case AcceptanceStatusConnectable:
		r.Connectable++
		sourceReport.Connectable++
	case AcceptanceStatusWithdrawn:
		r.Withdrawn++
		sourceReport.Withdrawn++
	case AcceptanceStatusUnknown:
		r.Unknown++
		sourceReport.Unknown++
	case AcceptanceStatusBlocked:
		r.Blocked++
		sourceReport.Blocked++
	case AcceptanceStatusFailed:
		r.Failed++
		sourceReport.Failed++
	}
	r.BySource[source] = sourceReport
}

func (r *PendingCleanupRun) Normalize() {
	if r.ThresholdDays == 0 && r.ThresholdMonths > 0 {
		r.ThresholdDays = r.ThresholdMonths * 30
	}
	if r.Audits == nil {
		r.Audits = []AuditEvent{}
	}
	if r.Observations == nil {
		r.Observations = []PendingCandidateObservation{}
	}
	if r.Withdrawals == nil {
		r.Withdrawals = []PendingWithdrawEvent{}
	}
	if r.Notes == nil {
		r.Notes = []string{}
	}
}

func (r *PendingCleanupRun) MarkUpdated() {
	r.UpdatedAt = time.Now()
}

func (r PendingCleanupRun) WithdrawnCount() uint32 {
	var count uint32
	for _, event := range r.Withdrawals {
		if event.Status == PendingWithdrawStatusWithdrawn {
			count++
		}
	}
	return count
}

func (r PendingCleanupRun) AuditedDelta() *int64 {
	if r.StartAudit == nil || r.LatestAudit == nil {
		return nil
	}
	delta := int64(*r.LatestAudit) - int64(*r.StartAudit)
	return &delta
}

func (r PendingCleanupRun) HasWithdrawEventForObservation(observation PendingCandidateObservation) bool {
	for _, event := range r.Withdrawals {
		if event.ProfileURL != nil && observation.ProfileURL != nil {
			if *event.ProfileURL == *observation.ProfileURL {
				return true
			}
			continue
		}
		if event.Name == observation.Name && event.AgeText == observation.AgeText {
			return true
		}
	}
	return false
}

func (r PendingCleanupRun) NextEligibleObservation() *PendingCandidateObservation {
	for i := range r.Observations {
		observation := r.Observations[i]
		if observation.Eligible && !r.HasWithdrawEventForObservation(observation) {
			return &r.Observations[i]
		}
	}
	return nil
}

func (r PendingCleanupRun) WithdrawCapacityRemaining() uint32 {
	count := r.WithdrawnCount()
	if count >= r.MaxWithdrawals {
		return 0
	}
	return r.MaxWithdrawals - count
}

func (r PendingCleanupRun) OperatorPlan() PendingCleanupPlan {
	if r.State == PendingCleanupStateNeedsReaudit {
		reason := "cleanup is paused in NEEDS_REAUDIT"
		return PendingCleanupPlan{Action: "reaudit", Reason: &reason}
	}
	if r.WithdrawCapacityRemaining() == 0 {
		return PendingCleanupPlan{Action: "final-audit"}
	}
	if candidate := r.NextEligibleObservation(); candidate != nil {
		remaining := r.WithdrawCapacityRemaining()
		return PendingCleanupPlan{
			Action:                    "withdraw-candidate",
			Name:                      &candidate.Name,
			ProfileURL:                candidate.ProfileURL,
			AgeText:                   &candidate.AgeText,
			WithdrawCapacityRemaining: &remaining,
		}
	}
	reason := "no unrecorded eligible stale invitation is imported"
	return PendingCleanupPlan{Action: "capture-more", Reason: &reason}
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for key := range m {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func ptr[T any](value T) *T {
	return &value
}

func minUint32(left, right uint32) uint32 {
	if left < right {
		return left
	}
	return right
}

func cleanInline(value string) string {
	return strings.Join(strings.Fields(value), " ")
}
