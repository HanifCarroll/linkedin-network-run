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
	GeneratedAt time.Time
	StatePath   string
	Status      string
	Bucket      string
	Query       string
	Counts      map[string]int
	Leads       []Lead
}

type reviewDetailPage struct {
	GeneratedAt time.Time
	StatePath   string
	Lead        Lead
	Subject     string
	Body        string
	BackURL     string
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
	return mux
}

func (s *reviewServer) handleRoot(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	http.Redirect(w, r, "/leads?status=dry_run_ready", http.StatusSeeOther)
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
	page := reviewListPage{
		GeneratedAt: time.Now(),
		StatePath:   s.store.StatePath(),
		Status:      status,
		Bucket:      bucket,
		Query:       r.URL.Query().Get("q"),
		Counts:      reviewMessageCounts(state),
		Leads:       reviewLeads(state, MessageStatus(status), bucket, query),
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
		Subject:     subject,
		Body:        body,
		BackURL:     reviewBackURL(r),
	}
	if err := s.template.ExecuteTemplate(w, "detail", page); err != nil {
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

func reviewLeads(state OutreachState, status MessageStatus, bucket string, query string) []Lead {
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
		leads = append(leads, lead)
	}
	sort.SliceStable(leads, func(i, j int) bool {
		if leads[i].FitScore == leads[j].FitScore {
			return leads[i].Name < leads[j].Name
		}
		return leads[i].FitScore > leads[j].FitScore
	})
	return leads
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

const reviewTemplateHTML = `
{{define "list"}}
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recruiter Outreach Review</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;color:#17202a;background:#f7f8fa}
a{color:#1455d9;text-decoration:none}
header{background:#fff;border-bottom:1px solid #d9dee7;padding:16px 24px}
main{padding:20px 24px}
h1{font-size:28px;line-height:1.15;margin:8px 0 10px}
.meta{color:#5c6675;font-size:13px}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}
.tabs a,.pill{border:1px solid #c8d0dc;background:#fff;border-radius:6px;padding:7px 10px;font-size:13px;color:#1d2733}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
input,textarea,select{font:inherit;border:1px solid #b9c3d1;border-radius:6px;padding:8px;background:#fff}
button{font:inherit;border:1px solid #1f5fd6;background:#1f5fd6;color:#fff;border-radius:6px;padding:8px 12px;cursor:pointer}
.table-wrap{overflow-x:auto;border:1px solid #d9dee7;background:#fff}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d9dee7}
th,td{padding:10px;border-bottom:1px solid #e4e8ef;text-align:left;vertical-align:top;font-size:14px}
th{font-size:12px;color:#5c6675;text-transform:uppercase;letter-spacing:.04em;background:#fbfcfd}
.status{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.empty{background:#fff;border:1px solid #d9dee7;padding:20px}
@media (max-width:720px){header,main{padding:14px 16px}h1{font-size:24px}.filters{display:grid}.filters input,.filters select,.filters button{width:100%;box-sizing:border-box}th,td{font-size:13px;padding:8px}}
</style>
</head>
<body>
<header>
<h1>Recruiter Outreach Review</h1>
<div class="meta">State: {{.StatePath}} · Generated: {{.GeneratedAt.Format "2006-01-02 15:04:05"}}</div>
</header>
<main>
<nav class="tabs">
<a href="/leads?status=dry_run_ready">Messageable {{index .Counts "dry_run_ready"}}</a>
<a href="/leads?status=drafted">Drafted {{index .Counts "drafted"}}</a>
<a href="/leads?status=needs_edit">Needs Edit {{index .Counts "needs_edit"}}</a>
<a href="/leads?status=approved">Approved {{index .Counts "approved"}}</a>
<a href="/leads?status=send_failed">Send Failed {{index .Counts "send_failed"}}</a>
</nav>
<form class="filters" method="get" action="/leads">
<input type="hidden" name="status" value="{{.Status}}">
<select name="bucket">
<option value="">All buckets</option>
<option value="agency" {{if eq .Bucket "agency"}}selected{{end}}>Agencies</option>
<option value="recruiter" {{if eq .Bucket "recruiter"}}selected{{end}}>Recruiters</option>
</select>
<input name="q" value="{{.Query}}" placeholder="Search name, title, company">
<button type="submit">Filter</button>
</form>
{{if .Leads}}
<div class="table-wrap"><table>
<thead><tr><th>Name</th><th>Bucket</th><th>Status</th><th>Title</th><th>Company</th><th>Score</th><th></th></tr></thead>
<tbody>
{{range .Leads}}
<tr>
<td><strong>{{.Name}}</strong></td>
<td>{{bucket .}}</td>
<td><span class="status">{{.MessageStatus}}</span></td>
<td>{{ptr .Title}}</td>
<td>{{ptr .Company}}</td>
<td>{{.FitScore}}</td>
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

{{define "detail"}}
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{.Lead.Name}} · Outreach Review</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;color:#17202a;background:#f7f8fa}
a{color:#1455d9;text-decoration:none}
header{background:#fff;border-bottom:1px solid #d9dee7;padding:16px 24px}
main{max-width:1180px;padding:20px 24px}
h1{font-size:32px;line-height:1.15;margin:28px 0 12px}
.meta,.field{color:#5c6675;font-size:13px}
.meta{overflow-wrap:anywhere}
.summary{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-bottom:18px}
.field{background:#fff;border:1px solid #d9dee7;border-radius:6px;padding:10px;min-width:0;overflow-wrap:anywhere}
label{display:block;font-weight:600;margin:14px 0 6px}
input,textarea{box-sizing:border-box;width:100%;font:inherit;border:1px solid #b9c3d1;border-radius:6px;padding:10px;background:#fff}
textarea{min-height:360px;line-height:1.45;resize:vertical}
button{font:inherit;border:1px solid #1f5fd6;background:#1f5fd6;color:#fff;border-radius:6px;padding:8px 12px;cursor:pointer}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}
.secondary{background:#fff;color:#1d2733;border-color:#b9c3d1}
.warn{background:#fff7ed;color:#9a3412;border-color:#fdba74}
.approve{background:#137333;border-color:#137333}
@media (max-width:760px){header,main{padding:14px 16px}h1{font-size:26px;margin:20px 0 10px}.summary{grid-template-columns:1fr}.actions{display:grid}.actions button{width:100%}textarea{min-height:300px}}
@media (max-width:420px){h1{font-size:24px}.meta,.field{font-size:12px}input,textarea,button{font-size:16px}}
</style>
</head>
<body>
<header>
<a href="{{.BackURL}}">Back to list</a>
<h1>{{.Lead.Name}}</h1>
<div class="meta">State: {{.StatePath}} · Status: {{.Lead.MessageStatus}} · Generated: {{.GeneratedAt.Format "2006-01-02 15:04:05"}}</div>
</header>
<main>
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
</main>
</body>
</html>
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
		"backURL": func(status string, bucket string, query string) string {
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
			return url.QueryEscape("/leads?" + strings.Join(parts, "&"))
		},
	}
}

func urlQueryEscape(value string) string {
	return strings.ReplaceAll(url.QueryEscape(value), "+", "%20")
}
