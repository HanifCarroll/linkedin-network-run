package outreach

import (
	"strings"
	"time"
)

const AppDir = "recruiter-agency-outreach"

type LeadType string

const (
	LeadTypeContractRecruiter LeadType = "contract_recruiter"
	LeadTypeAgencyResource    LeadType = "agency_resource"
	LeadTypeAgencyDelivery    LeadType = "agency_delivery"
	LeadTypeAgencyFounder     LeadType = "agency_founder"
	LeadTypeBadFit            LeadType = "bad_fit"
)

type LeadStatus string

const (
	LeadStatusCaptured    LeadStatus = "captured"
	LeadStatusEligible    LeadStatus = "eligible"
	LeadStatusNeedsReview LeadStatus = "needs_review"
	LeadStatusRejected    LeadStatus = "rejected"
)

type AgencyAccountStatus string

const (
	AgencyAccountStatusQualified   AgencyAccountStatus = "qualified"
	AgencyAccountStatusNeedsReview AgencyAccountStatus = "needs_review"
	AgencyAccountStatusRejected    AgencyAccountStatus = "rejected"
	AgencyAccountStatusExhausted   AgencyAccountStatus = "exhausted"
)

type AgencyContactCandidateStatus string

const (
	AgencyContactCandidateStatusWebsiteContactCandidate AgencyContactCandidateStatus = "website_contact_candidate"
	AgencyContactCandidateStatusGenericInbox            AgencyContactCandidateStatus = "generic_inbox"
	AgencyContactCandidateStatusContactForm             AgencyContactCandidateStatus = "contact_form"
	AgencyContactCandidateStatusRejected                AgencyContactCandidateStatus = "rejected"
	AgencyContactCandidateStatusConverted               AgencyContactCandidateStatus = "converted"
)

type AgencyContactReviewStatus string

const (
	AgencyContactReviewStatusNeedsReview AgencyContactReviewStatus = "needs_review"
	AgencyContactReviewStatusApproved    AgencyContactReviewStatus = "approved"
	AgencyContactReviewStatusRejected    AgencyContactReviewStatus = "rejected"
	AgencyContactReviewStatusConverted   AgencyContactReviewStatus = "converted"
)

type MessageStatus string

const (
	MessageStatusNone               MessageStatus = "none"
	MessageStatusDrafted            MessageStatus = "drafted"
	MessageStatusNeedsEdit          MessageStatus = "needs_edit"
	MessageStatusApproved           MessageStatus = "approved"
	MessageStatusDryRunReady        MessageStatus = "dry_run_ready"
	MessageStatusSent               MessageStatus = "sent"
	MessageStatusManuallySent       MessageStatus = "manually_sent"
	MessageStatusNotMessageable     MessageStatus = "not_messageable"
	MessageStatusConversationExists MessageStatus = "conversation_exists"
	MessageStatusSendFailed         MessageStatus = "send_failed"
	MessageStatusBlocked            MessageStatus = "blocked"
	MessageStatusReplied            MessageStatus = "replied"
	MessageStatusRepliedNotFit      MessageStatus = "replied_not_fit"
	MessageStatusRepliedFuture      MessageStatus = "replied_future"
	MessageStatusRepliedUnknown     MessageStatus = "replied_unknown"
)

type OutreachState struct {
	SchemaVersion           int                      `json:"schema_version"`
	Leads                   []Lead                   `json:"leads"`
	AgencyAccounts          []AgencyAccount          `json:"agency_accounts"`
	AgencyContactCandidates []AgencyContactCandidate `json:"agency_contact_candidates"`
	CaptureCursors          map[string]CaptureCursor `json:"capture_cursors"`
	RunEvents               []RunEvent               `json:"run_events"`
	UpdatedAt               time.Time                `json:"updated_at"`
}

