package app

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"
)

type SalesNavCapture struct {
	CapturedAt     *string               `json:"capturedAt"`
	Source         *string               `json:"source"`
	URL            *string               `json:"url"`
	ResumeURL      *string               `json:"resumeUrl"`
	Page           *SalesNavCapturePage  `json:"page"`
	Pages          []SalesNavCapturePage `json:"pages"`
	StateCounts    map[string]uint32     `json:"stateCounts"`
	RawRowCount    *uint32               `json:"rawRowCount"`
	OutputRowCount *uint32               `json:"outputRowCount"`
	Rows           []SalesNavCaptureRow  `json:"rows"`
}

type SalesNavCapturePage struct {
	URL       *string `json:"url"`
	PageLabel *string `json:"pageLabel"`
}

type ImportCaptureOptions struct {
	OnlyConnectable bool
}

type SalesNavAudit struct {
	PeopleCount uint32   `json:"peopleCount"`
	RecentNames []string `json:"recentNames"`
}

func (a *SalesNavAudit) UnmarshalJSON(data []byte) error {
	type primary struct {
		PeopleCount      *uint32  `json:"peopleCount"`
		PeopleCountSnake *uint32  `json:"people_count"`
		RecentNames      []string `json:"recentNames"`
		RecentNamesSnake []string `json:"recent_names"`
	}
	var value primary
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	if value.PeopleCount != nil {
		a.PeopleCount = *value.PeopleCount
	} else if value.PeopleCountSnake != nil {
		a.PeopleCount = *value.PeopleCountSnake
	}
	if value.RecentNames != nil {
		a.RecentNames = value.RecentNames
	} else if value.RecentNamesSnake != nil {
		a.RecentNames = value.RecentNamesSnake
	} else {
		a.RecentNames = []string{}
	}
	return nil
}

func LoadSalesNavAudit(path string) (SalesNavAudit, error) {
	var audit SalesNavAudit
	if err := readJSONFile(path, &audit, "reading audit "+path, "parsing audit "+path); err != nil {
		return SalesNavAudit{}, err
	}
	if audit.RecentNames == nil {
		audit.RecentNames = []string{}
	}
	return audit, nil
}

type SalesNavSendResult struct {
	Candidate SalesNavSendCandidate `json:"candidate"`
	Status    string                `json:"status"`
	Send      json.RawMessage       `json:"send"`
}

func LoadSalesNavSendResult(path string) (SalesNavSendResult, error) {
	var result SalesNavSendResult
	if err := readJSONFile(path, &result, "reading send "+path, "parsing send "+path); err != nil {
		return SalesNavSendResult{}, err
	}
	return result, nil
}

func (r SalesNavSendResult) ToCandidateStatus() (CandidateStatus, string) {
	switch r.Status {
	case "pending-verified":
		return CandidateStatusPending, "salesnav-send-one verified Connect - Pending"
	case "already-pending":
		return CandidateStatusAlreadyPending, "salesnav-send-one found already pending"
	case "email-required":
		return CandidateStatusSkipped, "salesnav-send-one stopped on email-required invite flow"
	default:
		send := "no send detail"
		if len(r.Send) > 0 && string(r.Send) != "null" {
			send = string(r.Send)
		}
		return CandidateStatusFailed, fmt.Sprintf("salesnav-send-one status %s; %s", r.Status, send)
	}
}

type SalesNavSendCandidate struct {
	Source     string  `json:"source"`
	Name       string  `json:"name"`
	ProfileURL *string `json:"profileUrl"`
}

func (c *SalesNavSendCandidate) UnmarshalJSON(data []byte) error {
	type primary struct {
		Source          string  `json:"source"`
		Name            string  `json:"name"`
		ProfileURL      *string `json:"profileUrl"`
		ProfileURLSnake *string `json:"profile_url"`
	}
	var value primary
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	c.Source = value.Source
	c.Name = value.Name
	c.ProfileURL = value.ProfileURL
	if c.ProfileURL == nil {
		c.ProfileURL = value.ProfileURLSnake
	}
	return nil
}

func LoadSalesNavCapture(path string) (SalesNavCapture, error) {
	var capture SalesNavCapture
	if err := readJSONFile(path, &capture, "reading capture "+path, "parsing capture "+path); err != nil {
		return SalesNavCapture{}, err
	}
	if capture.Pages == nil {
		capture.Pages = []SalesNavCapturePage{}
	}
	if capture.StateCounts == nil {
		capture.StateCounts = map[string]uint32{}
	}
	if capture.Rows == nil {
		capture.Rows = []SalesNavCaptureRow{}
	}
	return capture, nil
}

