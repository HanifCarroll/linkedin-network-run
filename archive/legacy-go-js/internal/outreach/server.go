package outreach

import (
	"context"
	"fmt"
	"html/template"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

type reviewServer struct {
	store    *Store
	template *template.Template
}

type reviewListPage struct {
	GeneratedAt  time.Time
	StatePath    string
	Status       string
	Bucket       string
	Query        string
	NameQuery    string
	TitleQuery   string
	CompanyQuery string
	Sort         string
	Dir          string
	Counts       map[string]int
	Leads        []Lead
}

type reviewDetailPage struct {
	GeneratedAt time.Time
	StatePath   string
	Lead        Lead
	Events      []RunEvent
	Subject     string
	Body        string
	BackURL     string
}

type reviewHomePage struct {
	GeneratedAt     time.Time
	StatePath       string
	DashboardPath   string
	Counts          map[string]int
	SentAgencies    int
	SentRecruiters  int
	ReadyAgencies   int
	ReadyRecruiters int
	AgencyAccounts  map[string]int
	RecentEvents    []RunEvent
	LastEvent       *RunEvent
}

type reviewAccountsPage struct {
	GeneratedAt time.Time
	StatePath   string
	Status      string
	Query       string
	Accounts    []AgencyAccount
	Counts      map[string]int
}

type reviewEventsPage struct {
	GeneratedAt time.Time
	StatePath   string
	Events      []RunEvent
}

func serveCommand(ctx context.Context, withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var addr string
	cmd := &cobra.Command{
		Use:   "serve",
		Short: "Run a local review UI for recruiter/agency outreach drafts",
		RunE: withStore(func(store *Store) error {
			server, err := newReviewServer(store)
			if err != nil {
				return err
			}
			httpServer := &http.Server{
				Addr:              addr,
				Handler:           server.routes(),
				ReadHeaderTimeout: 5 * time.Second,
			}
			errCh := make(chan error, 1)
			go func() {
				fmt.Printf("review_ui=http://%s\n", addr)
				errCh <- httpServer.ListenAndServe()
			}()
			select {
			case <-ctx.Done():
				shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				return httpServer.Shutdown(shutdownCtx)
			case err := <-errCh:
				if err == http.ErrServerClosed {
					return nil
				}
				return err
			}
		}),
	}
	cmd.Flags().StringVar(&addr, "addr", "127.0.0.1:8765", "local address to serve")
	return cmd
}

func newReviewServer(store *Store) (*reviewServer, error) {
	tmpl, err := template.New("review").Funcs(reviewTemplateFuncs()).Parse(reviewTemplateHTML)
	if err != nil {
		return nil, err
	}
	return &reviewServer{store: store, template: tmpl}, nil
}

func (s *reviewServer) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/", s.handleRoot)
	mux.HandleFunc("/leads", s.handleLeads)
	mux.HandleFunc("/leads/", s.handleLead)
	mux.HandleFunc("/accounts", s.handleAccounts)
	mux.HandleFunc("/events", s.handleEvents)
	return mux
}