type RunEvent struct {
	At               time.Time `json:"at"`
	RunID            string    `json:"run_id,omitempty"`
	Phase            string    `json:"phase"`
	Command          string    `json:"command,omitempty"`
	Args             []string  `json:"args,omitempty"`
	Bucket           string    `json:"bucket,omitempty"`
	LeadID           string    `json:"lead_id,omitempty"`
	AccountID        string    `json:"account_id,omitempty"`
	Name             string    `json:"name,omitempty"`
	Result           string    `json:"result,omitempty"`
	Note             string    `json:"note,omitempty"`
	OutPath          string    `json:"out_path,omitempty"`
	DashboardPath    string    `json:"dashboard_path,omitempty"`
	StatePath        string    `json:"state_path,omitempty"`
	TargetAgencies   int       `json:"target_agencies,omitempty"`
	TargetRecruiters int       `json:"target_recruiters,omitempty"`
	AllowSend        bool      `json:"allow_send,omitempty"`
	StartedAt        time.Time `json:"started_at,omitempty"`
	CompletedAt      time.Time `json:"completed_at,omitempty"`
	Blocker          string    `json:"blocker,omitempty"`
}

type CaptureCursor struct {
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
	StateCounts         map[string]uint32 `json:"state_counts"`
}

type Lead struct {
	ID                    string        `json:"id"`
	Source                string        `json:"source"`
	Name                  string        `json:"name"`
	FirstName             string        `json:"first_name"`
	ProfileURL            *string       `json:"profile_url"`
	SalesProfileURN       *string       `json:"sales_profile_urn"`
	Title                 *string       `json:"title"`
	Company               *string       `json:"company"`
	AgencyAccountID       *string       `json:"agency_account_id,omitempty"`
	AgencyAccountName     *string       `json:"agency_account_name,omitempty"`
	AgencyAccountURL      *string       `json:"agency_account_url,omitempty"`
	AgencyAccountReasons  []string      `json:"agency_account_reasons,omitempty"`
	AgencyAccountEvidence string        `json:"agency_account_evidence,omitempty"`
	LeadType              LeadType      `json:"lead_type"`
	Status                LeadStatus    `json:"status"`
	MessageStatus         MessageStatus `json:"message_status"`
	FitScore              int           `json:"fit_score"`
	FitReasons            []string      `json:"fit_reasons"`
	RejectReasons         []string      `json:"reject_reasons"`
	EvidenceText          string        `json:"evidence_text"`
	MenuState             string        `json:"menu_state"`
	CapturedAt            *string       `json:"captured_at"`
	ImportedAt            time.Time     `json:"imported_at"`
	UpdatedAt             time.Time     `json:"updated_at"`
	MessageStatusAt       *time.Time    `json:"message_status_at,omitempty"`
	Draft                 *MessageDraft `json:"draft"`
	SendAttempts          []SendAttempt `json:"send_attempts"`
	Notes                 []string      `json:"notes"`
}

type AgencyAccount struct {
	ID                           string              `json:"id"`
	Source                       string              `json:"source"`
	Name                         string              `json:"name"`
	AccountURL                   *string             `json:"account_url"`
	Website                      *string             `json:"website"`
	Domain                       *string             `json:"domain"`
	Industry                     *string             `json:"industry"`
	Headcount                    *string             `json:"headcount"`
	Location                     *string             `json:"location"`
	Status                       AgencyAccountStatus `json:"status"`
	FitScore                     int                 `json:"fit_score"`
	FitReasons                   []string            `json:"fit_reasons"`
	RejectReasons                []string            `json:"reject_reasons"`
	EvidenceText                 string              `json:"evidence_text"`
	CapturedAt                   *string             `json:"captured_at"`
	ImportedAt                   time.Time           `json:"imported_at"`
	UpdatedAt                    time.Time           `json:"updated_at"`
	LastContactCaptureAt         *time.Time          `json:"last_contact_capture_at"`
	ContactCaptureCount          int                 `json:"contact_capture_count"`
	LastContactStrategy          *string             `json:"last_contact_strategy,omitempty"`
	LastContactError             *string             `json:"last_contact_error,omitempty"`
	LastContactErrorAt           *time.Time          `json:"last_contact_error_at,omitempty"`
	ContactErrorCount            int                 `json:"contact_error_count,omitempty"`
	LastWebsiteEnrichedAt        *time.Time          `json:"last_website_enriched_at,omitempty"`
	WebsiteEnrichmentCount       int                 `json:"website_enrichment_count,omitempty"`
	LastWebsiteEnrichmentError   *string             `json:"last_website_enrichment_error,omitempty"`
	LastWebsiteEnrichmentErrorAt *time.Time          `json:"last_website_enrichment_error_at,omitempty"`
	Notes                        []string            `json:"notes"`
}

