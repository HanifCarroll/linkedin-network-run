package app

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func testDate(t *testing.T, value string) Date {
	t.Helper()
	date, err := ParseDate(value)
	if err != nil {
		t.Fatal(err)
	}
	return date
}

func testRun(t *testing.T, target uint32) Run {
	t.Helper()
	return NewRunDefault(target, testDate(t, "2026-05-26"))
}

func connectableObservation(source, name, profileURL string) CandidateObservation {
	return CandidateObservation{
		ImportedAt:   time.Now(),
		Source:       source,
		Index:        0,
		Name:         name,
		ProfileURL:   &profileURL,
		VisibleState: json.RawMessage("null"),
		MenuState:    "connectable",
		MenuLabels:   []string{"Connect"},
	}
}

func completedShortAuditRun(t *testing.T) Run {
	t.Helper()
	run := NewRunDefault(1, testDate(t, "2026-06-13"))
	start := uint32(100)
	latest := uint32(100)
	run.StartAudit = &start
	run.LatestAudit = &latest
	run.State = RunStateFinalReconcile
	profile := "https://www.linkedin.com/sales/lead/primary"
	run.Candidates = append(run.Candidates, CandidateEvent{
		At:         time.Now(),
		Source:     "ASAP - Startup CTO Eng Leaders",
		Name:       "Primary Sent",
		ProfileURL: &profile,
		Status:     CandidateStatusPending,
	})
	return run
}

func TestDefaultMixMatchesCurrent30RequestContract(t *testing.T) {
	sources := DefaultSources(30)
	var primaryTotal uint32
	for _, source := range sources {
		if !source.Fallback {
			primaryTotal += source.Target
		}
	}
	if primaryTotal != 30 {
		t.Fatalf("primary total = %d, want 30", primaryTotal)
	}
	want := []struct {
		name   string
		target uint32
	}{
		{"ASAP - Agency Owners Delivery", 9},
		{"ASAP - Contract Recruiters Staffing", 7},
		{"ASAP - Startup CTO Eng Leaders", 6},
		{"ASAP - High-Intent SaaS AI Founders", 5},
		{"ASAP - Vertical Proof Buyers", 3},
	}
	for i, item := range want {
		if sources[i].Name != item.name || sources[i].Target != item.target {
			t.Fatalf("source %d = %s/%d, want %s/%d", i, sources[i].Name, sources[i].Target, item.name, item.target)
		}
	}
	if got := sources[5].Name; got != "FO - Founders - Urgent" {
		t.Fatalf("fallback source = %q", got)
	}
}

func TestExhaustedSourceCarriesRemainingIntoNextSource(t *testing.T) {
	run := NewRunDefault(30, testDate(t, "2026-05-26"))
	run.Candidates = append(run.Candidates, CandidateEvent{
		At:     time.Now(),
		Source: "ASAP - Agency Owners Delivery",
		Name:   "A",
		Status: CandidateStatusPending,
	})
	run.Sources[0].Exhausted = true
	next := run.NextSource()
	if next == nil {
		t.Fatal("next source is nil")
	}
	if next.Name != "ASAP - Contract Recruiters Staffing" || next.Quota != 15 {
		t.Fatalf("next = %#v", next)
	}
}

func TestAuditedDeltaUsesSentPeopleCount(t *testing.T) {
	run := testRun(t, 22)
	start := uint32(913)
	latest := uint32(936)
	run.StartAudit = &start
	run.LatestAudit = &latest
	if got := run.AuditedDelta(); got == nil || *got != 23 {
		t.Fatalf("delta = %v, want 23", got)
	}
}

func TestNeedsReauditBlocksNextSource(t *testing.T) {
	run := testRun(t, 22)
	run.State = RunStateNeedsReaudit
	if next := run.NextSource(); next != nil {
		t.Fatalf("next source = %#v, want nil", next)
	}
}