func (s *reviewServer) handleRoot(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	state, err := s.store.Load()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	events := recentEvents(state.RunEvents, 12)
	page := reviewHomePage{
		GeneratedAt:     time.Now(),
		StatePath:       s.store.StatePath(),
		DashboardPath:   s.store.DefaultDailyDashboardPath(),
		Counts:          reviewMessageCounts(state),
		SentAgencies:    sentCount(state, "agency"),
		SentRecruiters:  sentCount(state, "recruiter"),
		ReadyAgencies:   readyCount(state, "agency"),
		ReadyRecruiters: readyCount(state, "recruiter"),
		AgencyAccounts:  reviewAgencyAccountCounts(state),
		RecentEvents:    events,
	}
	if len(events) > 0 {
		page.LastEvent = &events[0]
	}
	if err := s.template.ExecuteTemplate(w, "home", page); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func (s *reviewServer) handleLeads(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	state, err := s.store.Load()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	status := cleanText(r.URL.Query().Get("status"))
	if status == "" {
		status = string(MessageStatusDryRunReady)
	}
	bucket := cleanText(r.URL.Query().Get("bucket"))
	query := strings.ToLower(cleanText(r.URL.Query().Get("q")))
	nameQuery := strings.ToLower(cleanText(r.URL.Query().Get("name")))
	titleQuery := strings.ToLower(cleanText(r.URL.Query().Get("title")))
	companyQuery := strings.ToLower(cleanText(r.URL.Query().Get("company")))
	sortKey := cleanText(r.URL.Query().Get("sort"))
	if sortKey == "" {
		sortKey = "score"
	}
	dir := cleanText(r.URL.Query().Get("dir"))
	if dir != "asc" {
		dir = "desc"
	}
	page := reviewListPage{
		GeneratedAt:  time.Now(),
		StatePath:    s.store.StatePath(),
		Status:       status,
		Bucket:       bucket,
		Query:        r.URL.Query().Get("q"),
		NameQuery:    r.URL.Query().Get("name"),
		TitleQuery:   r.URL.Query().Get("title"),
		CompanyQuery: r.URL.Query().Get("company"),
		Sort:         sortKey,
		Dir:          dir,
		Counts:       reviewMessageCounts(state),
		Leads:        reviewLeads(state, MessageStatus(status), bucket, query, nameQuery, titleQuery, companyQuery, sortKey, dir),
	}
	if err := s.template.ExecuteTemplate(w, "list", page); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func (s *reviewServer) handleLead(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/leads/")
	parts := strings.Split(strings.Trim(rest, "/"), "/")
	if len(parts) == 0 || parts[0] == "" {
		http.NotFound(w, r)
		return
	}
	leadID := parts[0]
	if r.Method == http.MethodGet && len(parts) == 1 {
		s.handleLeadDetail(w, r, leadID)
		return
	}
	if r.Method == http.MethodPost && len(parts) == 2 {
		switch parts[1] {
		case "draft":
			s.handleDraftSave(w, r, leadID)
			return
		case "status":
			s.handleStatusSave(w, r, leadID)
			return
		}
	}
	http.NotFound(w, r)
}

func (s *reviewServer) handleLeadDetail(w http.ResponseWriter, r *http.Request, leadID string) {
	state, err := s.store.Load()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	index := findLeadByID(state.Leads, leadID)
	if index < 0 {
		http.NotFound(w, r)
		return
	}
	lead := state.Leads[index]
	subject := messageSubject(lead)
	body := ""
	if lead.Draft != nil {
		subject = draftSubject(lead)
		body = lead.Draft.Body
	}
	page := reviewDetailPage{
		GeneratedAt: time.Now(),
		StatePath:   s.store.StatePath(),
		Lead:        lead,
		Events:      eventsForLead(state.RunEvents, lead.ID),
		Subject:     subject,
		Body:        body,
		BackURL:     reviewBackURL(r),
	}
	if err := s.template.ExecuteTemplate(w, "detail", page); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func (s *reviewServer) handleAccounts(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	state, err := s.store.Load()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	status := cleanText(r.URL.Query().Get("status"))
	query := strings.ToLower(cleanText(r.URL.Query().Get("q")))
	accounts := []AgencyAccount{}
	for _, account := range state.AgencyAccounts {
		if status != "" && string(account.Status) != status {
			continue
		}
		if query != "" && !strings.Contains(strings.ToLower(strings.Join([]string{account.Name, pointerValue(account.Industry), pointerValue(account.Headcount), pointerValue(account.Location), strings.Join(account.FitReasons, " ")}, " ")), query) {
			continue
		}
		accounts = append(accounts, account)
	}
	sort.SliceStable(accounts, func(i, j int) bool {
		if accounts[i].Status == accounts[j].Status {
			return accounts[i].FitScore > accounts[j].FitScore
		}
		return accounts[i].Status < accounts[j].Status
	})
	page := reviewAccountsPage{
		GeneratedAt: time.Now(),
		StatePath:   s.store.StatePath(),
		Status:      status,
		Query:       r.URL.Query().Get("q"),
		Accounts:    accounts,
		Counts:      reviewAgencyAccountCounts(state),
	}
	if err := s.template.ExecuteTemplate(w, "accounts", page); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func (s *reviewServer) handleEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	state, err := s.store.Load()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	page := reviewEventsPage{
		GeneratedAt: time.Now(),
		StatePath:   s.store.StatePath(),
		Events:      recentEvents(state.RunEvents, 120),
	}
	if err := s.template.ExecuteTemplate(w, "events", page); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func (s *reviewServer) handleDraftSave(w http.ResponseWriter, r *http.Request, leadID string) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	subject := strings.TrimSpace(r.FormValue("subject"))
	body := strings.TrimSpace(strings.ReplaceAll(r.FormValue("body"), "\r\n", "\n"))
	if subject == "" || body == "" {
		http.Error(w, "subject and body are required", http.StatusBadRequest)
		return
	}
	state, err := s.store.Load()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	index := findLeadByID(state.Leads, leadID)
	if index < 0 {
		http.NotFound(w, r)
		return
	}
	lead := &state.Leads[index]
	angle := ""
	if lead.Draft != nil {
		angle = lead.Draft.Angle
	}
	if angle == "" {
		angle = draftAngle(*lead)
	}
	lead.Draft = &MessageDraft{
		Subject:     subject,
		Body:        body,
		Angle:       angle,
		Evidence:    draftEvidence(*lead),
		GeneratedAt: time.Now(),
	}
	lead.MessageStatus = MessageStatusDrafted
	lead.UpdatedAt = time.Now()
	if err := s.store.Save(state); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	http.Redirect(w, r, "/leads/"+leadID, http.StatusSeeOther)
}

func (s *reviewServer) handleStatusSave(w http.ResponseWriter, r *http.Request, leadID string) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	status, err := parseMessageStatus(r.FormValue("status"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	switch status {
	case MessageStatusApproved, MessageStatusNeedsEdit, MessageStatusDrafted:
	default:
		http.Error(w, "review UI can only set drafted, needs_edit, or approved", http.StatusBadRequest)
		return
	}
	state, err := s.store.Load()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	index := findLeadByID(state.Leads, leadID)
	if index < 0 {
		http.NotFound(w, r)
		return
	}
	if status == MessageStatusApproved && state.Leads[index].Draft == nil {
		http.Error(w, "cannot approve a lead without a draft", http.StatusBadRequest)
		return
	}
	state.Leads[index].MessageStatus = status
	state.Leads[index].UpdatedAt = time.Now()
	if err := s.store.Save(state); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	http.Redirect(w, r, "/leads/"+leadID, http.StatusSeeOther)
}

func reviewMessageCounts(state OutreachState) map[string]int {
	counts := map[string]int{}
	for _, lead := range state.Leads {
		if !leadMatchesSendableBucket(state, lead, bucketForLead(lead)) {
			continue
		}
		counts[string(lead.MessageStatus)]++
	}
	return counts
}

func reviewLeads(state OutreachState, status MessageStatus, bucket string, query string, nameQuery string, titleQuery string, companyQuery string, sortKey string, dir string) []Lead {
	leads := []Lead{}
	for _, lead := range state.Leads {
		leadBucket := bucketForLead(lead)
		if !leadMatchesSendableBucket(state, lead, leadBucket) {
			continue
		}
		if status != "" && lead.MessageStatus != status {
			continue
		}
		if bucket != "" && leadBucket != bucket {
			continue
		}
		if query != "" && !reviewLeadMatches(lead, query) {
			continue
		}
		if nameQuery != "" && !strings.Contains(strings.ToLower(lead.Name), nameQuery) {
			continue
		}
		if titleQuery != "" && !strings.Contains(strings.ToLower(pointerValue(lead.Title)), titleQuery) {
			continue
		}
		if companyQuery != "" && !strings.Contains(strings.ToLower(pointerValue(lead.Company)+" "+pointerValue(lead.AgencyAccountName)), companyQuery) {
			continue
		}
		leads = append(leads, lead)
	}
	sort.SliceStable(leads, func(i, j int) bool {
		cmp := compareReviewLeads(leads[i], leads[j], sortKey)
		if cmp == 0 {
			cmp = strings.Compare(strings.ToLower(leads[i].Name), strings.ToLower(leads[j].Name))
		}
		if dir == "asc" {
			return cmp < 0
		}
		return cmp > 0
	})
	return leads
}

func reviewAgencyAccountCounts(state OutreachState) map[string]int {
	counts := map[string]int{}
	for _, account := range state.AgencyAccounts {
		counts[string(account.Status)]++
	}
	return counts
}

func recentEvents(events []RunEvent, limit int) []RunEvent {
	out := append([]RunEvent(nil), events...)
	sort.SliceStable(out, func(i, j int) bool {
		return out[i].At.After(out[j].At)
	})
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out
}

func eventsForLead(events []RunEvent, leadID string) []RunEvent {
	matches := []RunEvent{}
	for _, event := range recentEvents(events, 0) {
		if event.LeadID == leadID {
			matches = append(matches, event)
		}
	}
	return matches
}

func compareReviewLeads(left Lead, right Lead, sortKey string) int {
	switch sortKey {
	case "name":
		return strings.Compare(strings.ToLower(left.Name), strings.ToLower(right.Name))
	case "bucket":
		return strings.Compare(bucketForLead(left), bucketForLead(right))
	case "status":
		return strings.Compare(statusLabel(left.MessageStatus), statusLabel(right.MessageStatus))
	case "title":
		return strings.Compare(strings.ToLower(pointerValue(left.Title)), strings.ToLower(pointerValue(right.Title)))
	case "company":
		return strings.Compare(strings.ToLower(pointerValue(left.Company)), strings.ToLower(pointerValue(right.Company)))
	case "sent":
		return compareTimes(lastSentAt(left), lastSentAt(right))
	case "score":
		fallthrough
	default:
		if left.FitScore < right.FitScore {
			return -1
		}
		if left.FitScore > right.FitScore {
			return 1
		}
		return 0
	}
}

func compareTimes(left *time.Time, right *time.Time) int {
	if left == nil && right == nil {
		return 0
	}
	if left == nil {
		return -1
	}
	if right == nil {
		return 1
	}
	if left.Before(*right) {
		return -1
	}
	if left.After(*right) {
		return 1
	}
	return 0
}

func reviewLeadMatches(lead Lead, query string) bool {
	haystack := strings.ToLower(strings.Join([]string{
		lead.Name,
		pointerValue(lead.Title),
		pointerValue(lead.Company),
		pointerValue(lead.AgencyAccountName),
		string(lead.LeadType),
		string(lead.MessageStatus),
	}, " "))
	return strings.Contains(haystack, query)
}

func reviewBackURL(r *http.Request) string {
	if value := r.URL.Query().Get("back"); strings.TrimSpace(value) != "" && strings.HasPrefix(value, "/leads") {
		return value
	}
	return "/leads?status=dry_run_ready"
}

func statusLabel(status MessageStatus) string {
	switch status {
	case MessageStatusNone:
		return "Not drafted"
	case MessageStatusDrafted:
		return "Draft"
	case MessageStatusNeedsEdit:
		return "Needs edit"
	case MessageStatusApproved:
		return "Approved"
	case MessageStatusDryRunReady:
		return "Ready to send"
	case MessageStatusSent:
		return "Sent"
	case MessageStatusManuallySent:
		return "Manually sent"
	case MessageStatusNotMessageable:
		return "Not messageable"
	case MessageStatusConversationExists:
		return "Conversation exists"
	case MessageStatusSendFailed:
		return "Send failed"
	case MessageStatusBlocked:
		return "Blocked"
	case MessageStatusReplied:
		return "Replied"
	case MessageStatusRepliedNotFit:
		return "Replied, not fit"
	case MessageStatusRepliedFuture:
		return "Replied, future"
	case MessageStatusRepliedUnknown:
		return "Replied, unknown"
	default:
		return titleLabel(strings.ReplaceAll(string(status), "_", " "))
	}
}

func statusClass(status MessageStatus) string {
	switch status {
	case MessageStatusDryRunReady:
		return "ready"
	case MessageStatusSent, MessageStatusManuallySent:
		return "sent"
	case MessageStatusDrafted:
		return "draft"
	case MessageStatusNeedsEdit, MessageStatusSendFailed, MessageStatusBlocked:
		return "attention"
	case MessageStatusConversationExists, MessageStatusNotMessageable:
		return "muted"
	default:
		return "neutral"
	}
}

func accountStatusLabel(status AgencyAccountStatus) string {
	switch status {
	case AgencyAccountStatusQualified:
		return "Qualified"
	case AgencyAccountStatusNeedsReview:
		return "Needs review"
	case AgencyAccountStatusRejected:
		return "Rejected"
	case AgencyAccountStatusExhausted:
		return "Exhausted"
	default:
		return titleLabel(strings.ReplaceAll(string(status), "_", " "))
	}
}

func accountStatusClass(status AgencyAccountStatus) string {
	switch status {
	case AgencyAccountStatusQualified:
		return "sent"
	case AgencyAccountStatusNeedsReview:
		return "attention"
	case AgencyAccountStatusExhausted, AgencyAccountStatusRejected:
		return "muted"
	default:
		return "neutral"
	}
}

func attemptLabel(status string) string {
	switch status {
	case "dry-run-messageable":
		return "Ready"
	case "sent-clicked":
		return "Sent"
	case "conversation-exists":
		return "Existing conversation"
	case "not-messageable":
		return "No message option"
	case "send-button-missing":
		return "Send button missing"
	case "composer-missing":
		return "Composer missing"
	case "blocked":
		return "Blocked"
	default:
		return titleLabel(strings.ReplaceAll(status, "-", " "))
	}
}

func attemptClass(status string) string {
	switch status {
	case "dry-run-messageable":
		return "ready"
	case "sent-clicked":
		return "sent"
	case "conversation-exists", "not-messageable":
		return "muted"
	case "send-button-missing", "composer-missing", "blocked":
		return "failed"
	default:
		return "neutral"
	}
}

func lastSentAt(lead Lead) *time.Time {
	for i := len(lead.SendAttempts) - 1; i >= 0; i-- {
		if lead.SendAttempts[i].Status == "sent-clicked" {
			return &lead.SendAttempts[i].At
		}
	}
	return nil
}

func relativeTime(value *time.Time, now time.Time) string {
	if value == nil {
		return ""
	}
	delta := now.Sub(*value)
	if delta < time.Minute {
		return "just now"
	}
	if delta < time.Hour {
		return fmt.Sprintf("%dm ago", int(delta.Minutes()))
	}
	if delta < 24*time.Hour {
		return fmt.Sprintf("%dh ago", int(delta.Hours()))
	}
	return fmt.Sprintf("%dd ago", int(delta.Hours()/24))
}

const reviewTemplateHTML = `
{{define "home"}}
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Outreach Command Center</title>
<style>{{template "style"}}</style>
</head>
<body>
<header>
<h1>Outreach Command Center</h1>
<div class="meta">Daily recruiter and agency outreach · {{.StatePath}} · Updated {{.GeneratedAt.Format "2006-01-02 15:04:05"}}</div>
</header>
<main>
{{template "nav" .}}
<section class="cards">
<a class="card" href="/leads?status=sent&bucket=agency&sort=sent&dir=desc"><span>Agencies sent</span><strong>{{.SentAgencies}}</strong></a>
<a class="card" href="/leads?status=sent&bucket=recruiter&sort=sent&dir=desc"><span>Recruiters sent</span><strong>{{.SentRecruiters}}</strong></a>
<a class="card" href="/leads?status=dry_run_ready&bucket=agency"><span>Agencies ready</span><strong>{{.ReadyAgencies}}</strong></a>
<a class="card" href="/leads?status=dry_run_ready&bucket=recruiter"><span>Recruiters ready</span><strong>{{.ReadyRecruiters}}</strong></a>
</section>
<section class="panel">
<h2>Run State</h2>
<div class="grid">
<div><span class="label">Dashboard</span><a href="/events">{{.DashboardPath}}</a></div>
<div><span class="label">Last event</span>{{if .LastEvent}}{{.LastEvent.Phase}} · {{.LastEvent.Result}} · {{rel .LastEvent.At $.GeneratedAt}}{{else}}No run events recorded{{end}}</div>
<div><span class="label">Safety</span>Dry-run and allow-send are CLI-controlled. Connection requests are not part of this workflow.</div>
<div><span class="label">Agency accounts</span>Qualified {{index .AgencyAccounts "qualified"}} · Needs review {{index .AgencyAccounts "needs_review"}} · Exhausted {{index .AgencyAccounts "exhausted"}}</div>
</div>
</section>
<section class="panel">
<h2>Saved Views</h2>
<div class="saved">
<a href="/leads?status=dry_run_ready">Today: Ready</a>
<a href="/leads?status=sent&sort=sent&dir=desc">Today: Sent</a>
<a href="/accounts?status=qualified">Qualified agency accounts</a>
<a href="/leads?status=send_failed">Failed sends</a>
<a href="/leads?status=conversation_exists">Existing conversations</a>
<a href="/events">Run timeline</a>
</div>
</section>
<section class="panel">
<h2>Recent Timeline</h2>
{{template "eventsTable" .RecentEvents}}
</section>
</main>
</body>
</html>
{{end}}

{{define "list"}}
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Outreach Command Center</title>
<style>{{template "style"}}</style>
</head>
<body>
<header>
<h1>Outreach Command Center</h1>
<div class="meta">Recruiter and agency outreach queue · {{.StatePath}} · Updated {{.GeneratedAt.Format "2006-01-02 15:04:05"}}</div>
</header>
<main>
{{template "nav" .}}
<nav class="tabs">
<a class="{{if eq .Status "dry_run_ready"}}active{{end}}" href="{{statusURL "dry_run_ready" .Bucket .Query}}">Ready to send {{index .Counts "dry_run_ready"}}</a>
<a class="{{if eq .Status "drafted"}}active{{end}}" href="{{statusURL "drafted" .Bucket .Query}}">Draft {{index .Counts "drafted"}}</a>
<a class="{{if eq .Status "sent"}}active{{end}}" href="{{statusURL "sent" .Bucket .Query}}">Sent {{index .Counts "sent"}}</a>
<a class="{{if eq .Status "conversation_exists"}}active{{end}}" href="{{statusURL "conversation_exists" .Bucket .Query}}">Conversation exists {{index .Counts "conversation_exists"}}</a>
<a class="{{if eq .Status "needs_edit"}}active{{end}}" href="{{statusURL "needs_edit" .Bucket .Query}}">Needs edit {{index .Counts "needs_edit"}}</a>
<a class="{{if eq .Status "send_failed"}}active{{end}}" href="{{statusURL "send_failed" .Bucket .Query}}">Send failed {{index .Counts "send_failed"}}</a>
</nav>
<form class="filters" method="get" action="/leads">
<input type="hidden" name="status" value="{{.Status}}">
<input type="hidden" name="sort" value="{{.Sort}}">
<input type="hidden" name="dir" value="{{.Dir}}">
<select name="bucket">
<option value="">All buckets</option>
<option value="agency" {{if eq .Bucket "agency"}}selected{{end}}>Agencies</option>
<option value="recruiter" {{if eq .Bucket "recruiter"}}selected{{end}}>Recruiters</option>
</select>
<input name="q" value="{{.Query}}" placeholder="Search name, title, company">
<input name="name" value="{{.NameQuery}}" placeholder="Name">
<input name="title" value="{{.TitleQuery}}" placeholder="Title">
<input name="company" value="{{.CompanyQuery}}" placeholder="Company">
<button type="submit">Filter</button>
</form>
{{if .Leads}}
<div class="table-wrap"><table>
<thead><tr>
<th><a href="{{sortURL .Status .Bucket .Query .Sort .Dir "name"}}">Name{{sortMark .Sort .Dir "name"}}</a></th>
<th><a href="{{sortURL .Status .Bucket .Query .Sort .Dir "bucket"}}">Lane{{sortMark .Sort .Dir "bucket"}}</a></th>
<th><a href="{{sortURL .Status .Bucket .Query .Sort .Dir "status"}}">Status{{sortMark .Sort .Dir "status"}}</a></th>
<th><a href="{{sortURL .Status .Bucket .Query .Sort .Dir "title"}}">Title{{sortMark .Sort .Dir "title"}}</a></th>
<th><a href="{{sortURL .Status .Bucket .Query .Sort .Dir "company"}}">Company{{sortMark .Sort .Dir "company"}}</a></th>
<th><a href="{{sortURL .Status .Bucket .Query .Sort .Dir "score"}}">Score{{sortMark .Sort .Dir "score"}}</a></th>
<th><a href="{{sortURL .Status .Bucket .Query .Sort .Dir "sent"}}">Sent{{sortMark .Sort .Dir "sent"}}</a></th>
<th></th></tr></thead>
<tbody>
{{range .Leads}}
<tr>
<td><strong>{{.Name}}</strong></td>
<td>{{bucket .}}</td>
<td><span class="badge {{statusClass .MessageStatus}}">{{statusLabel .MessageStatus}}</span></td>
<td class="title">{{ptr .Title}}</td>
<td class="company">{{ptr .Company}}</td>
<td>{{.FitScore}}</td>
<td class="age" title="{{sentAt .}}">{{sentAgo . $.GeneratedAt}}</td>
<td><a href="/leads/{{.ID}}?back={{backURL $.Status $.Bucket $.Query}}">Review</a></td>
</tr>
{{end}}
</tbody>
</table></div>
{{else}}
<div class="empty">No recruiter or agency leads match this filter.</div>
{{end}}
</main>
</body>
</html>
{{end}}

{{define "accounts"}}
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agency Accounts · Outreach Command Center</title>
<style>{{template "style"}}</style>
</head>
<body>
<header><h1>Agency Accounts</h1><div class="meta">{{.StatePath}} · Updated {{.GeneratedAt.Format "2006-01-02 15:04:05"}}</div></header>
<main>
{{template "nav" .}}
<nav class="tabs">
<a class="{{if eq .Status ""}}active{{end}}" href="/accounts">All {{len .Accounts}}</a>
<a class="{{if eq .Status "qualified"}}active{{end}}" href="/accounts?status=qualified">Qualified {{index .Counts "qualified"}}</a>
<a class="{{if eq .Status "needs_review"}}active{{end}}" href="/accounts?status=needs_review">Needs review {{index .Counts "needs_review"}}</a>
<a class="{{if eq .Status "exhausted"}}active{{end}}" href="/accounts?status=exhausted">Exhausted {{index .Counts "exhausted"}}</a>
</nav>
<form class="filters" method="get" action="/accounts">
<input type="hidden" name="status" value="{{.Status}}">
<input name="q" value="{{.Query}}" placeholder="Search account, industry, reason">
<button type="submit">Filter</button>
</form>
<div class="table-wrap"><table>
<thead><tr><th>Account</th><th>Status</th><th>Score</th><th>Headcount</th><th>Industry</th><th>Contacts checked</th><th>Reasons</th></tr></thead>
<tbody>{{range .Accounts}}<tr>
<td><strong>{{.Name}}</strong>{{if .AccountURL}}<br><a href="{{ptr .AccountURL}}" target="_blank" rel="noreferrer">Sales Nav</a>{{end}}</td>
<td><span class="badge {{accountStatusClass .Status}}">{{accountStatusLabel .Status}}</span></td>
<td>{{.FitScore}}</td><td>{{ptr .Headcount}}</td><td class="company">{{ptr .Industry}}</td><td>{{.ContactCaptureCount}}</td><td class="company">{{join .FitReasons}}</td>
</tr>{{end}}</tbody>
</table></div>
</main>
</body>
</html>
{{end}}

{{define "events"}}
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Run Timeline · Outreach Command Center</title>
<style>{{template "style"}}</style>
</head>
<body>
<header><h1>Run Timeline</h1><div class="meta">{{.StatePath}} · Updated {{.GeneratedAt.Format "2006-01-02 15:04:05"}}</div></header>
<main>{{template "nav" .}}{{template "eventsTable" .Events}}</main>
</body>
</html>
{{end}}

{{define "detail"}}
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{.Lead.Name}} · Outreach Review</title>
<style>{{template "style"}}</style>
</head>
<body>
<header>
<a href="{{.BackURL}}">Back to list</a>
<h1>{{.Lead.Name}}</h1>
<div class="meta">State: {{.StatePath}} · Status: {{statusLabel .Lead.MessageStatus}} · Generated: {{.GeneratedAt.Format "2006-01-02 15:04:05"}}</div>
</header>
<main>
{{template "nav" .}}
<section class="summary">
<div class="field">Bucket<br><strong>{{bucket .Lead}}</strong></div>
<div class="field">Lead type<br><strong>{{.Lead.LeadType}}</strong></div>
<div class="field">Title<br><strong>{{ptr .Lead.Title}}</strong></div>
<div class="field">Company<br><strong>{{ptr .Lead.Company}}</strong></div>
{{if .Lead.ProfileURL}}<div class="field">Profile<br><a href="{{ptr .Lead.ProfileURL}}" target="_blank" rel="noreferrer">{{ptr .Lead.ProfileURL}}</a></div>{{end}}
{{if .Lead.AgencyAccountName}}<div class="field">Agency account<br><strong>{{ptr .Lead.AgencyAccountName}}</strong></div>{{end}}
</section>
<form method="post" action="/leads/{{.Lead.ID}}/draft">
<label for="subject">Subject</label>
<input id="subject" name="subject" value="{{.Subject}}">
<label for="body">Body</label>
<textarea id="body" name="body">{{.Body}}</textarea>
<div class="actions"><button type="submit">Save Draft</button></div>
</form>
<form class="actions" method="post" action="/leads/{{.Lead.ID}}/status">
<button class="approve" name="status" value="approved" type="submit">Approve</button>
<button class="warn" name="status" value="needs_edit" type="submit">Needs Edit</button>
<button class="secondary" name="status" value="drafted" type="submit">Mark Drafted</button>
</form>
<section class="panel">
<h2>Send History</h2>
{{if .Lead.SendAttempts}}
<div class="table-wrap"><table><thead><tr><th>Time</th><th>Status</th><th>Mode</th><th>Diagnostics</th><th>Artifact</th></tr></thead><tbody>
{{range .Lead.SendAttempts}}<tr><td>{{time .At}}</td><td><span class="badge {{attemptClass .Status}}">{{attemptLabel .Status}}</span></td><td>{{if .DryRun}}Dry run{{else}}Live{{end}}</td><td class="company">{{diag .Diagnostics}}</td><td>{{.OutPath}}</td></tr>{{end}}
</tbody></table></div>
{{else}}<div class="empty">No send attempts recorded.</div>{{end}}
</section>
<section class="panel">
<h2>Lead Timeline</h2>
{{template "eventsTable" .Events}}
</section>
</main>
</body>
</html>
{{end}}

{{define "eventsTable"}}
{{if .}}
<div class="table-wrap"><table><thead><tr><th>When</th><th>Phase</th><th>Lane</th><th>Name</th><th>Result</th><th>Note</th><th>Artifact</th></tr></thead><tbody>
{{range .}}<tr><td class="age">{{time .At}}</td><td>{{.Phase}}</td><td>{{.Bucket}}</td><td>{{.Name}}</td><td>{{.Result}}</td><td class="company">{{.Note}}</td><td class="company">{{.OutPath}}</td></tr>{{end}}
</tbody></table></div>
{{else}}<div class="empty">No run events recorded yet.</div>{{end}}
{{end}}

{{define "nav"}}
<nav class="topnav">
<a href="/">Overview</a>
<a href="/leads?status=dry_run_ready">Leads</a>
<a href="/accounts">Agency accounts</a>
<a href="/events">Timeline</a>
</nav>
{{end}}

{{define "style"}}
:root{--bg:#f5f5f7;--panel:#fff;--text:#1d1d1f;--muted:#6e6e73;--line:#d2d2d7;--soft:#e8e8ed;--blue:#0066cc;--green:#1f7a4d;--orange:#9a5b00;--red:#b42318}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",sans-serif;margin:0;color:var(--text);background:var(--bg)}
a{color:var(--blue);text-decoration:none}
header{background:rgba(255,255,255,.86);border-bottom:1px solid var(--line);padding:22px 32px;backdrop-filter:saturate(180%) blur(18px);position:sticky;top:0;z-index:2}
main{padding:24px 32px;max-width:1440px}
h1{font-size:34px;letter-spacing:0;line-height:1.08;margin:4px 0 8px;font-weight:720}
h2{font-size:18px;margin:0 0 14px}
.meta{color:var(--muted);font-size:13px;line-height:1.45;overflow-wrap:anywhere}
.topnav,.tabs,.saved{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 18px}
.topnav a,.tabs a,.saved a{border:1px solid var(--line);background:rgba(255,255,255,.72);border-radius:999px;padding:7px 12px;font-size:13px;color:var(--text)}
.tabs a.active{background:var(--text);border-color:var(--text);color:#fff}
.filters{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;align-items:center}
input,textarea,select{font:inherit;border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#fff;color:var(--text)}
input[name=q]{min-width:280px}
button{font:inherit;border:1px solid var(--blue);background:var(--blue);color:#fff;border-radius:10px;padding:9px 14px;cursor:pointer}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:18px}
.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;color:var(--text)}
.card span,.label{display:block;color:var(--muted);font-size:12px;margin-bottom:8px}.card strong{font-size:34px;line-height:1}
.panel{margin-bottom:18px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;font-size:14px}.grid div{overflow-wrap:anywhere}
.table-wrap{overflow-x:auto;border:1px solid var(--line);background:var(--panel);border-radius:14px}
table{width:100%;border-collapse:separate;border-spacing:0;background:var(--panel)}
th,td{padding:11px 14px;border-bottom:1px solid var(--soft);text-align:left;vertical-align:middle;font-size:14px;white-space:nowrap}
td.title,td.company{white-space:normal;min-width:220px;overflow-wrap:anywhere}
tr:last-child td{border-bottom:0}
th{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;background:#fbfbfd;font-weight:650}
th a{color:var(--muted)}
.badge{display:inline-flex;align-items:center;border-radius:999px;padding:3px 8px;font-size:12px;font-weight:600;border:1px solid var(--line);background:#f5f5f7;color:var(--muted)}
.badge.ready{background:#eef5ff;color:#064f9e;border-color:#c8ddff}.badge.sent{background:#edf8f2;color:var(--green);border-color:#c7ead8}.badge.draft{background:#f7f7f8;color:#515154}.badge.attention{background:#fff7ed;color:var(--orange);border-color:#fed7aa}.badge.muted{background:#f2f2f2;color:#6e6e73}.badge.failed{background:#fff1f0;color:var(--red);border-color:#fecdca}
.empty{background:#fff;border:1px solid var(--line);border-radius:14px;padding:24px;color:var(--muted)}
.age{color:var(--muted);font-size:13px}
.summary{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-bottom:18px}
.field{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px;min-width:0;overflow-wrap:anywhere;color:var(--muted);font-size:13px}
label{display:block;font-weight:600;margin:14px 0 6px}
textarea{width:100%;min-height:360px;line-height:1.45;resize:vertical}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.secondary{background:#fff;color:#1d2733;border-color:var(--line)}.warn{background:#fff7ed;color:#9a3412;border-color:#fdba74}.approve{background:#137333;border-color:#137333}
@media (max-width:900px){.cards,.grid,.summary{grid-template-columns:1fr 1fr}}
@media (max-width:720px){header,main{padding:16px}h1{font-size:28px}.cards,.grid,.summary{grid-template-columns:1fr}.filters{display:grid}.filters input,.filters select,.filters button{width:100%;min-width:0}th,td{font-size:13px;padding:9px 10px}.actions{display:grid}.actions button{width:100%}textarea{min-height:300px}}
@media (max-width:420px){h1{font-size:24px}.meta,.field{font-size:12px}input,textarea,button{font-size:16px}}
{{end}}
`

func reviewTemplateFuncs() template.FuncMap {
	return template.FuncMap{
		"ptr": func(value *string) string {
			if value == nil {
				return ""
			}
			return *value
		},
		"bucket": func(lead Lead) string {
			return bucketForLead(lead)
		},
		"statusLabel": func(status MessageStatus) string {
			return statusLabel(status)
		},
		"statusClass": func(status MessageStatus) string {
			return statusClass(status)
		},
		"sentAgo": func(lead Lead, now time.Time) string {
			return relativeTime(lastSentAt(lead), now)
		},
		"sentAt": func(lead Lead) string {
			value := lastSentAt(lead)
			if value == nil {
				return ""
			}
			return value.Format("2006-01-02 15:04")
		},
		"time": func(value time.Time) string {
			if value.IsZero() {
				return ""
			}
			return value.Format("2006-01-02 15:04")
		},
		"rel": func(value time.Time, now time.Time) string {
			if value.IsZero() {
				return ""
			}
			return relativeTime(&value, now)
		},
		"join": func(values []string) string {
			return strings.Join(values, "; ")
		},
		"diag": func(values map[string]string) string {
			if len(values) == 0 {
				return ""
			}
			keys := make([]string, 0, len(values))
			for key := range values {
				keys = append(keys, key)
			}
			sort.Strings(keys)
			parts := make([]string, 0, len(keys))
			for _, key := range keys {
				parts = append(parts, key+": "+values[key])
			}
			return strings.Join(parts, "; ")
		},
		"attemptLabel": func(status string) string {
			return attemptLabel(status)
		},
		"attemptClass": func(status string) string {
			return attemptClass(status)
		},
		"accountStatusLabel": func(status AgencyAccountStatus) string {
			return accountStatusLabel(status)
		},
		"accountStatusClass": func(status AgencyAccountStatus) string {
			return accountStatusClass(status)
		},
		"sortURL": func(status string, bucket string, query string, currentSort string, currentDir string, column string) string {
			dir := "asc"
			if currentSort == column && currentDir == "asc" {
				dir = "desc"
			}
			parts := reviewQueryParts(status, bucket, query)
			parts = append(parts, "sort="+urlQueryEscape(column), "dir="+urlQueryEscape(dir))
			return "/leads?" + strings.Join(parts, "&")
		},
		"sortMark": func(currentSort string, currentDir string, column string) string {
			if currentSort != column {
				return ""
			}
			if currentDir == "asc" {
				return " ↑"
			}
			return " ↓"
		},
		"statusURL": func(status string, bucket string, query string) string {
			return "/leads?" + strings.Join(reviewQueryParts(status, bucket, query), "&")
		},
		"backURL": func(status string, bucket string, query string) string {
			return url.QueryEscape("/leads?" + strings.Join(reviewQueryParts(status, bucket, query), "&"))
		},
	}
}

func reviewQueryParts(status string, bucket string, query string) []string {
	parts := []string{}
	if status != "" {
		parts = append(parts, "status="+urlQueryEscape(status))
	}
	if bucket != "" {
		parts = append(parts, "bucket="+urlQueryEscape(bucket))
	}
	if query != "" {
		parts = append(parts, "q="+urlQueryEscape(query))
	}
	return parts
}

func urlQueryEscape(value string) string {
	return strings.ReplaceAll(url.QueryEscape(value), "+", "%20")
}