type AgencyContactCandidate struct {
	ID                string                       `json:"id"`
	AgencyAccountID   string                       `json:"agency_account_id"`
	AgencyAccountName string                       `json:"agency_account_name"`
	Source            string                       `json:"source"`
	SourceURL         *string                      `json:"source_url,omitempty"`
	Status            AgencyContactCandidateStatus `json:"status"`
	ReviewStatus      AgencyContactReviewStatus    `json:"review_status"`
	Name              *string                      `json:"name,omitempty"`
	Title             *string                      `json:"title,omitempty"`
	Email             *string                      `json:"email,omitempty"`
	ProfileURL        *string                      `json:"profile_url,omitempty"`
	ContactURL        *string                      `json:"contact_url,omitempty"`
	FormAction        *string                      `json:"form_action,omitempty"`
	Evidence          []string                     `json:"evidence"`
	ImportedAt        time.Time                    `json:"imported_at"`
	UpdatedAt         time.Time                    `json:"updated_at"`
	Notes             []string                     `json:"notes"`
}

type MessageDraft struct {
	Subject     string    `json:"subject,omitempty"`
	Body        string    `json:"body"`
	Angle       string    `json:"angle"`
	Evidence    []string  `json:"evidence"`
	GeneratedAt time.Time `json:"generated_at"`
}

type SendAttempt struct {
	At          time.Time         `json:"at"`
	RunID       string            `json:"run_id,omitempty"`
	DryRun      bool              `json:"dry_run"`
	Status      string            `json:"status"`
	ResultURL   *string           `json:"result_url"`
	Note        *string           `json:"note"`
	OutPath     string            `json:"out_path"`
	Diagnostics map[string]string `json:"diagnostics,omitempty"`
}

type ImportSummary struct {
	Source     string `json:"source"`
	Stored     int    `json:"stored"`
	Updated    int    `json:"updated"`
	Rejected   int    `json:"rejected"`
	Reviewed   int    `json:"reviewed"`
	Eligible   int    `json:"eligible"`
	TotalLeads int    `json:"total_leads"`
}

type QueueItem struct {
	ID                    string        `json:"id"`
	Name                  string        `json:"name"`
	ProfileURL            *string       `json:"profile_url"`
	Title                 *string       `json:"title"`
	Company               *string       `json:"company"`
	AgencyAccountName     *string       `json:"agency_account_name,omitempty"`
	AgencyAccountURL      *string       `json:"agency_account_url,omitempty"`
	AgencyAccountReasons  []string      `json:"agency_account_reasons,omitempty"`
	AgencyAccountEvidence string        `json:"agency_account_evidence,omitempty"`
	Source                string        `json:"source"`
	LeadType              LeadType      `json:"lead_type"`
	Status                LeadStatus    `json:"status"`
	MessageStatus         MessageStatus `json:"message_status"`
	FitScore              int           `json:"fit_score"`
	FitReasons            []string      `json:"fit_reasons"`
	EvidenceText          string        `json:"evidence_text"`
	Draft                 *string       `json:"draft,omitempty"`
}

type DraftReport struct {
	GeneratedAt time.Time   `json:"generated_at"`
	Items       []QueueItem `json:"items"`
}

type StatusCounts struct {
	ByStatus                             map[LeadStatus]int                   `json:"by_status"`
	ByLeadType                           map[LeadType]int                     `json:"by_lead_type"`
	ByMessageStatus                      map[MessageStatus]int                `json:"by_message_status"`
	BySource                             map[string]int                       `json:"by_source"`
	ByAgencyAccountStatus                map[AgencyAccountStatus]int          `json:"by_agency_account_status"`
	ByAgencyAccountSource                map[string]int                       `json:"by_agency_account_source"`
	ByAgencyContactCandidateStatus       map[AgencyContactCandidateStatus]int `json:"by_agency_contact_candidate_status"`
	ByAgencyContactCandidateReviewStatus map[AgencyContactReviewStatus]int    `json:"by_agency_contact_candidate_review_status"`
	ByAgencyContactCandidateSource       map[string]int                       `json:"by_agency_contact_candidate_source"`
}