type SalesNavCaptureRow struct {
	Index        uint32                     `json:"index"`
	Name         *string                    `json:"name"`
	Text         *string                    `json:"text"`
	ProfileURL   *string                    `json:"profileUrl"`
	ScrollURN    *string                    `json:"scrollUrn"`
	VisibleState json.RawMessage            `json:"visibleState"`
	MenuState    *string                    `json:"menuState"`
	MenuLabels   []SalesNavCaptureMenuLabel `json:"menuLabels"`
	Links        []SalesNavCaptureLink      `json:"links"`
	RowHTMLPath  *string                    `json:"rowHtmlPath"`
}

type SalesNavCaptureMenuLabel struct {
	Text *string `json:"text"`
	Aria *string `json:"aria"`
}

type SalesNavCaptureLink struct {
	Text *string `json:"text"`
	Aria *string `json:"aria"`
	Href *string `json:"href"`
}

func CaptureStateCount(capture SalesNavCapture, state string) uint32 {
	if value, ok := capture.StateCounts[state]; ok {
		return value
	}
	var count uint32
	for _, row := range capture.Rows {
		if row.MenuState != nil && *row.MenuState == state {
			count++
		}
	}
	return count
}

func SameObservationIdentity(left, right CandidateObservation) bool {
	if left.ProfileURL != nil && right.ProfileURL != nil {
		return NormalizeLinkedInURL(*left.ProfileURL) == NormalizeLinkedInURL(*right.ProfileURL)
	}
	return left.Source == right.Source && left.Name == right.Name
}

func CandidateMatchesObservation(candidate CandidateEvent, observation CandidateObservation) bool {
	if candidate.ProfileURL != nil && observation.ProfileURL != nil {
		return NormalizeLinkedInURL(*candidate.ProfileURL) == NormalizeLinkedInURL(*observation.ProfileURL)
	}
	return candidate.Name == observation.Name && candidate.Source == observation.Source
}

func IsAutoStaleSkip(candidate CandidateEvent) bool {
	return candidate.Status == CandidateStatusSkipped && candidate.Note != nil && strings.Contains(*candidate.Note, "auto-skipped stale imported candidate")
}

func SalesProfileURNToLeadURL(urn string) *string {
	start := strings.Index(urn, "(")
	if start < 0 || !strings.HasSuffix(urn, ")") {
		return nil
	}
	tuple := strings.TrimSuffix(urn[start+1:], ")")
	parts := strings.Split(tuple, ",")
	if len(parts) != 3 {
		return nil
	}
	for i := range parts {
		parts[i] = strings.TrimSpace(parts[i])
		if parts[i] == "" {
			return nil
		}
	}
	value := fmt.Sprintf("https://www.linkedin.com/sales/lead/%s,%s,%s", parts[0], parts[1], parts[2])
	return &value
}

func PushTiming(run *Run, phase string, source *string, started time.Time, detail *string) {
	durationMS := uint64(time.Since(started).Milliseconds())
	run.Timings = append(run.Timings, RunTimingEvent{
		At:         time.Now(),
		Phase:      phase,
		Source:     source,
		DurationMS: durationMS,
		Detail:     detail,
	})
	run.MarkUpdated()
}

func CaptureToObservations(source string, capture SalesNavCapture, options ImportCaptureOptions) []CandidateObservation {
	observations := []CandidateObservation{}
	for _, row := range capture.Rows {
		if row.Name == nil || strings.TrimSpace(*row.Name) == "" {
			continue
		}
		menuState := "unknown"
		if row.MenuState != nil {
			menuState = *row.MenuState
		}
		if options.OnlyConnectable && menuState != "connectable" {
			continue
		}
		labels := []string{}
		for _, label := range row.MenuLabels {
			var value *string
			if label.Text != nil {
				value = label.Text
			} else {
				value = label.Aria
			}
			if value == nil {
				continue
			}
			cleaned := strings.TrimSpace(*value)
			if cleaned != "" {
				labels = append(labels, cleaned)
			}
		}
		profileURL := row.ProfileURL
		if profileURL == nil && row.ScrollURN != nil {
			profileURL = SalesProfileURNToLeadURL(*row.ScrollURN)
		}
		visibleState := row.VisibleState
		if len(visibleState) == 0 {
			visibleState = json.RawMessage("null")
		}
		observations = append(observations, CandidateObservation{
			ImportedAt:      time.Now(),
			CapturedAt:      capture.CapturedAt,
			Source:          source,
			Index:           row.Index,
			Name:            strings.TrimSpace(*row.Name),
			ProfileURL:      profileURL,
			SalesProfileURN: row.ScrollURN,
			VisibleState:    visibleState,
			MenuState:       menuState,
			MenuLabels:      labels,
			RowHTMLPath:     row.RowHTMLPath,
		})
	}
	return observations
}