func TestImportCaptureExposesNextConnectableCandidate(t *testing.T) {
	run := testRun(t, 22)
	capturedAt := "2026-05-26T12:00:00Z"
	source := "ASAP - Agency Owners Delivery"
	capture := SalesNavCapture{
		CapturedAt:  &capturedAt,
		Source:      &source,
		StateCounts: map[string]uint32{},
		Rows: []SalesNavCaptureRow{
			{
				Index:      0,
				Name:       ptr("Already Pending"),
				ProfileURL: ptr("https://www.linkedin.com/sales/lead/a"),
				MenuState:  ptr("already-pending"),
				MenuLabels: []SalesNavCaptureMenuLabel{{Text: ptr("Connect - Pending")}},
			},
			{
				Index:      1,
				Name:       ptr("Connectable Founder"),
				ProfileURL: ptr("https://www.linkedin.com/sales/lead/b"),
				MenuState:  ptr("connectable"),
				MenuLabels: []SalesNavCaptureMenuLabel{{Text: ptr("Connect")}},
			},
		},
	}
	imported, err := ImportCapture(&run, capture, ImportCaptureOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if imported != 2 {
		t.Fatalf("imported = %d, want 2", imported)
	}
	if got := run.NextConnectableObservation().Name; got != "Connectable Founder" {
		t.Fatalf("next candidate = %q", got)
	}
}

func TestImportCaptureCanFilterToConnectableRows(t *testing.T) {
	run := testRun(t, 22)
	source := "ASAP - Agency Owners Delivery"
	capture := SalesNavCapture{
		Source:      &source,
		StateCounts: map[string]uint32{},
		Rows: []SalesNavCaptureRow{
			{Index: 0, Name: ptr("Already Pending"), ProfileURL: ptr("https://www.linkedin.com/sales/lead/a"), MenuState: ptr("already-pending")},
			{Index: 1, Name: ptr("Connectable Founder"), ProfileURL: ptr("https://www.linkedin.com/sales/lead/b"), MenuState: ptr("connectable")},
		},
	}
	imported, err := ImportCapture(&run, capture, ImportCaptureOptions{OnlyConnectable: true})
	if err != nil {
		t.Fatal(err)
	}
	if imported != 1 || len(run.Observations) != 1 || run.Observations[0].Name != "Connectable Founder" {
		t.Fatalf("unexpected import: imported=%d observations=%#v", imported, run.Observations)
	}
}

func TestImportCaptureDerivesProfileURLFromSalesProfileURN(t *testing.T) {
	run := testRun(t, 22)
	source := "ASAP - Agency Owners Delivery"
	urn := "urn:li:fs_salesProfile:(ACwAAACZuNoBDnWZnoEzJVGp-uptyWQSfIw87UM,NAME_SEARCH,HDgt)"
	capture := SalesNavCapture{
		Source:      &source,
		StateCounts: map[string]uint32{},
		Rows: []SalesNavCaptureRow{{
			Index:     0,
			Name:      ptr("Connectable Founder"),
			ScrollURN: &urn,
			MenuState: ptr("connectable"),
		}},
	}
	if _, err := ImportCapture(&run, capture, ImportCaptureOptions{OnlyConnectable: true}); err != nil {
		t.Fatal(err)
	}
	want := "https://www.linkedin.com/sales/lead/ACwAAACZuNoBDnWZnoEzJVGp-uptyWQSfIw87UM,NAME_SEARCH,HDgt"
	if got := *run.Observations[0].ProfileURL; got != want {
		t.Fatalf("profile url = %q, want %q", got, want)
	}
}

func TestImportCaptureDedupesSalesNavURLsWithTrackingParams(t *testing.T) {
	run := testRun(t, 22)
	source := "ASAP - Contract Recruiters Staffing"
	capture := SalesNavCapture{
		Source:      &source,
		StateCounts: map[string]uint32{},
		Rows: []SalesNavCaptureRow{
			{Index: 0, Name: ptr("Duplicate Lead"), ProfileURL: ptr("https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token?_ntb=session"), MenuState: ptr("connectable")},
			{Index: 1, Name: ptr("Duplicate Lead"), ProfileURL: ptr("https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token"), MenuState: ptr("connectable")},
		},
	}
	imported, err := ImportCapture(&run, capture, ImportCaptureOptions{OnlyConnectable: true})
	if err != nil {
		t.Fatal(err)
	}
	if imported != 1 || len(run.Observations) != 1 {
		t.Fatalf("imported=%d len=%d", imported, len(run.Observations))
	}
}

func TestNormalizeLinkedInURLDedupesSalesNavLeadAuthTokens(t *testing.T) {
	left := "https://www.linkedin.com/sales/lead/abc123,NAME_SEARCH,token-one?_ntb=session"
	right := "https://www.linkedin.com/sales/lead/abc123,SEARCH,token-two"
	if NormalizeLinkedInURL(left) != NormalizeLinkedInURL(right) {
		t.Fatalf("normalized urls differ: %q vs %q", NormalizeLinkedInURL(left), NormalizeLinkedInURL(right))
	}
}

func TestCandidateMatchingIgnoresSalesNavTrackingParams(t *testing.T) {
	candidateURL := "https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token?_ntb=session"
	candidate := CandidateEvent{
		At:         time.Now(),
		Source:     "ASAP - Contract Recruiters Staffing",
		Name:       "Tracked Lead",
		ProfileURL: &candidateURL,
		Status:     CandidateStatusPending,
	}
	observation := connectableObservation(
		"ASAP - Contract Recruiters Staffing",
		"Tracked Lead",
		"https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token",
	)
	if !CandidateMatchesObservation(candidate, observation) {
		t.Fatal("candidate did not match observation")
	}
}

func TestReservoirPlanAndFillReusesPrecapturedCandidates(t *testing.T) {
	run := testRun(t, 22)
	reservoir := CandidateReservoir{Observations: []CandidateObservation{
		connectableObservation("ASAP - Agency Owners Delivery", "Reservoir Founder", "https://www.linkedin.com/sales/lead/reservoir"),
	}}
	plan := run.OperatorPlanWithReservoir(&reservoir)
	if plan.Action != "use-reservoir" || valueOrEmpty(plan.Source) != "ASAP - Agency Owners Delivery" || uintValue(plan.Remaining) != 7 || intValue(plan.Available) != 1 {
		t.Fatalf("plan = %#v", plan)
	}
	imported, err := FillRunFromReservoir(&run, &reservoir, "ASAP - Agency Owners Delivery", 10)
	if err != nil {
		t.Fatal(err)
	}
	if imported != 1 || len(reservoir.Observations) != 0 || run.NextConnectableObservation().Name != "Reservoir Founder" {
		t.Fatalf("reservoir fill failed: imported=%d reservoir=%d", imported, len(reservoir.Observations))
	}
}

func TestFinalAuditShortPreservesFallbackCandidatesForTopUp(t *testing.T) {
	run := completedShortAuditRun(t)
	fallback := connectableObservation("FO - Founders - Urgent", "Fallback Top Up", "https://www.linkedin.com/sales/lead/fallback")
	run.Observations = append(run.Observations, fallback)
	note := "auto-skipped stale imported candidate after source closed or filled"
	run.Candidates = append(run.Candidates, CandidateEvent{
		At:         time.Now(),
		Source:     fallback.Source,
		Name:       fallback.Name,
		ProfileURL: fallback.ProfileURL,
		Status:     CandidateStatusSkipped,
		Note:       &note,
	})
	drained, err := DrainStaleConnectableCandidates(&run, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(drained) != 0 || run.NextTopUpObservation().Name != "Fallback Top Up" {
		t.Fatalf("drained=%d next=%#v", len(drained), run.NextTopUpObservation())
	}
}

func TestTopUpReservoirFillIgnoresOldAutoStaleSkip(t *testing.T) {
	run := completedShortAuditRun(t)
	fallback := connectableObservation("FO - Founders - Urgent", "Reservoir Top Up", "https://www.linkedin.com/sales/lead/reservoir-top-up")
	note := "auto-skipped stale imported candidate after source closed or filled"
	run.Candidates = append(run.Candidates, CandidateEvent{
		At:         time.Now(),
		Source:     fallback.Source,
		Name:       fallback.Name,
		ProfileURL: fallback.ProfileURL,
		Status:     CandidateStatusSkipped,
		Note:       &note,
	})
	reservoir := CandidateReservoir{Observations: []CandidateObservation{fallback}, UpdatedAt: TimePtr(time.Now())}
	imported, err := FillRunFromReservoirForTopUp(&run, &reservoir, "FO - Founders - Urgent", 5)
	if err != nil {
		t.Fatal(err)
	}
	if imported != 1 || len(reservoir.Observations) != 0 || run.NextTopUpObservation().Name != "Reservoir Top Up" {
		t.Fatalf("top-up fill failed")
	}
}

func TestSavedSearchURLResolvesFromSearchesArtifact(t *testing.T) {
	path := filepath.Join(t.TempDir(), "saved-searches.json")
	err := os.WriteFile(path, []byte(`{"searches":[{"name":"FO - Founders - Urgent","viewUrl":"https://www.linkedin.com/sales/search/people?savedSearchId=1"}]}`), 0o644)
	if err != nil {
		t.Fatal(err)
	}
	resolved, err := ResolveCaptureURL(nil, path, "FO - Founders - Urgent", "--url")
	if err != nil {
		t.Fatal(err)
	}
	if resolved != "https://www.linkedin.com/sales/search/people?savedSearchId=1" {
		t.Fatalf("resolved = %q", resolved)
	}
}

func TestCapturePlanExpandsBufferAfterEmailRequiredSkips(t *testing.T) {
	run := NewRunDefault(30, testDate(t, "2026-06-12"))
	for i := 0; i < 2; i++ {
		run.Candidates = append(run.Candidates, CandidateEvent{
			At:         time.Now(),
			Source:     "ASAP - Agency Owners Delivery",
			Name:       "Verified",
			ProfileURL: ptr("https://www.linkedin.com/sales/lead/verified"),
			Status:     CandidateStatusPending,
		})
	}
	for i := 0; i < 3; i++ {
		note := "salesnav-send-one stopped on email-required invite flow"
		run.Candidates = append(run.Candidates, CandidateEvent{
			At:         time.Now(),
			Source:     "ASAP - Agency Owners Delivery",
			Name:       "Email Required",
			ProfileURL: ptr("https://www.linkedin.com/sales/lead/skipped"),
			Status:     CandidateStatusSkipped,
			Note:       &note,
		})
	}
	plan := run.OperatorPlan()
	if plan.Action != "capture-source" || plan.Capture == nil {
		t.Fatalf("plan = %#v", plan)
	}
	if uintValue(plan.Remaining) != 7 || plan.Capture.Pages != 5 || plan.Capture.StopAfterConnectable != 14 || plan.Capture.Reason != "high-email-required" || plan.Capture.PlaywriterTimeoutMS != 90000 {
		t.Fatalf("capture = %#v remaining=%d", plan.Capture, uintValue(plan.Remaining))
	}
}

func TestLargeCapturePlanUsesExtendedPlaywriterTimeout(t *testing.T) {
	run := NewRunDefault(30, testDate(t, "2026-06-14"))
	capture := run.CaptureRecommendation("ASAP - Agency Owners Delivery", 9)
	if capture.Reason != "standard-buffer" || capture.Pages != 5 || capture.StopAfterConnectable != 12 || capture.PlaywriterTimeoutMS != 90000 {
		t.Fatalf("capture = %#v", capture)
	}
}

func TestSourceYieldMarksSaturatedCaptureAsLowYield(t *testing.T) {
	run := testRun(t, 22)
	run.CaptureCursors["ASAP - Agency Owners Delivery"] = SourceCaptureCursor{
		Source:              "ASAP - Agency Owners Delivery",
		UpdatedAt:           time.Now(),
		PageLabel:           ptr("Page 3 of 10"),
		CapturedPages:       2,
		RawRowCount:         50,
		OutputRowCount:      0,
		ConnectableCount:    0,
		AlreadyPendingCount: 50,
		StateCounts:         map[string]uint32{"already-pending": 50},
	}
	lowYield := LowYieldSourceNames(run, 50, 0.05)
	stats := SourceYieldReport(run)
	if len(lowYield) != 1 || lowYield[0] != "ASAP - Agency Owners Delivery" {
		t.Fatalf("low yield = %#v", lowYield)
	}
	if stats[0].ConnectableYield == nil || *stats[0].ConnectableYield != 0 || !strings.Contains(stats[0].Recommendation, "low-yield") {
		t.Fatalf("stats = %#v", stats[0])
	}
}

func TestImportCaptureUpdatesResumeCursorForPlan(t *testing.T) {
	run := testRun(t, 22)
	source := "ASAP - Agency Owners Delivery"
	capturedAt := "2026-06-06T12:00:00Z"
	resumeURL := "https://www.linkedin.com/sales/search/people?page=20"
	pageLabel := "Page 20 of 40"
	rawRows := uint32(25)
	outputRows := uint32(0)
	capture := SalesNavCapture{
		CapturedAt:     &capturedAt,
		Source:         &source,
		URL:            &resumeURL,
		ResumeURL:      &resumeURL,
		Page:           &SalesNavCapturePage{URL: &resumeURL, PageLabel: &pageLabel},
		StateCounts:    map[string]uint32{"already-pending": 25, "connectable": 0},
		RawRowCount:    &rawRows,
		OutputRowCount: &outputRows,
		Rows: []SalesNavCaptureRow{{
			Index:      0,
			Name:       ptr("Already Pending"),
			ProfileURL: ptr("https://www.linkedin.com/sales/lead/a"),
			MenuState:  ptr("already-pending"),
		}},
	}
	imported, err := ImportCapture(&run, capture, ImportCaptureOptions{OnlyConnectable: true})
	if err != nil {
		t.Fatal(err)
	}
	if imported != 0 {
		t.Fatalf("imported = %d", imported)
	}
	plan := run.OperatorPlan()
	if plan.Action != "capture-source" || valueOrEmpty(plan.Source) != source || plan.ResumeURL == nil || *plan.ResumeURL != resumeURL || plan.Cursor == nil {
		t.Fatalf("plan = %#v", plan)
	}
	if plan.Cursor.PageLabel == nil || *plan.Cursor.PageLabel != pageLabel || plan.Cursor.RawRowCount != 25 || plan.Cursor.ConnectableCount != 0 || plan.Cursor.AlreadyPendingCount != 25 {
		t.Fatalf("cursor = %#v", plan.Cursor)
	}
}

func TestNextCandidateIgnoresFilledSourceObservations(t *testing.T) {
	run := testRun(t, 22)
	for i := 0; i < 7; i++ {
		run.Candidates = append(run.Candidates, CandidateEvent{At: time.Now(), Source: "ASAP - Agency Owners Delivery", Name: "AI Founder", Status: CandidateStatusPending})
	}
	run.Observations = append(run.Observations,
		connectableObservation("ASAP - Agency Owners Delivery", "Stale AI Founder", "https://www.linkedin.com/sales/lead/stale"),
		connectableObservation("ASAP - Contract Recruiters Staffing", "Active Product Leader", "https://www.linkedin.com/sales/lead/active"),
	)
	if got := run.NextConnectableObservation().Name; got != "Active Product Leader" {
		t.Fatalf("next = %q", got)
	}
}

func TestDrainStaleCandidatesSkipsFilledSourceQueue(t *testing.T) {
	run := testRun(t, 22)
	for i := 0; i < 7; i++ {
		run.Candidates = append(run.Candidates, CandidateEvent{At: time.Now(), Source: "ASAP - Agency Owners Delivery", Name: "AI Founder", Status: CandidateStatusPending})
	}
	run.Observations = append(run.Observations, connectableObservation("ASAP - Agency Owners Delivery", "Stale AI Founder", "https://www.linkedin.com/sales/lead/stale"))
	drained, err := DrainStaleConnectableCandidates(&run, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(drained) != 1 || drained[0].Status != CandidateStatusSkipped || !run.HasCandidateEventForObservation(run.Observations[0]) {
		t.Fatalf("drained=%#v", drained)
	}
}

func TestSendResultMapsPendingVerifiedToPendingEvent(t *testing.T) {
	result := SalesNavSendResult{
		Candidate: SalesNavSendCandidate{Source: "ASAP - Startup CTO Eng Leaders", Name: "Verified Founder", ProfileURL: ptr("https://www.linkedin.com/sales/lead/x")},
		Status:    "pending-verified",
	}
	status, note := result.ToCandidateStatus()
	if status != CandidateStatusPending || !strings.Contains(note, "Connect - Pending") {
		t.Fatalf("status=%s note=%q", status, note)
	}
}

func TestTopUpResultDoesNotIncrementRowLevelVerifiedCount(t *testing.T) {
	run := testRun(t, 22)
	result := SalesNavSendResult{
		Candidate: SalesNavSendCandidate{Source: "ASAP - Vertical Proof Buyers", Name: "Top Up Founder", ProfileURL: ptr("https://www.linkedin.com/sales/lead/top-up")},
		Status:    "pending-verified",
	}
	event, err := RecordTopUpSendResult(&run, result, "/tmp/top-up-result.json", ptr("audit reconciliation"))
	if err != nil {
		t.Fatal(err)
	}
	if event.Status != CandidateStatusAuditTopUp || run.VerifiedCount() != 0 {
		t.Fatalf("event=%#v verified=%d", event, run.VerifiedCount())
	}
}

func TestAcceptanceLedgerSeedsPendingAndTopUpInvites(t *testing.T) {
	run := testRun(t, 22)
	run.Candidates = append(run.Candidates,
		CandidateEvent{At: time.Now(), Source: "ASAP - Startup CTO Eng Leaders", Name: "Verified Founder", ProfileURL: ptr("https://www.linkedin.com/sales/lead/verified?_ntb=abc"), Status: CandidateStatusPending},
		CandidateEvent{At: time.Now(), Source: "ASAP - Vertical Proof Buyers", Name: "Top Up Founder", ProfileURL: ptr("https://www.linkedin.com/sales/lead/top-up"), Status: CandidateStatusAuditTopUp},
		CandidateEvent{At: time.Now(), Source: "ASAP - Startup CTO Eng Leaders", Name: "Skipped Founder", ProfileURL: ptr("https://www.linkedin.com/sales/lead/skipped"), Status: CandidateStatusSkipped},
	)
	ledger := AcceptanceLedger{}
	seeded := ledger.UpsertFromRun(run)
	reseeded := ledger.UpsertFromRun(run)
	if seeded != 2 || reseeded != 0 || len(ledger.Invitations) != 2 || *ledger.Invitations[0].ProfileURL != "https://www.linkedin.com/sales/lead/verified?_ntb=abc" {
		t.Fatalf("seeded=%d reseeded=%d ledger=%#v", seeded, reseeded, ledger)
	}
}

func TestAcceptanceHistorySeedReadsControllerJSONL(t *testing.T) {
	dir := t.TempDir()
	store := &Store{Dir: dir}
	runID := uuid.New()
	sentAt := time.Now().Add(-8 * 24 * time.Hour)
	pendingEvent := CandidateEvent{
		At:         sentAt,
		Source:     "ASAP - Startup CTO Eng Leaders",
		Name:       "Historical Founder",
		ProfileURL: ptr("https://www.linkedin.com/sales/lead/historical?_ntb=abc"),
		Status:     CandidateStatusPending,
	}
	skippedEvent := CandidateEvent{
		At:         sentAt,
		Source:     "ASAP - Startup CTO Eng Leaders",
		Name:       "Skipped Founder",
		ProfileURL: ptr("https://www.linkedin.com/sales/lead/skipped"),
		Status:     CandidateStatusSkipped,
	}
	lines := []string{
		mustJSON(map[string]any{"at": sentAt, "run_id": runID, "kind": "start", "payload": map[string]any{"target": 25}}),
		mustJSON(map[string]any{"at": sentAt, "run_id": runID, "kind": "record-send-result", "payload": map[string]any{"event": pendingEvent}}),
		mustJSON(map[string]any{"at": sentAt, "run_id": runID, "kind": "record-send-result", "payload": map[string]any{"event": skippedEvent}}),
	}
	if err := os.WriteFile(filepath.Join(dir, runID.String()+".jsonl"), []byte(strings.Join(lines, "\n")), 0o644); err != nil {
		t.Fatal(err)
	}
	ledger := AcceptanceLedger{}
	summary, err := store.SeedAcceptanceFromHistory(&ledger)
	if err != nil {
		t.Fatal(err)
	}
	reseeded, err := store.SeedAcceptanceFromHistory(&ledger)
	if err != nil {
		t.Fatal(err)
	}
	if summary.RunLogs != 1 || summary.SentEvents != 1 || summary.Seeded != 1 || reseeded.Seeded != 0 || len(ledger.Invitations) != 1 {
		t.Fatalf("summary=%#v reseeded=%#v ledger=%#v", summary, reseeded, ledger)
	}
}

func TestAcceptanceImportUpdatesSourceReport(t *testing.T) {
	run := testRun(t, 22)
	run.Candidates = append(run.Candidates,
		CandidateEvent{At: time.Now().Add(-8 * 24 * time.Hour), Source: "ASAP - Startup CTO Eng Leaders", Name: "Accepted Founder", ProfileURL: ptr("https://www.linkedin.com/sales/lead/accepted?_ntb=abc"), Status: CandidateStatusPending},
		CandidateEvent{At: time.Now().Add(-8 * 24 * time.Hour), Source: "ASAP - Startup CTO Eng Leaders", Name: "Pending Founder", ProfileURL: ptr("https://www.linkedin.com/sales/lead/pending"), Status: CandidateStatusPending},
	)
	ledger := AcceptanceLedger{}
	ledger.UpsertFromRun(run)
	checkedAt := time.Now()
	summary := ledger.ImportOutcomes(AcceptanceOutcomeArtifact{Rows: []AcceptanceOutcomeRow{
		{Source: "ASAP - Startup CTO Eng Leaders", Name: "Accepted Founder", ProfileURL: ptr("https://www.linkedin.com/sales/lead/accepted"), Status: AcceptanceStatusAccepted, CheckedAt: &checkedAt, Relationship: ptr("1st")},
		{Source: "ASAP - Startup CTO Eng Leaders", Name: "Pending Founder", ProfileURL: ptr("https://www.linkedin.com/sales/lead/pending"), Status: AcceptanceStatusPending, CheckedAt: &checkedAt, Relationship: ptr("2nd")},
	}})
	report := ledger.Report(7, nil)
	if summary.Matched != 2 || report.TotalSent != 2 || report.Checked != 2 || report.Accepted != 1 || report.BySource["ASAP - Startup CTO Eng Leaders"].Pending != 1 {
		t.Fatalf("summary=%#v report=%#v", summary, report)
	}
}

func TestAcceptedFollowupCandidatesSkipAlreadyDraftedPeople(t *testing.T) {
	run := NewRunDefault(22, testDate(t, "2026-06-20"))
	run.Candidates = append(run.Candidates,
		CandidateEvent{At: time.Now().Add(-48 * time.Hour), Source: "ASAP - Agency Owners Delivery", Name: "Accepted Agency Owner", ProfileURL: ptr("https://www.linkedin.com/sales/lead/accepted?_ntb=abc"), Status: CandidateStatusPending},
		CandidateEvent{At: time.Now().Add(-48 * time.Hour), Source: "ASAP - Agency Owners Delivery", Name: "Still Pending", ProfileURL: ptr("https://www.linkedin.com/sales/lead/pending"), Status: CandidateStatusPending},
	)
	ledger := AcceptanceLedger{}
	ledger.UpsertFromRun(run)
	checkedAt := time.Now()
	ledger.ImportOutcomes(AcceptanceOutcomeArtifact{Rows: []AcceptanceOutcomeRow{
		{Source: "ASAP - Agency Owners Delivery", Name: "Accepted Agency Owner", ProfileURL: ptr("https://www.linkedin.com/sales/lead/accepted"), Status: AcceptanceStatusAccepted, CheckedAt: &checkedAt, Relationship: ptr("1st"), Note: ptr("lead page shows 1st-degree relationship")},
		{Source: "ASAP - Agency Owners Delivery", Name: "Still Pending", ProfileURL: ptr("https://www.linkedin.com/sales/lead/pending"), Status: AcceptanceStatusPending, CheckedAt: &checkedAt, Relationship: ptr("2nd")},
	}})
	candidates := ledger.AcceptedForFollowup(AcceptanceFollowupLedger{}, false)
	if len(candidates) != 1 || candidates[0].Name != "Accepted Agency Owner" {
		t.Fatalf("candidates=%#v", candidates)
	}
	report := BuildDraftReport(candidates, nil, DraftStrategyAsapContractV1, nil)
	followups := AcceptanceFollowupLedger{}
	followups.RecordReport(report, "/tmp/followups.md", nil)
	if got := ledger.AcceptedForFollowup(followups, false); len(got) != 0 {
		t.Fatalf("got drafted candidates %#v", got)
	}
	if got := ledger.AcceptedForFollowup(followups, true); len(got) != 1 {
		t.Fatalf("include drafted got %#v", got)
	}
}

func TestOperatorPlanPrefersReauditThenSendThenCapture(t *testing.T) {
	run := testRun(t, 22)
	if run.OperatorPlan().Action != "capture-source" {
		t.Fatalf("initial plan = %#v", run.OperatorPlan())
	}
	run.Observations = append(run.Observations, connectableObservation("ASAP - Agency Owners Delivery", "Connectable", "https://www.linkedin.com/sales/lead/c"))
	if run.OperatorPlan().Action != "send-candidate" {
		t.Fatalf("send plan = %#v", run.OperatorPlan())
	}
	run.State = RunStateNeedsReaudit
	if run.OperatorPlan().Action != "reaudit" {
		t.Fatalf("reaudit plan = %#v", run.OperatorPlan())
	}
}

func TestRunLevelRealSendCapCountsPendingEvents(t *testing.T) {
	run := NewRun(22, testDate(t, "2026-05-26"), 1)
	if run.RealSendCapacityRemaining() != 1 {
		t.Fatalf("capacity = %d", run.RealSendCapacityRemaining())
	}
	run.Candidates = append(run.Candidates, CandidateEvent{At: time.Now(), Source: "ASAP - Startup CTO Eng Leaders", Name: "A", Status: CandidateStatusPending})
	if run.RealSendCapacityRemaining() != 0 {
		t.Fatalf("capacity = %d", run.RealSendCapacityRemaining())
	}
}

func TestOperatorPlanBlocksWhenRealSendCapIsReached(t *testing.T) {
	run := NewRun(22, testDate(t, "2026-05-26"), 1)
	run.Candidates = append(run.Candidates, CandidateEvent{At: time.Now(), Source: "ASAP - Startup CTO Eng Leaders", Name: "Already Sent", Status: CandidateStatusPending})
	run.Observations = append(run.Observations, connectableObservation("ASAP - Agency Owners Delivery", "Connectable", "https://www.linkedin.com/sales/lead/c"))
	if run.OperatorPlan().Action != "blocked" {
		t.Fatalf("plan = %#v", run.OperatorPlan())
	}
}

func TestAuditImportAppliesPeopleCount(t *testing.T) {
	run := testRun(t, 22)
	ApplyAudit(&run, 933, ptr("start"))
	ApplyAudit(&run, 934, ptr("after"))
	if delta := run.AuditedDelta(); delta == nil || *delta != 1 {
		t.Fatalf("delta=%v", delta)
	}
}

func TestSendResultFailureMapsToFailedEvent(t *testing.T) {
	result := SalesNavSendResult{Candidate: SalesNavSendCandidate{Source: "ASAP - Startup CTO Eng Leaders", Name: "Unverified Founder"}, Status: "unverified:send-button-missing"}
	status, note := result.ToCandidateStatus()
	if status != CandidateStatusFailed || !strings.Contains(note, "unverified") {
		t.Fatalf("status=%s note=%q", status, note)
	}
}

func TestPendingAgeParserMarksMonthsAndYearsAsStale(t *testing.T) {
	cases := map[string]uint32{
		"Sent today":        0,
		"Sent 3 weeks ago":  0,
		"Sent 1 month ago":  1,
		"Sent 2 months ago": 2,
		"Sent 1 year ago":   12,
	}
	for input, want := range cases {
		got := ParseSentAgeMonths(input)
		if got == nil || *got != want {
			t.Fatalf("%q = %v, want %d", input, got, want)
		}
	}
}

func TestPendingCaptureImportExposesNextEligibleInvitation(t *testing.T) {
	run := NewPendingCleanupRun(75, 2, testDate(t, "2026-05-26"))
	capturedAt := "2026-05-26T12:00:00Z"
	one := uint32(1)
	three := uint32(3)
	capture := PendingCapture{
		CapturedAt: &capturedAt,
		Rows: []PendingCaptureRow{
			{Index: 0, Name: ptr("Fresh Invite"), AgeText: ptr("Sent 1 month ago"), AgeMonths: &one},
			{Index: 1, Name: ptr("Stale Invite"), ProfileURL: ptr("https://www.linkedin.com/in/stale"), AgeText: ptr("Sent 3 months ago"), AgeMonths: &three},
		},
	}
	imported, err := ImportPendingCapture(&run, capture)
	if err != nil {
		t.Fatal(err)
	}
	if imported != 2 || run.NextEligibleObservation().Name != "Stale Invite" {
		t.Fatalf("imported=%d next=%#v", imported, run.NextEligibleObservation())
	}
}

func TestPendingWithdrawResultCountsOnlyVerifiedWithdrawals(t *testing.T) {
	run := NewPendingCleanupRun(1, 2, testDate(t, "2026-05-26"))
	result := PendingWithdrawResult{Candidate: PendingWithdrawCandidate{Name: "Stale Invite", AgeText: "Sent 2 months ago"}, Status: "withdrawn-verified"}
	event, err := RecordPendingWithdrawResult(&run, result, "/tmp/withdraw-result.json")
	if err != nil {
		t.Fatal(err)
	}
	if event.Status != PendingWithdrawStatusWithdrawn || run.WithdrawnCount() != 1 || run.OperatorPlan().Action != "final-audit" {
		t.Fatalf("event=%#v run=%#v plan=%#v", event, run, run.OperatorPlan())
	}
}

func TestPlaywriterFallbackDetectsBunxExecutableNames(t *testing.T) {
	if !IsBunxPath("/Users/hanifcarroll/.bun/bin/bunx") || !IsBunxPath("bunx.exe") || IsBunxPath("/Users/hanifcarroll/.bun/bin/playwriter") {
		t.Fatal("bunx detection failed")
	}
}

func TestFollowupLedgerDedupesByNormalizedLinkedInURL(t *testing.T) {
	candidate := draftCandidate("ASAP - Contract Recruiters Staffing")
	ledger := AcceptanceFollowupLedger{}
	report := BuildDraftReport([]AcceptedDraftCandidate{candidate}, nil, DraftStrategyAsapContractV1, nil)
	inserted := ledger.RecordReport(report, "/tmp/report.md", nil)
	if inserted != 1 || !ledger.HasDraftFor(candidate) {
		t.Fatalf("inserted=%d ledger=%#v", inserted, ledger)
	}
}

func TestRecruiterSourceGetsContractAvailabilityMessage(t *testing.T) {
	report := BuildDraftReport([]AcceptedDraftCandidate{draftCandidate("ASAP - Contract Recruiters Staffing")}, nil, DraftStrategyAsapContractV1, nil)
	if !strings.Contains(report.Items[0].Draft, "contract roles") || !strings.Contains(report.Items[0].Draft, "HC Studio LLC") {
		t.Fatalf("draft=%q", report.Items[0].Draft)
	}
}

func TestResearchTitleAndCompanyAreUsedAsEvidence(t *testing.T) {
	artifact := AcceptedResearchArtifact{
		CapturedAt: ptr("2026-06-20T00:00:00Z"),
		Rows: []AcceptedResearchRow{{
			Source:     "ASAP - Startup CTO Eng Leaders",
			Name:       "Jamie Rivera",
			ProfileURL: ptr("https://www.linkedin.com/sales/lead/abc"),
			SalesNav:   &SalesNavResearch{Title: ptr("CTO"), Company: ptr("Acme AI")},
			Warnings:   []string{},
		}},
	}
	report := BuildDraftReport([]AcceptedDraftCandidate{draftCandidate("ASAP - Startup CTO Eng Leaders")}, &artifact, DraftStrategyAsapContractV1, nil)
	if !strings.Contains(report.Items[0].Angle, "Acme AI") {
		t.Fatalf("angle=%q", report.Items[0].Angle)
	}
	found := false
	for _, evidence := range report.Items[0].Evidence {
		if strings.Contains(evidence, "Sales Nav company: Acme AI") {
			found = true
		}
	}
	if !found {
		t.Fatalf("evidence=%#v", report.Items[0].Evidence)
	}
}

func draftCandidate(source string) AcceptedDraftCandidate {
	return AcceptedDraftCandidate{
		RunID:          uuid.New(),
		RunDate:        Date{Time: time.Date(2026, 6, 20, 0, 0, 0, 0, time.Local)},
		Source:         source,
		Name:           "Jamie Rivera",
		ProfileURL:     ptr("https://www.linkedin.com/sales/lead/abc?_ntb=x"),
		SentAt:         time.Now(),
		AcceptedAt:     time.Now(),
		Relationship:   ptr("1st"),
		AcceptanceNote: ptr("lead page shows 1st-degree relationship"),
	}
}

func mustJSON(value any) string {
	raw, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	return string(raw)
}