func (s *OutreachState) Normalize() {
	if s.SchemaVersion == 0 {
		s.SchemaVersion = 1
	}
	if s.Leads == nil {
		s.Leads = []Lead{}
	}
	if s.AgencyAccounts == nil {
		s.AgencyAccounts = []AgencyAccount{}
	}
	if s.AgencyContactCandidates == nil {
		s.AgencyContactCandidates = []AgencyContactCandidate{}
	}
	if s.CaptureCursors == nil {
		s.CaptureCursors = map[string]CaptureCursor{}
	}
	if s.RunEvents == nil {
		s.RunEvents = []RunEvent{}
	}
	for i := range s.AgencyAccounts {
		s.AgencyAccounts[i].Normalize()
	}
	for i := range s.Leads {
		s.Leads[i].Normalize()
	}
	for i := range s.AgencyContactCandidates {
		s.AgencyContactCandidates[i].Normalize()
	}
}

func (l *Lead) Normalize() {
	l.Name = cleanText(l.Name)
	l.Source = cleanText(l.Source)
	if l.FirstName == "" {
		l.FirstName = firstName(l.Name)
	}
	if l.MessageStatus == "" {
		l.MessageStatus = MessageStatusNone
	}
	if l.Status == "" {
		l.Status = LeadStatusCaptured
	}
	if l.FitReasons == nil {
		l.FitReasons = []string{}
	}
	if l.RejectReasons == nil {
		l.RejectReasons = []string{}
	}
	if l.Notes == nil {
		l.Notes = []string{}
	}
	if l.SendAttempts == nil {
		l.SendAttempts = []SendAttempt{}
	}
	if l.AgencyAccountReasons == nil {
		l.AgencyAccountReasons = []string{}
	}
	l.EvidenceText = truncateEvidence(l.EvidenceText)
	l.AgencyAccountEvidence = truncateEvidence(l.AgencyAccountEvidence)
}

func (a *AgencyAccount) Normalize() {
	a.Name = cleanText(a.Name)
	a.Source = cleanText(a.Source)
	if a.Status == "" {
		a.Status = AgencyAccountStatusNeedsReview
	}
	if a.FitReasons == nil {
		a.FitReasons = []string{}
	}
	if a.RejectReasons == nil {
		a.RejectReasons = []string{}
	}
	if a.Notes == nil {
		a.Notes = []string{}
	}
	a.EvidenceText = truncateEvidence(a.EvidenceText)
}

func (c *AgencyContactCandidate) Normalize() {
	c.Source = cleanText(c.Source)
	c.AgencyAccountID = cleanText(c.AgencyAccountID)
	c.AgencyAccountName = cleanText(c.AgencyAccountName)
	if c.Status == "" {
		c.Status = AgencyContactCandidateStatusWebsiteContactCandidate
	}
	if c.ReviewStatus == "" {
		c.ReviewStatus = AgencyContactReviewStatusNeedsReview
	}
	if c.Evidence == nil {
		c.Evidence = []string{}
	}
	if c.Notes == nil {
		c.Notes = []string{}
	}
	c.Evidence = truncateEvidenceItems(c.Evidence)
}

func cleanText(value string) string {
	return strings.Join(strings.Fields(value), " ")
}

func firstName(name string) string {
	fields := strings.Fields(name)
	if len(fields) == 0 {
		return "there"
	}
	return fields[0]
}

func truncateEvidence(value string) string {
	cleaned := cleanText(value)
	if len(cleaned) <= 700 {
		return cleaned
	}
	return cleaned[:700]
}

func truncateEvidenceItems(values []string) []string {
	if len(values) == 0 {
		return []string{}
	}
	items := []string{}
	for _, value := range values {
		cleaned := truncateEvidence(value)
		if cleaned == "" {
			continue
		}
		items = append(items, cleaned)
	}
	return items
}