func UpdateCaptureCursor(run *Run, source string, capture SalesNavCapture) {
	var lastPage *SalesNavCapturePage
	if capture.Page != nil {
		lastPage = capture.Page
	} else if len(capture.Pages) > 0 {
		lastPage = &capture.Pages[len(capture.Pages)-1]
	}
	resumeURL := capture.ResumeURL
	if resumeURL == nil {
		resumeURL = capture.URL
	}
	if resumeURL == nil && lastPage != nil {
		resumeURL = lastPage.URL
	}
	capturedPages := uint32(len(capture.Pages))
	if capturedPages == 0 && capture.Page != nil {
		capturedPages = 1
	}
	rawRowCount := uint32(len(capture.Rows))
	if capture.RawRowCount != nil {
		rawRowCount = *capture.RawRowCount
	}
	outputRowCount := uint32(len(capture.Rows))
	if capture.OutputRowCount != nil {
		outputRowCount = *capture.OutputRowCount
	}
	var pageLabel *string
	if lastPage != nil {
		pageLabel = lastPage.PageLabel
	}
	run.CaptureCursors[source] = SourceCaptureCursor{
		Source:              source,
		UpdatedAt:           time.Now(),
		CapturedAt:          capture.CapturedAt,
		ResumeURL:           resumeURL,
		PageLabel:           pageLabel,
		CapturedPages:       capturedPages,
		RawRowCount:         rawRowCount,
		OutputRowCount:      outputRowCount,
		ConnectableCount:    CaptureStateCount(capture, "connectable"),
		AlreadyPendingCount: CaptureStateCount(capture, "already-pending"),
		MissingTriggerCount: CaptureStateCount(capture, "missing-trigger"),
		StateCounts:         capture.StateCounts,
	}
}

