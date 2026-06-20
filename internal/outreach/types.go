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

type MessageStatus string

const (
	MessageStatusNone               MessageStatus = "none"
	MessageStatusDrafted            MessageStatus = "drafted"
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
	SchemaVersion  int                      `json:"schema_version"`
	Leads          []Lead                   `json:"leads"`
	CaptureCursors map[string]CaptureCursor `json:"capture_cursors"`
	UpdatedAt      time.Time                `json:"updated_at"`
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
	ID              string        `json:"id"`
	Source          string        `json:"source"`
	Name            string        `json:"name"`
	FirstName       string        `json:"first_name"`
	ProfileURL      *string       `json:"profile_url"`
	SalesProfileURN *string       `json:"sales_profile_urn"`
	Title           *string       `json:"title"`
	Company         *string       `json:"company"`
	LeadType        LeadType      `json:"lead_type"`
	Status          LeadStatus    `json:"status"`
	MessageStatus   MessageStatus `json:"message_status"`
	FitScore        int           `json:"fit_score"`
	FitReasons      []string      `json:"fit_reasons"`
	RejectReasons   []string      `json:"reject_reasons"`
	EvidenceText    string        `json:"evidence_text"`
	MenuState       string        `json:"menu_state"`
	CapturedAt      *string       `json:"captured_at"`
	ImportedAt      time.Time     `json:"imported_at"`
	UpdatedAt       time.Time     `json:"updated_at"`
	Draft           *MessageDraft `json:"draft"`
	SendAttempts    []SendAttempt `json:"send_attempts"`
	Notes           []string      `json:"notes"`
}

type MessageDraft struct {
	Body        string    `json:"body"`
	Angle       string    `json:"angle"`
	Evidence    []string  `json:"evidence"`
	GeneratedAt time.Time `json:"generated_at"`
}

type SendAttempt struct {
	At        time.Time `json:"at"`
	DryRun    bool      `json:"dry_run"`
	Status    string    `json:"status"`
	ResultURL *string   `json:"result_url"`
	Note      *string   `json:"note"`
	OutPath   string    `json:"out_path"`
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
	ID            string        `json:"id"`
	Name          string        `json:"name"`
	ProfileURL    *string       `json:"profile_url"`
	Title         *string       `json:"title"`
	Company       *string       `json:"company"`
	Source        string        `json:"source"`
	LeadType      LeadType      `json:"lead_type"`
	Status        LeadStatus    `json:"status"`
	MessageStatus MessageStatus `json:"message_status"`
	FitScore      int           `json:"fit_score"`
	FitReasons    []string      `json:"fit_reasons"`
	EvidenceText  string        `json:"evidence_text"`
	Draft         *string       `json:"draft,omitempty"`
}

type DraftReport struct {
	GeneratedAt time.Time   `json:"generated_at"`
	Items       []QueueItem `json:"items"`
}

type StatusCounts struct {
	ByStatus        map[LeadStatus]int    `json:"by_status"`
	ByLeadType      map[LeadType]int      `json:"by_lead_type"`
	ByMessageStatus map[MessageStatus]int `json:"by_message_status"`
	BySource        map[string]int        `json:"by_source"`
}

func (s *OutreachState) Normalize() {
	if s.SchemaVersion == 0 {
		s.SchemaVersion = 1
	}
	if s.Leads == nil {
		s.Leads = []Lead{}
	}
	if s.CaptureCursors == nil {
		s.CaptureCursors = map[string]CaptureCursor{}
	}
	for i := range s.Leads {
		s.Leads[i].Normalize()
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
	l.EvidenceText = truncateEvidence(l.EvidenceText)
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