func ImportCapture(run *Run, capture SalesNavCapture, options ImportCaptureOptions) (int, error) {
	source := ""
	if capture.Source != nil {
		source = *capture.Source
	} else if next := run.NextSource(); next != nil {
		source = next.Name
	} else {
		return 0, fmt.Errorf("capture did not include source and run has no next source")
	}
	if err := EnsureKnownSource(*run, source); err != nil {
		return 0, err
	}
	UpdateCaptureCursor(run, source, capture)
	imported := 0
	for _, observation := range CaptureToObservations(source, capture, options) {
		existingIndex := -1
		for i, existing := range run.Observations {
			if SameObservationIdentity(existing, observation) {
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

func ImportCaptureIntoReservoir(reservoir *CandidateReservoir, capture SalesNavCapture, options ImportCaptureOptions) (int, error) {
	if capture.Source == nil {
		return 0, fmt.Errorf("capture did not include source")
	}
	imported := 0
	for _, observation := range CaptureToObservations(*capture.Source, capture, options) {
		existingIndex := -1
		for i, existing := range reservoir.Observations {
			if SameObservationIdentity(existing, observation) {
				existingIndex = i
				break
			}
		}
		if existingIndex >= 0 {
			reservoir.Observations[existingIndex] = observation
		} else {
			reservoir.Observations = append(reservoir.Observations, observation)
			imported++
		}
	}
	now := time.Now()
	reservoir.UpdatedAt = &now
	return imported, nil
}

func FillRunFromReservoir(run *Run, reservoir *CandidateReservoir, source string, limit int) (int, error) {
	if err := EnsureKnownSource(*run, source); err != nil {
		return 0, err
	}
	selectedKeys := map[string]bool{}
	imported := 0
	for _, observation := range reservoir.AvailableForRunSource(*run, source) {
		if imported >= limit {
			break
		}
		observation.ImportedAt = time.Now()
		selectedKeys[ObservationKey(observation)] = true
		run.Observations = append(run.Observations, observation)
		imported++
	}
	if imported > 0 {
		remaining := reservoir.Observations[:0]
		for _, observation := range reservoir.Observations {
			if !selectedKeys[ObservationKey(observation)] {
				remaining = append(remaining, observation)
			}
		}
		reservoir.Observations = remaining
		now := time.Now()
		reservoir.UpdatedAt = &now
		run.MarkUpdated()
	}
	return imported, nil
}

func FillRunFromReservoirForTopUp(run *Run, reservoir *CandidateReservoir, source string, limit int) (int, error) {
	if err := EnsureKnownSource(*run, source); err != nil {
		return 0, err
	}
	selectedKeys := map[string]bool{}
	selected := []CandidateObservation{}
	for _, observation := range reservoir.Observations {
		if len(selected) >= limit {
			break
		}
		if observation.Source != source || observation.MenuState != "connectable" || run.HasTopUpBlockingEventForObservation(observation) {
			continue
		}
		exists := false
		for _, existing := range run.Observations {
			if SameObservationIdentity(existing, observation) {
				exists = true
				break
			}
		}
		if exists {
			continue
		}
		observation.ImportedAt = time.Now()
		selectedKeys[ObservationKey(observation)] = true
		selected = append(selected, observation)
	}
	if len(selected) > 0 {
		run.Observations = append(run.Observations, selected...)
		remaining := reservoir.Observations[:0]
		for _, observation := range reservoir.Observations {
			if !selectedKeys[ObservationKey(observation)] {
				remaining = append(remaining, observation)
			}
		}
		reservoir.Observations = remaining
		now := time.Now()
		reservoir.UpdatedAt = &now
		run.MarkUpdated()
	}
	return len(selected), nil
}

func ObservationKey(observation CandidateObservation) string {
	profile := ""
	if observation.ProfileURL != nil {
		profile = NormalizeLinkedInURL(*observation.ProfileURL)
	}
	return observation.Source + "\x00" + observation.Name + "\x00" + profile
}

func SourceYieldStatsForRun(run Run, source SourcePlan) SourceYieldStats {
	cursor, hasCursor := run.CaptureCursors[source.Name]
	rawRowCount := uint32(0)
	connectableCount := uint32(0)
	alreadyPendingCount := uint32(0)
	if hasCursor {
		rawRowCount = cursor.RawRowCount
		connectableCount = cursor.ConnectableCount
		alreadyPendingCount = cursor.AlreadyPendingCount
	} else {
		for _, observation := range run.Observations {
			if observation.Source == source.Name && observation.MenuState == "connectable" {
				connectableCount++
			}
		}
	}
	var emailRequiredSkips uint32
	for _, candidate := range run.Candidates {
		if candidate.Source != source.Name || candidate.Status != CandidateStatusSkipped || candidate.Note == nil {
			continue
		}
		if strings.Contains(strings.ToLower(*candidate.Note), "email-required") {
			emailRequiredSkips++
		}
	}
	pendingSends := run.SourceVerifiedCount(source.Name)
	var yield *float64
	if rawRowCount > 0 {
		value := float64(connectableCount) / float64(rawRowCount)
		yield = &value
	}
	recommendation := "no capture data"
	if yield != nil {
		switch {
		case rawRowCount >= 50 && *yield <= 0.05:
			recommendation = "low-yield: consider reservoir/fallback before deeper capture"
		case rawRowCount >= 25 && *yield <= 0.10:
			recommendation = "thin: capture with a small buffer and be ready to carry over"
		default:
			recommendation = "ok"
		}
	}
	return SourceYieldStats{
		Source:              source.Name,
		RawRowCount:         rawRowCount,
		ConnectableCount:    connectableCount,
		AlreadyPendingCount: alreadyPendingCount,
		EmailRequiredSkips:  emailRequiredSkips,
		PendingSends:        pendingSends,
		ConnectableYield:    yield,
		Recommendation:      recommendation,
	}
}

func SourceYieldReport(run Run) []SourceYieldStats {
	result := make([]SourceYieldStats, 0, len(run.Sources))
	for _, source := range run.Sources {
		result = append(result, SourceYieldStatsForRun(run, source))
	}
	return result
}

func LowYieldSourceNames(run Run, minRawRows uint32, maxConnectableYield float64) []string {
	names := []string{}
	for _, stats := range SourceYieldReport(run) {
		if stats.RawRowCount >= minRawRows && stats.ConnectableYield != nil && *stats.ConnectableYield <= maxConnectableYield {
			names = append(names, stats.Source)
		}
	}
	return names
}

func SaveRawJSON(path string, value any) error {
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	return os.WriteFile(path, raw, 0o644)
}
