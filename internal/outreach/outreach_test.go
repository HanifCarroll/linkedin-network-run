package outreach

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/hanifcarroll/linkedin-network-run/internal/app"
	"golang.org/x/net/html"
)

func TestImportCaptureClassifiesContractRecruiter(t *testing.T) {
	source := "ASAP - Contract Recruiters Staffing"
	state := OutreachState{}
	capture := app.SalesNavCapture{
		Source: &source,
		Rows: []app.SalesNavCaptureRow{{
			Index:      0,
			Name:       strPtr("Riley Recruiter"),
			Text:       strPtr("Riley Recruiter\nSenior Technical Recruiter\nAcme Staffing\nContract React TypeScript roles"),
			ProfileURL: strPtr("https://www.linkedin.com/sales/lead/abc?_ntb=x"),
			MenuState:  strPtr("connectable"),
		}},
	}
	summary, err := ImportCapture(&state, capture, ImportOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if summary.Eligible != 1 || len(state.Leads) != 1 {
		t.Fatalf("summary=%#v leads=%#v", summary, state.Leads)
	}
	lead := state.Leads[0]
	if lead.LeadType != LeadTypeContractRecruiter || lead.Status != LeadStatusEligible {
		t.Fatalf("lead classification = %s/%s", lead.LeadType, lead.Status)
	}
	if lead.Title == nil || *lead.Title != "Senior Technical Recruiter" {
		t.Fatalf("title = %v", lead.Title)
	}
}

func TestImportCaptureDedupesSalesNavLeadAuthTokens(t *testing.T) {
	source := "ASAP - Contract Recruiters Staffing"
	state := OutreachState{}
	capture := app.SalesNavCapture{
		Source: &source,
		Rows: []app.SalesNavCaptureRow{
			{
				Index:      0,
				Name:       strPtr("Riley Recruiter"),
				Text:       strPtr("Riley Recruiter\nSenior Technical Recruiter\nAcme Staffing\nContract React TypeScript roles"),
				ProfileURL: strPtr("https://www.linkedin.com/sales/lead/abc123,NAME_SEARCH,token-one?_ntb=x"),
				MenuState:  strPtr("connectable"),
			},
			{
				Index:      1,
				Name:       strPtr("Riley Recruiter"),
				Text:       strPtr("Riley Recruiter\nSenior Technical Recruiter\nAcme Staffing\nContract React TypeScript roles"),
				ProfileURL: strPtr("https://www.linkedin.com/sales/lead/abc123,SEARCH,token-two"),
				MenuState:  strPtr("connectable"),
			},
		},
	}
	summary, err := ImportCapture(&state, capture, ImportOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if len(state.Leads) != 1 || summary.Stored != 1 || summary.Updated != 1 {
		t.Fatalf("summary=%#v leads=%#v", summary, state.Leads)
	}
}

func TestImportCaptureClassifiesAgencyDeliveryAndDrafts(t *testing.T) {
	source := "ASAP - Agency Owners Delivery"
	state := OutreachState{}
	capture := app.SalesNavCapture{
		Source: &source,
		Rows: []app.SalesNavCaptureRow{{
			Index:      0,
			Name:       strPtr("Dana Delivery"),
			Text:       strPtr("Dana Delivery\nHead of Delivery\nBright Product Studio\nReact TypeScript AI product agency"),
			ProfileURL: strPtr("https://www.linkedin.com/sales/lead/agency"),
			MenuState:  strPtr("connectable"),
			Links: []app.SalesNavCaptureLink{{
				Text: strPtr("Bright Product Studio"),
				Href: strPtr("https://www.linkedin.com/sales/company/bright"),
			}},
		}},
	}
	if _, err := ImportCapture(&state, capture, ImportOptions{}); err != nil {
		t.Fatal(err)
	}
	report := DraftMessages(&state, 10)
	if len(report.Items) != 1 {
		t.Fatalf("draft count = %d", len(report.Items))
	}
	lead := state.Leads[0]
	if lead.LeadType != LeadTypeAgencyDelivery || lead.MessageStatus != MessageStatusDrafted {
		t.Fatalf("lead = %#v", lead)
	}
	if lead.Draft == nil || !strings.Contains(lead.Draft.Body, "I'm a full-stack product engineer (8 YoE) that builds and launches AI-powered web & mobile products.") {
		t.Fatalf("draft = %#v", lead.Draft)
	}
}

func TestImportCaptureUsesCompanyLinkInsteadOfLocationLine(t *testing.T) {
	source := "ASAP - Agency Owners Delivery"
	state := OutreachState{}
	capture := app.SalesNavCapture{
		Source: &source,
		Rows: []app.SalesNavCaptureRow{{
			Index:      0,
			Name:       strPtr("Dustin Overbeck"),
			Text:       strPtr("Dustin Overbeck\n2nd degree connection\nOwner  Tweak Agency\nSturgeon Bay, Wisconsin, United States\nAbout:\nDigital product agency and product marketing work"),
			ProfileURL: strPtr("https://www.linkedin.com/sales/lead/dustin"),
			MenuState:  strPtr("connectable"),
			Links: []app.SalesNavCaptureLink{{
				Text: strPtr("Tweak Agency"),
				Href: strPtr("https://www.linkedin.com/sales/company/3597948"),
			}},
		}},
	}
	if _, err := ImportCapture(&state, capture, ImportOptions{}); err != nil {
		t.Fatal(err)
	}
	report := DraftMessages(&state, 10)
	if len(report.Items) != 1 {
		t.Fatalf("draft count = %d", len(report.Items))
	}
	lead := state.Leads[0]
	if lead.Title == nil || *lead.Title != "Owner" {
		t.Fatalf("title = %v", lead.Title)
	}
	if lead.Company == nil || *lead.Company != "Tweak Agency" {
		t.Fatalf("company = %v", lead.Company)
	}
	if lead.Draft == nil || strings.Contains(lead.Draft.Body, "Sturgeon Bay, Wisconsin, United States works") {
		t.Fatalf("draft = %#v", lead.Draft)
	}
	if !strings.Contains(lead.Draft.Body, "Recent projects:") {
		t.Fatalf("draft = %q", lead.Draft.Body)
	}
}

func TestImportCaptureRejectsAgencySourceWithoutProfileAgencySignal(t *testing.T) {
	source := "ASAP - Agency Owners Delivery"
	state := OutreachState{}
	capture := app.SalesNavCapture{
		Source: &source,
		Rows: []app.SalesNavCaptureRow{{
			Index:      0,
			Name:       strPtr("Vin Curto"),
			Text:       strPtr("Vin Curto\n2nd degree connection\nFounder  TEN26.ai\nNew York City Metropolitan Area\nAbout:\nAI growth strategist and performance marketer"),
			ProfileURL: strPtr("https://www.linkedin.com/sales/lead/vin"),
			MenuState:  strPtr("connectable"),
			Links: []app.SalesNavCaptureLink{{
				Text: strPtr("TEN26.ai"),
				Href: strPtr("https://www.linkedin.com/sales/company/123"),
			}},
		}},
	}
	if _, err := ImportCapture(&state, capture, ImportOptions{}); err != nil {
		t.Fatal(err)
	}
	lead := state.Leads[0]
	if lead.Status != LeadStatusRejected || lead.LeadType != LeadTypeBadFit {
		t.Fatalf("lead = %#v", lead)
	}
}

func TestImportCaptureRejectsAgencyCompanyWithoutPersonaTitle(t *testing.T) {
	source := "ASAP - Agency Owners Delivery"
	state := OutreachState{}
	capture := app.SalesNavCapture{
		Source: &source,
		Rows: []app.SalesNavCaptureRow{{
			Index:      0,
			Name:       strPtr("Troy Hipolito"),
			Text:       strPtr("Troy Hipolito\n2nd degree connection\nThe Not-So-Boring LinkedIn Guy\nLas Vegas, Nevada, United States\nAbout:\nSales Training & Outreach for coaches and consultants"),
			ProfileURL: strPtr("https://www.linkedin.com/sales/lead/troy"),
			MenuState:  strPtr("connectable"),
			Links: []app.SalesNavCaptureLink{{
				Text: strPtr("The Troy Agency"),
				Href: strPtr("https://www.linkedin.com/sales/company/troy-agency"),
			}},
		}},
	}
	if _, err := ImportCapture(&state, capture, ImportOptions{}); err != nil {
		t.Fatal(err)
	}
	lead := state.Leads[0]
	if lead.Status != LeadStatusRejected || lead.LeadType != LeadTypeBadFit {
		t.Fatalf("lead = %#v", lead)
	}
}

func TestImportCaptureRejectsStudioInTitleWithoutCompanyLink(t *testing.T) {
	source := "ASAP - Agency Owners Delivery"
	state := OutreachState{}
	capture := app.SalesNavCapture{
		Source: &source,
		Rows: []app.SalesNavCaptureRow{{
			Index:      0,
			Name:       strPtr("Aaron Francis"),
			Text:       strPtr("Aaron Francis\n2nd degree connection\nCo-Founder Try Hard Studios\nDallas, Texas, United States\nAbout:\nWe make videos developers want to watch"),
			ProfileURL: strPtr("https://www.linkedin.com/sales/lead/aaron"),
			MenuState:  strPtr("connectable"),
		}},
	}
	if _, err := ImportCapture(&state, capture, ImportOptions{}); err != nil {
		t.Fatal(err)
	}
	lead := state.Leads[0]
	if lead.Status != LeadStatusRejected || lead.LeadType != LeadTypeBadFit {
		t.Fatalf("lead = %#v", lead)
	}
}

func TestImportAccountCaptureQualifiesAndRejectsAgencyAccounts(t *testing.T) {
	source := AgencyAccountProductSource
	state := OutreachState{}
	capture := SalesNavAccountCapture{
		Source: &source,
		URL:    strPtr("https://www.linkedin.com/sales/search/company"),
		Rows: []SalesNavAccountCaptureRow{
			{
				Index:      0,
				Name:       strPtr("Bright Product Studio"),
				Text:       strPtr("Bright Product Studio\nSoftware Development\nCustom software, React, TypeScript, AI and MVP product launches"),
				AccountURL: strPtr("https://www.linkedin.com/sales/company/12345?_ntb=x"),
				Website:    strPtr("https://bright.example.com"),
				Industry:   strPtr("Software Development"),
			},
			{
				Index:      1,
				Name:       strPtr("Growth Ads Agency"),
				Text:       strPtr("Growth Ads Agency\nAdvertising services\nPaid media, SEO, social media marketing, and lead generation"),
				AccountURL: strPtr("https://www.linkedin.com/sales/company/99999"),
			},
		},
	}
	summary, err := ImportAccountCapture(&state, capture)
	if err != nil {
		t.Fatal(err)
	}
	if summary.Qualified != 1 || summary.Rejected != 1 || len(state.AgencyAccounts) != 2 {
		t.Fatalf("summary=%#v accounts=%#v", summary, state.AgencyAccounts)
	}
	qualified := state.AgencyAccounts[0]
	if qualified.Name != "Bright Product Studio" || qualified.Status != AgencyAccountStatusQualified {
		t.Fatalf("qualified account = %#v", qualified)
	}
	if qualified.Domain == nil || *qualified.Domain != "bright.example.com" {
		t.Fatalf("domain = %v", qualified.Domain)
	}
	rejected := state.AgencyAccounts[1]
	if rejected.Name != "Growth Ads Agency" || rejected.Status != AgencyAccountStatusRejected {
		t.Fatalf("rejected account = %#v", rejected)
	}
	if state.CaptureCursors[source].OutputRowCount != 2 {
		t.Fatalf("cursor = %#v", state.CaptureCursors[source])
	}
}

func TestImportAccountCaptureQualifiesWordPressWebDesignAccounts(t *testing.T) {
	source := AgencyAccountDevelopmentSource
	state := OutreachState{}
	capture := SalesNavAccountCapture{
		Source: &source,
		Rows: []SalesNavAccountCaptureRow{{
			Index:      0,
			Name:       strPtr("QeWebby - WordPress Development Agency"),
			Text:       strPtr("QeWebby - WordPress Development Agency\nIT Services and IT Consulting\nWordPress agency crafting high-performing websites with web designer and WordPress developer services"),
			AccountURL: strPtr("https://www.linkedin.com/sales/company/79865165"),
		}},
	}
	if _, err := ImportAccountCapture(&state, capture); err != nil {
		t.Fatal(err)
	}
	account := state.AgencyAccounts[0]
	if account.Status != AgencyAccountStatusQualified {
		t.Fatalf("account = %#v", account)
	}
	if !containsAny(strings.Join(account.FitReasons, "\n"), "website/wordpress build account signal") {
		t.Fatalf("fit reasons = %#v", account.FitReasons)
	}
}

func TestImportCaptureUsesQualifiedAgencyAccountContext(t *testing.T) {
	account := AgencyAccount{
		ID:           "acct_bright",
		Name:         "Bright Product Studio",
		AccountURL:   strPtr("https://www.linkedin.com/sales/company/12345"),
		Status:       AgencyAccountStatusQualified,
		FitScore:     90,
		FitReasons:   []string{"software/product delivery account signal"},
		EvidenceText: "Bright Product Studio Software Development Custom software and MVP product delivery",
	}
	strategy, ok := firstAgencyContactSearchStrategy(account)
	if !ok {
		t.Fatal("missing agency contact strategy")
	}
	source := agencyContactSource(account, strategy)
	state := OutreachState{AgencyAccounts: []AgencyAccount{account}}
	capture := app.SalesNavCapture{
		Source: &source,
		Rows: []app.SalesNavCaptureRow{{
			Index:      0,
			Name:       strPtr("Dana Founder"),
			Text:       strPtr("Dana Founder\nFounder\nNew York City Metropolitan Area\nAbout:\nProduct delivery leadership"),
			ProfileURL: strPtr("https://www.linkedin.com/sales/lead/dana"),
			MenuState:  strPtr("connectable"),
		}},
	}
	if _, err := ImportCapture(&state, capture, ImportOptions{AgencyAccount: &account}); err != nil {
		t.Fatal(err)
	}
	lead := state.Leads[0]
	if lead.Status != LeadStatusEligible || lead.LeadType != LeadTypeAgencyFounder {
		t.Fatalf("lead = %#v", lead)
	}
	if lead.AgencyAccountName == nil || *lead.AgencyAccountName != "Bright Product Studio" {
		t.Fatalf("agency account context = %#v", lead)
	}
	report := DraftMessages(&state, 10)
	if len(report.Items) != 1 {
		t.Fatalf("draft count = %d", len(report.Items))
	}
	if state.Leads[0].Draft == nil || !strings.Contains(state.Leads[0].Draft.Body, "I'm a full-stack product engineer (8 YoE) that builds and launches AI-powered web & mobile products.") {
		t.Fatalf("draft = %#v", state.Leads[0].Draft)
	}
	if !strings.Contains(strings.Join(state.Leads[0].Draft.Evidence, "\n"), "Agency account reasons") {
		t.Fatalf("draft evidence = %#v", state.Leads[0].Draft.Evidence)
	}
}

func TestImportCaptureRejectsNonRecruiterAgencySource(t *testing.T) {
	source := "ASAP - Startup CTO Eng Leaders"
	state := OutreachState{}
	capture := app.SalesNavCapture{
		Source: &source,
		Rows: []app.SalesNavCaptureRow{{
			Index:      0,
			Name:       strPtr("Casey CTO"),
			Text:       strPtr("Casey CTO\nCTO\nSaaS Company"),
			ProfileURL: strPtr("https://www.linkedin.com/sales/lead/cto"),
			MenuState:  strPtr("connectable"),
		}},
	}
	if _, err := ImportCapture(&state, capture, ImportOptions{}); err != nil {
		t.Fatal(err)
	}
	if state.Leads[0].Status != LeadStatusRejected || state.Leads[0].LeadType != LeadTypeBadFit {
		t.Fatalf("lead = %#v", state.Leads[0])
	}
}

func TestImportCaptureRejectsRecruiterSourceWithoutRecruiterSignal(t *testing.T) {
	source := "ASAP - Contract Recruiters Staffing"
	state := OutreachState{}
	capture := app.SalesNavCapture{
		Source: &source,
		Rows: []app.SalesNavCaptureRow{{
			Index:      0,
			Name:       strPtr("Jacques Nack"),
			Text:       strPtr("Jacques Nack\nFounder & CEO\nc² (cSquare)\nAI GRC Platform and payments"),
			ProfileURL: strPtr("https://www.linkedin.com/sales/lead/jacques"),
			MenuState:  strPtr("connectable"),
			Links: []app.SalesNavCaptureLink{{
				Text: strPtr("c² (cSquare)"),
				Href: strPtr("https://www.linkedin.com/sales/company/csquare"),
			}},
		}},
	}
	if _, err := ImportCapture(&state, capture, ImportOptions{}); err != nil {
		t.Fatal(err)
	}
	lead := state.Leads[0]
	if lead.Status != LeadStatusRejected || lead.LeadType != LeadTypeBadFit {
		t.Fatalf("lead = %#v", lead)
	}
}

func TestImportCaptureRejectsRecruitingMentionWithoutRecruiterTitle(t *testing.T) {
	source := "ASAP - Contract Recruiters Staffing"
	state := OutreachState{}
	capture := app.SalesNavCapture{
		Source: &source,
		Rows: []app.SalesNavCaptureRow{{
			Index:      0,
			Name:       strPtr("Brenna Lasky"),
			Text:       strPtr("Brenna Lasky\nFounder\nBrenna Lasky Coaching\nAbout:\nEx-Meta, Salesforce, Google Recruiting; sharing my journey into big tech"),
			ProfileURL: strPtr("https://www.linkedin.com/sales/lead/brenna"),
			MenuState:  strPtr("connectable"),
			Links: []app.SalesNavCaptureLink{{
				Text: strPtr("Brenna Lasky Coaching"),
				Href: strPtr("https://www.linkedin.com/sales/company/brenna-coaching"),
			}},
		}},
	}
	if _, err := ImportCapture(&state, capture, ImportOptions{}); err != nil {
		t.Fatal(err)
	}
	lead := state.Leads[0]
	if lead.Status != LeadStatusRejected || lead.LeadType != LeadTypeBadFit {
		t.Fatalf("lead = %#v", lead)
	}
}

func TestImportCaptureDedupesNormalizedProfileURL(t *testing.T) {
	source := "ASAP - Contract Recruiters Staffing"
	state := OutreachState{}
	first := app.SalesNavCapture{
		Source: &source,
		Rows: []app.SalesNavCaptureRow{{
			Index:      0,
			Name:       strPtr("Riley Recruiter"),
			Text:       strPtr("Riley Recruiter\nTechnical Recruiter\nAcme Staffing"),
			ProfileURL: strPtr("https://www.linkedin.com/sales/lead/abc?_ntb=x"),
			MenuState:  strPtr("connectable"),
		}},
	}
	second := app.SalesNavCapture{
		Source: &source,
		Rows: []app.SalesNavCaptureRow{{
			Index:      1,
			Name:       strPtr("Riley Recruiter"),
			Text:       strPtr("Riley Recruiter\nTechnical Recruiter\nAcme Staffing\nC2C contract"),
			ProfileURL: strPtr("https://www.linkedin.com/sales/lead/abc"),
			MenuState:  strPtr("connectable"),
		}},
	}
	if _, err := ImportCapture(&state, first, ImportOptions{}); err != nil {
		t.Fatal(err)
	}
	summary, err := ImportCapture(&state, second, ImportOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if summary.Updated != 1 || len(state.Leads) != 1 {
		t.Fatalf("summary=%#v leads=%#v", summary, state.Leads)
	}
}

func TestApplyMessageSendResultMapsStatuses(t *testing.T) {
	lead := Lead{ID: "lead_1", Name: "Riley Recruiter", MessageStatus: MessageStatusDrafted}
	ApplyMessageSendResult(&lead, MessageSendResult{DryRun: true, Status: "dry-run-messageable"}, "/tmp/dry-run.json", "run_1")
	if lead.MessageStatus != MessageStatusDryRunReady || len(lead.SendAttempts) != 1 {
		t.Fatalf("dry-run lead = %#v", lead)
	}
	if lead.MessageStatusAt == nil || lead.SendAttempts[0].At.IsZero() || lead.SendAttempts[0].RunID != "run_1" {
		t.Fatalf("timestamp/run id missing: %#v", lead)
	}
	ApplyMessageSendResult(&lead, MessageSendResult{DryRun: false, Status: "sent-clicked"}, "/tmp/sent.json", "run_1")
	if lead.MessageStatus != MessageStatusSent || len(lead.SendAttempts) != 2 {
		t.Fatalf("sent lead = %#v", lead)
	}
	ApplyMessageSendResult(&lead, MessageSendResult{DryRun: true, Status: "not-messageable"}, "/tmp/not-messageable.json", "run_1")
	if lead.MessageStatus != MessageStatusNotMessageable || len(lead.SendAttempts) != 3 {
		t.Fatalf("not-messageable lead = %#v", lead)
	}
	ApplyMessageSendResult(&lead, MessageSendResult{DryRun: true, Status: "conversation-exists"}, "/tmp/conversation.json", "run_1")
	if lead.MessageStatus != MessageStatusConversationExists || len(lead.SendAttempts) != 4 {
		t.Fatalf("conversation-exists lead = %#v", lead)
	}
}

func TestApplyMessageSendResultMapsBlockedAndFailure(t *testing.T) {
	lead := Lead{ID: "lead_1", Name: "Riley Recruiter", MessageStatus: MessageStatusDrafted}
	ApplyMessageSendResult(&lead, MessageSendResult{DryRun: true, Status: "blocked"}, "/tmp/blocked.json", "run_2")
	if lead.MessageStatus != MessageStatusBlocked {
		t.Fatalf("blocked status = %s", lead.MessageStatus)
	}
	ApplyMessageSendResult(&lead, MessageSendResult{DryRun: false, Status: "composer-missing"}, "/tmp/fail.json", "run_2")
	if lead.MessageStatus != MessageStatusSendFailed {
		t.Fatalf("failed status = %s", lead.MessageStatus)
	}
}

func TestDraftMessagesSkipsTerminalMessageStatuses(t *testing.T) {
	state := OutreachState{Leads: []Lead{
		{ID: "lead_1", Name: "Ready Recruiter", FirstName: "Ready", LeadType: LeadTypeContractRecruiter, Status: LeadStatusEligible, MessageStatus: MessageStatusNone, FitScore: 90},
		{ID: "lead_2", Name: "Existing Thread", FirstName: "Existing", LeadType: LeadTypeContractRecruiter, Status: LeadStatusEligible, MessageStatus: MessageStatusConversationExists, FitScore: 95},
		{ID: "lead_3", Name: "Already Sent", FirstName: "Already", LeadType: LeadTypeAgencyDelivery, Status: LeadStatusEligible, MessageStatus: MessageStatusSent, FitScore: 92},
	}}
	report := DraftMessages(&state, 10)
	if len(report.Items) != 1 || report.Items[0].ID != "lead_1" {
		t.Fatalf("report items = %#v", report.Items)
	}
}

func TestDraftMessagesStoresAngleAndEvidence(t *testing.T) {
	state := OutreachState{Leads: []Lead{{
		ID:            "lead_1",
		Name:          "Morgan Manager",
		FirstName:     "Morgan",
		Title:         strPtr("Resource Manager"),
		Company:       strPtr("Bright Product Studio"),
		LeadType:      LeadTypeAgencyResource,
		Status:        LeadStatusEligible,
		MessageStatus: MessageStatusNone,
		FitScore:      96,
		FitReasons:    []string{"agency resource/resourcing title", "software/product/AI signal"},
		EvidenceText:  "Resource Manager at Bright Product Studio working on React and AI product delivery",
	}}}
	report := DraftMessages(&state, 10)
	if len(report.Items) != 1 {
		t.Fatalf("draft count = %d", len(report.Items))
	}
	lead := state.Leads[0]
	if lead.Draft == nil {
		t.Fatal("expected draft")
	}
	if !strings.Contains(lead.Draft.Angle, "agency resource manager") {
		t.Fatalf("angle = %q", lead.Draft.Angle)
	}
	if len(lead.Draft.Evidence) < 3 {
		t.Fatalf("evidence = %#v", lead.Draft.Evidence)
	}
	if !strings.Contains(lead.Draft.Body, "US citizen contracting via my LLC (1099/C2C)") {
		t.Fatalf("body = %q", lead.Draft.Body)
	}
	if !strings.Contains(lead.Draft.Body, "Are you the right person to ask about this kind of project support?") || strings.Contains(lead.Draft.Body, "Would you like me to send my resume and project examples?") {
		t.Fatalf("body = %q", lead.Draft.Body)
	}
}

func TestRecruiterDraftUsesApprovedContractTemplate(t *testing.T) {
	lead := Lead{
		Name:      "Jackie Recruiter",
		FirstName: "Jackie",
		Title:     strPtr("Sr. Recruiter (Contract)"),
		Company:   strPtr("FTI Consulting"),
		LeadType:  LeadTypeContractRecruiter,
	}
	body := recruiterDraft(lead)
	if !strings.Contains(body, "Hi Jackie,") {
		t.Fatalf("body = %q", body)
	}
	if strings.Contains(body, "profile mentions") || !strings.Contains(body, "I saw that you recruit for contract roles at FTI Consulting, and I'm reaching out about contract work.") {
		t.Fatalf("body = %q", body)
	}
	if !strings.Contains(body, "I'm a full-stack product engineer (8 YoE) that builds and launches AI-powered web & mobile products.") {
		t.Fatalf("body = %q", body)
	}
	if !strings.Contains(body, "Turned an AI media MVP into a production agent platform") {
		t.Fatalf("body = %q", body)
	}
	if !strings.Contains(body, "Recent projects:") || strings.Contains(body, "Recent wins:") {
		t.Fatalf("body = %q", body)
	}
	if !strings.Contains(body, "Are you the right person to ask about contract roles that fit this background?") || strings.Contains(body, "Would you like me to send my resume and project examples?") {
		t.Fatalf("body = %q", body)
	}
	if strings.Contains(body, "Best,") || strings.Contains(body, "Hanif Carroll") {
		t.Fatalf("body = %q", body)
	}
}

func TestRecruiterDraftUsesTechnicalRecruiterOpening(t *testing.T) {
	lead := Lead{
		Name:      "Riley Recruiter",
		FirstName: "Riley",
		Title:     strPtr("Senior Technical Recruiter Contract"),
		Company:   strPtr("Randstad"),
		LeadType:  LeadTypeContractRecruiter,
	}
	body := recruiterDraft(lead)
	if !strings.Contains(body, "I saw that you recruit for contract technical roles at Randstad, and I'm reaching out about contract work.") {
		t.Fatalf("body = %q", body)
	}
}

func TestAgencyDraftDoesNotUseLocationAsCompany(t *testing.T) {
	lead := Lead{
		Name:      "Troy Hipolito",
		FirstName: "Troy",
		Company:   strPtr("Las Vegas, Nevada, United States"),
		LeadType:  LeadTypeAgencyDelivery,
	}
	body := agencyDraft(lead)
	if strings.Contains(body, "Las Vegas, Nevada, United States works") {
		t.Fatalf("body = %q", body)
	}
	if !strings.Contains(body, "I'm a full-stack product engineer (8 YoE) that builds and launches AI-powered web & mobile products. I'm reaching out about project or overflow work.") {
		t.Fatalf("body = %q", body)
	}
	if !strings.Contains(body, "Are you the right person to ask about this kind of project support?") {
		t.Fatalf("body = %q", body)
	}
}

func TestAgencyDraftUsesWebsiteAgencyPitch(t *testing.T) {
	lead := Lead{
		Name:                  "Quinn Owner",
		FirstName:             "Quinn",
		Company:               strPtr("QeWebby - WordPress Development Agency"),
		AgencyAccountName:     strPtr("QeWebby - WordPress Development Agency"),
		AgencyAccountReasons:  []string{"website/wordpress build account signal"},
		AgencyAccountEvidence: "WordPress agency crafting high-performing websites with web designer and WordPress developer services",
		LeadType:              LeadTypeAgencyFounder,
	}
	body := agencyDraft(lead)
	if !strings.Contains(body, "I came across QeWebby - WordPress Development Agency, and I'm reaching out about project or overflow work.") || !strings.Contains(body, "Comfortable collaborating with design and product teams.") {
		t.Fatalf("body = %q", body)
	}
	if !strings.Contains(draftAngle(lead), "web design/WordPress agency") {
		t.Fatalf("angle = %q", draftAngle(lead))
	}
}

func TestDashboardSeparatesAgencyAndRecruiterBuckets(t *testing.T) {
	state := OutreachState{Leads: []Lead{
		{
			ID:                    "agency_1",
			Name:                  "Dana Delivery",
			FirstName:             "Dana",
			LeadType:              LeadTypeAgencyDelivery,
			Status:                LeadStatusEligible,
			MessageStatus:         MessageStatusDryRunReady,
			FitScore:              91,
			FitReasons:            []string{"agency delivery/technical leadership title"},
			AgencyAccountID:       strPtr("acct_bright"),
			AgencyAccountName:     strPtr("Bright Product Studio"),
			AgencyAccountURL:      strPtr("https://www.linkedin.com/sales/company/12345"),
			AgencyAccountReasons:  []string{"software/product delivery account signal"},
			AgencyAccountEvidence: "Bright Product Studio Software Development",
			Draft:                 &MessageDraft{Body: "Agency draft", Angle: "agency delivery", Evidence: []string{"Title: Head of Delivery", "Agency account: Bright Product Studio"}},
		},
		{
			ID:            "recruiter_1",
			Name:          "Riley Recruiter",
			FirstName:     "Riley",
			LeadType:      LeadTypeContractRecruiter,
			Status:        LeadStatusEligible,
			MessageStatus: MessageStatusDryRunReady,
			FitScore:      93,
			FitReasons:    []string{"recruiter/staffing signal"},
			Draft:         &MessageDraft{Body: "Recruiter draft", Angle: "contract recruiter", Evidence: []string{"Title: Technical Recruiter"}},
		},
	}, AgencyAccounts: []AgencyAccount{{
		ID:     "acct_bright",
		Name:   "Bright Product Studio",
		Status: AgencyAccountStatusQualified,
	}}}
	report := BuildDashboardReport(state, "/tmp/outreach.json", 5, 5, true, nil)
	if len(report.ReadyAgencies) != 1 || report.ReadyAgencies[0].ID != "agency_1" {
		t.Fatalf("ready agencies = %#v", report.ReadyAgencies)
	}
	if len(report.ReadyRecruiters) != 1 || report.ReadyRecruiters[0].ID != "recruiter_1" {
		t.Fatalf("ready recruiters = %#v", report.ReadyRecruiters)
	}
	if report.ReadyCounts.Agencies != 1 || report.ReadyCounts.Recruiters != 1 {
		t.Fatalf("ready counts = %#v", report.ReadyCounts)
	}
	markdown := RenderDashboardMarkdown(report)
	if !strings.Contains(markdown, "## Agencies") || !strings.Contains(markdown, "## Recruiters") || !strings.Contains(markdown, "- Draft evidence:") || !strings.Contains(markdown, "Agency account: Bright Product Studio") || !strings.Contains(markdown, "Agency accounts: `1` qualified") || !strings.Contains(markdown, "- Ready now: `1` agencies, `1` recruiters") {
		t.Fatalf("markdown = %s", markdown)
	}
}

func TestDailySendCompletionCountsThisRunActions(t *testing.T) {
	state := OutreachState{Leads: []Lead{
		{
			ID:              "old_sent",
			Name:            "Old Sent",
			LeadType:        LeadTypeAgencyDelivery,
			Status:          LeadStatusEligible,
			MessageStatus:   MessageStatusSent,
			FitScore:        99,
			AgencyAccountID: strPtr("acct_active"),
		},
		{
			ID:              "ready_now",
			Name:            "Ready Now",
			LeadType:        LeadTypeAgencyDelivery,
			Status:          LeadStatusEligible,
			MessageStatus:   MessageStatusDryRunReady,
			FitScore:        95,
			AgencyAccountID: strPtr("acct_active"),
		},
	}, AgencyAccounts: []AgencyAccount{{
		ID:     "acct_active",
		Name:   "Active Studio",
		Status: AgencyAccountStatusQualified,
	}}}
	if bucketCompleteForRun(state, "agency", 1, true, nil) {
		t.Fatal("persisted sent lead should not satisfy a real-send daily quota")
	}
	actions := []DailyLeadAction{{
		Bucket: "agency",
		Result: "sent-clicked",
	}}
	if !bucketCompleteForRun(state, "agency", 1, true, actions) {
		t.Fatal("current-run sent action should satisfy a real-send daily quota")
	}
	if !bucketCompleteForRun(state, "agency", 1, false, nil) {
		t.Fatal("ready lead should satisfy a draft/validation daily quota")
	}
	if got := readyLeads(state, "agency"); len(got) != 1 || got[0].ID != "ready_now" {
		t.Fatalf("ready leads = %#v", got)
	}
	state.Leads = append(state.Leads, Lead{
		ID:              "approved",
		Name:            "Approved",
		LeadType:        LeadTypeAgencyDelivery,
		Status:          LeadStatusEligible,
		MessageStatus:   MessageStatusApproved,
		FitScore:        100,
		AgencyAccountID: strPtr("acct_active"),
	})
	if got := readyLeads(state, "agency"); len(got) != 1 || got[0].ID != "ready_now" {
		t.Fatalf("approved lead should not replace messageable send candidate: %#v", got)
	}
}

func TestDashboardShowsThisRunAndLifetimeCountsSeparately(t *testing.T) {
	state := OutreachState{Leads: []Lead{
		{
			ID:              "old_sent",
			Name:            "Old Sent",
			LeadType:        LeadTypeAgencyFounder,
			Status:          LeadStatusEligible,
			MessageStatus:   MessageStatusSent,
			FitScore:        99,
			AgencyAccountID: strPtr("acct_active"),
		},
		{
			ID:            "drafted_recruiter",
			Name:          "Drafted Recruiter",
			LeadType:      LeadTypeContractRecruiter,
			Status:        LeadStatusEligible,
			MessageStatus: MessageStatusDrafted,
			FitScore:      95,
			ProfileURL:    strPtr("https://linkedin.com/sales/lead/drafted"),
			Draft:         &MessageDraft{Body: "body"},
		},
	}, AgencyAccounts: []AgencyAccount{{
		ID:     "acct_active",
		Name:   "Active Studio",
		Status: AgencyAccountStatusQualified,
	}}}
	actions := []DailyLeadAction{
		{Bucket: "agency", Result: "sent-clicked"},
		{Bucket: "recruiter", Result: "conversation-exists"},
	}
	report := BuildDashboardReport(state, "/tmp/outreach.sqlite", 5, 5, true, actions)
	if report.RunCounts.Sent.Agencies != 1 || report.RunCounts.ConversationExists.Recruiters != 1 {
		t.Fatalf("run counts = %#v", report.RunCounts)
	}
	if report.LifetimeCounts.Agencies != 1 || report.BacklogCounts.Recruiters != 1 {
		t.Fatalf("lifetime/backlog counts = %#v %#v", report.LifetimeCounts, report.BacklogCounts)
	}
	markdown := RenderDashboardMarkdown(report)
	for _, want := range []string{
		"## Sourcing Readiness",
		"## Send Results",
		"- This-run sent: `1` agencies, `0` recruiters",
		"conversation_exists `0` agencies, `1` recruiters",
		"- Backlog drafted/needs validation: `0` agencies, `1` recruiters",
		"- Lifetime sent: `1` agencies, `0` recruiters",
	} {
		if !strings.Contains(markdown, want) {
			t.Fatalf("markdown missing %q:\n%s", want, markdown)
		}
	}
}

func TestDashboardRenderModeCallsOutNoRunAndLatestRun(t *testing.T) {
	started := time.Date(2026, time.June, 23, 12, 0, 0, 0, time.UTC)
	completed := started.Add(2 * time.Minute)
	state := OutreachState{
		RunEvents: []RunEvent{
			{At: started, RunID: "daily-1", Phase: "run-start", Command: "run-daily", StartedAt: started, TargetAgencies: 5, TargetRecruiters: 5, AllowSend: true, DashboardPath: "/tmp/run.md", StatePath: "/tmp/state.sqlite"},
			{At: started.Add(time.Minute), RunID: "daily-1", Phase: "send-message", Bucket: "recruiter", LeadID: "lead_1", Name: "Riley", Result: "sent-clicked"},
			{At: completed, RunID: "daily-1", Phase: "run-finish", Command: "run-daily", Result: "completed", StartedAt: started, CompletedAt: completed, TargetAgencies: 5, TargetRecruiters: 5, AllowSend: true, DashboardPath: "/tmp/run.md", StatePath: "/tmp/state.sqlite"},
		},
	}
	report := BuildDashboardReportWithOptions(state, "/tmp/state.sqlite", DashboardBuildOptions{
		Mode:             "render",
		DashboardPath:    "/tmp/latest-render.md",
		TargetAgencies:   5,
		TargetRecruiters: 5,
		AllowSend:        false,
		IncludeLatestRun: true,
	})
	markdown := RenderDashboardMarkdown(report)
	for _, want := range []string{
		"Dashboard render only; no send run executed.",
		"## Latest Run",
		"Run ID: `daily-1`",
		"Sent: `0` agencies, `1` recruiters",
		"Agency ready-to-send pool is short by 5 for this render target.",
		"The remaining send goal is shown under Recommended Next Run.",
	} {
		if !strings.Contains(markdown, want) {
			t.Fatalf("markdown missing %q:\n%s", want, markdown)
		}
	}
}

func TestLatestRunSummaryAndRecommendationPreferAgencyRetry(t *testing.T) {
	started := time.Date(2026, time.June, 23, 12, 0, 0, 0, time.UTC)
	state := OutreachState{RunEvents: []RunEvent{
		{At: started, RunID: "daily-2", Phase: "run-start", Command: "send-ready", StartedAt: started, TargetAgencies: 5, TargetRecruiters: 5, AllowSend: true, DashboardPath: "/tmp/run.md", StatePath: "/tmp/state.sqlite"},
		{At: started.Add(time.Second), RunID: "daily-2", Phase: "send-message", Bucket: "recruiter", LeadID: "lead_1", Name: "Riley", Result: "sent-clicked"},
		{At: started.Add(2 * time.Second), RunID: "daily-2", Phase: "send-message", Bucket: "agency", LeadID: "lead_2", Name: "Dana", Result: "sent-clicked"},
		{At: started.Add(time.Minute), RunID: "daily-2", Phase: "run-finish", Command: "send-ready", Result: "completed", StartedAt: started, CompletedAt: started.Add(time.Minute), TargetAgencies: 5, TargetRecruiters: 5, AllowSend: true, DashboardPath: "/tmp/run.md", StatePath: "/tmp/state.sqlite"},
	}}
	summary, ok := LatestRunSummary(state, "/tmp/state.sqlite")
	if !ok {
		t.Fatal("missing summary")
	}
	if summary.Counts.Sent.Agencies != 1 || summary.Counts.Sent.Recruiters != 1 {
		t.Fatalf("counts = %#v", summary.Counts.Sent)
	}
	if !summary.Recommendation.ShouldRetry || !strings.Contains(summary.Recommendation.Command, "send-ready --session auto --target-agencies 4 --target-recruiters 0 --allow-send") {
		t.Fatalf("recommendation = %#v", summary.Recommendation)
	}
	if strings.Contains(summary.Recommendation.Command, "run-daily") || strings.Contains(summary.Recommendation.Command, "--refresh-saved-searches") {
		t.Fatalf("send retry should not source = %#v", summary.Recommendation)
	}
}

func TestRunDailyRejectsAllowSend(t *testing.T) {
	store := Store{Dir: t.TempDir()}
	_, err := RunDaily(&store, DailyOptions{Session: "auto", AllowSend: true})
	if err == nil || !strings.Contains(err.Error(), "run-daily is sourcing-only") {
		t.Fatalf("err = %v", err)
	}
}

func TestLatestRunSummaryFallsBackToLegacyRunEvents(t *testing.T) {
	at := time.Date(2026, time.June, 23, 12, 15, 0, 0, time.UTC)
	state := OutreachState{RunEvents: []RunEvent{
		{At: at, Phase: "send-message", Bucket: "recruiter", LeadID: "lead_1", Name: "Riley", Result: "sent-clicked"},
		{At: at.Add(time.Minute), Phase: "send-message", Bucket: "agency", LeadID: "lead_2", Name: "Dana", Result: "sent-clicked"},
	}}
	summary, ok := LatestRunSummary(state, "/tmp/state.sqlite")
	if !ok {
		t.Fatal("missing legacy summary")
	}
	if !strings.HasPrefix(summary.RunID, "legacy-") || summary.Counts.Sent.Recruiters != 1 || summary.Counts.Sent.Agencies != 1 {
		t.Fatalf("summary = %#v", summary)
	}
}

func TestQueueItemByLeadIDIncludesDraftOutsideQueueLimit(t *testing.T) {
	state := OutreachState{Leads: []Lead{
		{ID: "high", Name: "High", LeadType: LeadTypeContractRecruiter, Status: LeadStatusEligible, MessageStatus: MessageStatusDrafted, FitScore: 99},
		{ID: "target", Name: "Target", LeadType: LeadTypeAgencyDelivery, Status: LeadStatusEligible, MessageStatus: MessageStatusDrafted, FitScore: 10, Draft: &MessageDraft{Body: "draft body"}},
	}}
	items := Queue(state, []LeadStatus{LeadStatusEligible}, 1, true)
	if len(items) != 1 || items[0].ID != "high" {
		t.Fatalf("limited queue = %#v", items)
	}
	item, ok := QueueItemByLeadID(state, "target", true)
	if !ok || item.ID != "target" || item.Draft == nil || *item.Draft != "draft body" {
		t.Fatalf("direct queue item = %#v ok=%t", item, ok)
	}
}

func TestBuildLeadDetailIncludesDraftCandidateAndAttempts(t *testing.T) {
	now := time.Date(2026, time.June, 23, 20, 0, 0, 0, time.UTC)
	state := OutreachState{
		Leads: []Lead{{
			ID:              "lead_lorenzo",
			Name:            "Lorenzo Fernandez",
			LeadType:        LeadTypeAgencyDelivery,
			Status:          LeadStatusEligible,
			MessageStatus:   MessageStatusDrafted,
			FitScore:        88,
			ProfileURL:      strPtr("https://www.linkedin.com/in/lorenzo-fernandez-297017b/"),
			Title:           strPtr("Sales Engineering"),
			Company:         strPtr("Oktana"),
			AgencyAccountID: strPtr("acct_oktana"),
			Draft:           &MessageDraft{Subject: "Subject", Body: "Hi Lorenzo", Angle: "agency", GeneratedAt: now},
			SendAttempts:    []SendAttempt{{At: now, RunID: "run-1", DryRun: true, Status: "dry-run-messageable", OutPath: "/tmp/result.json"}},
		}},
		AgencyAccounts: []AgencyAccount{{
			ID:         "acct_oktana",
			Name:       "Oktana",
			Status:     AgencyAccountStatusQualified,
			AccountURL: strPtr("https://www.linkedin.com/sales/company/3880229"),
		}},
		AgencyContactCandidates: []AgencyContactCandidate{{
			ID:                "agc_lorenzo",
			AgencyAccountID:   "acct_oktana",
			AgencyAccountName: "Oktana",
			Source:            "website_enrichment",
			SourceURL:         strPtr("https://oktana.com/about"),
			Status:            AgencyContactCandidateStatusConverted,
			ReviewStatus:      AgencyContactReviewStatusConverted,
			PromotedLeadID:    strPtr("lead_lorenzo"),
		}},
	}
	detail, ok := BuildLeadDetail(state, "/tmp/outreach.sqlite", "lead_lorenzo")
	if !ok || detail.AgencyAccount == nil || detail.AgencyContactCandidate == nil || detail.Lead.Draft == nil || len(detail.Lead.SendAttempts) != 1 {
		t.Fatalf("detail = %#v ok=%t", detail, ok)
	}
	text := RenderLeadDetailText(detail)
	for _, want := range []string{"lead=lead_lorenzo", "agency_contact_candidate=agc_lorenzo", "candidate_source=website_enrichment", "body:\nHi Lorenzo", "send_attempts:"} {
		if !strings.Contains(text, want) {
			t.Fatalf("detail text missing %q:\n%s", want, text)
		}
	}
}

func TestDashboardIncludesSentAgencyLeadsFromExhaustedAccounts(t *testing.T) {
	state := OutreachState{
		Leads: []Lead{{
			ID:              "sent_agency",
			Name:            "Sent Agency",
			LeadType:        LeadTypeAgencyFounder,
			Status:          LeadStatusEligible,
			MessageStatus:   MessageStatusSent,
			FitScore:        90,
			AgencyAccountID: strPtr("acct_exhausted"),
		}},
		AgencyAccounts: []AgencyAccount{{
			ID:     "acct_exhausted",
			Name:   "Exhausted Studio",
			Status: AgencyAccountStatusExhausted,
		}, {
			ID:     "acct_empty",
			Name:   "Empty Studio",
			Status: AgencyAccountStatusExhausted,
		}},
	}
	report := BuildDashboardReport(state, "/tmp/outreach.sqlite", 5, 5, true, nil)
	if report.LifetimeCounts.Agencies != 1 || len(report.SentAgencies) != 1 {
		t.Fatalf("report = %#v", report)
	}
	if report.AgencyFunnelCounts.WithContacts != 1 || report.AgencyFunnelCounts.WithMessageableOrSentContacts != 1 || report.AgencyFunnelCounts.ExhaustedWithoutContacts != 1 {
		t.Fatalf("funnel = %#v", report.AgencyFunnelCounts)
	}
	markdown := RenderDashboardMarkdown(report)
	if !strings.Contains(markdown, "Agency contactability:") || !strings.Contains(markdown, "`1` with contacts") {
		t.Fatalf("markdown = %s", markdown)
	}
}

func TestAgencyDrilldownCountsContactSearchStages(t *testing.T) {
	state := OutreachState{
		AgencyAccounts: []AgencyAccount{
			{ID: "acct_new", Name: "New Studio", Status: AgencyAccountStatusQualified},
			{ID: "acct_founder", Name: "Founder Studio", Status: AgencyAccountStatusQualified, ContactCaptureCount: 1},
			{ID: "acct_exec", Name: "Exec Studio", Status: AgencyAccountStatusQualified, ContactCaptureCount: 2, LastContactError: strPtr("page closed")},
			{ID: "acct_resource", Name: "Resource Studio", Status: AgencyAccountStatusQualified, ContactCaptureCount: 3},
			{ID: "acct_exhausted", Name: "Exhausted Studio", Status: AgencyAccountStatusExhausted, ContactCaptureCount: 2},
		},
		Leads: []Lead{{
			ID:              "lead_contact",
			Name:            "Dana",
			Status:          LeadStatusEligible,
			MessageStatus:   MessageStatusDrafted,
			LeadType:        LeadTypeAgencyFounder,
			AgencyAccountID: strPtr("acct_founder"),
		}},
	}
	counts := agencyDrilldownCounts(state)
	if counts.NotSearchedYet != 1 || counts.SearchedFounderRecent != 1 || counts.SearchedExecutiveBroad != 1 || counts.SearchedResourceBroad != 1 || counts.ContactsFound != 1 || counts.BrowserErrorRetryable != 1 || counts.ExhaustedWithoutContact != 1 {
		t.Fatalf("drilldown = %#v", counts)
	}
}

func TestShouldStopForAgencyNoProgressUsesConfiguredThreshold(t *testing.T) {
	tests := []struct {
		name    string
		options DailyOptions
		streak  int
		want    bool
	}{
		{
			name:    "disabled",
			options: DailyOptions{StopWhenNoProgress: false, MaxNoProgressSearches: 2},
			streak:  2,
			want:    false,
		},
		{
			name:    "below threshold",
			options: DailyOptions{StopWhenNoProgress: true, MaxNoProgressSearches: 3},
			streak:  2,
			want:    false,
		},
		{
			name:    "at threshold",
			options: DailyOptions{StopWhenNoProgress: true, MaxNoProgressSearches: 3},
			streak:  3,
			want:    true,
		},
		{
			name:    "default threshold",
			options: DailyOptions{StopWhenNoProgress: true},
			streak:  12,
			want:    true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := shouldStopForAgencyNoProgress(tt.options, &dailyProgress{AgencyNoProgressStreak: tt.streak})
			if got != tt.want {
				t.Fatalf("shouldStopForAgencyNoProgress() = %t, want %t", got, tt.want)
			}
		})
	}
}

func TestBuildAgencyPoolDiagnosisIdentifiesWebsiteEnrichmentCandidates(t *testing.T) {
	state := OutreachState{
		AgencyAccounts: []AgencyAccount{
			{
				ID:                  "acct_open",
				Name:                "Open Studio",
				Status:              AgencyAccountStatusQualified,
				FitScore:            95,
				Website:             strPtr("https://open.example.com"),
				ContactCaptureCount: 1,
			},
			{
				ID:                  "acct_exhausted",
				Name:                "Exhausted Studio",
				Status:              AgencyAccountStatusExhausted,
				FitScore:            90,
				Website:             strPtr("https://exhausted.example.com"),
				ContactCaptureCount: 3,
			},
			{
				ID:                  "acct_website",
				Name:                "Website Studio",
				Status:              AgencyAccountStatusQualified,
				FitScore:            88,
				Website:             strPtr("https://website.example.com"),
				ContactCaptureCount: 3,
			},
			{
				ID:                  "acct_search",
				Name:                "Search Studio",
				Status:              AgencyAccountStatusQualified,
				FitScore:            80,
				Website:             strPtr("https://search.example.com"),
				ContactCaptureCount: 1,
			},
		},
		Leads: []Lead{{
			ID:              "lead_open",
			Name:            "Dana",
			Status:          LeadStatusEligible,
			MessageStatus:   MessageStatusDrafted,
			LeadType:        LeadTypeAgencyFounder,
			AgencyAccountID: strPtr("acct_open"),
		}},
	}
	diagnosis := BuildAgencyPoolDiagnosis(state, "/tmp/outreach.sqlite", 20)
	if diagnosis.WebsiteCandidates != 2 || diagnosis.QualifiedWebsiteCandidates != 1 || diagnosis.ExhaustedWebsiteCandidates != 1 {
		t.Fatalf("website candidate counts = %#v", diagnosis)
	}
	steps := map[string]string{}
	for _, account := range diagnosis.Accounts {
		steps[account.ID] = account.NextStep
	}
	if steps["acct_open"] != "validate_or_send_open_lead" {
		t.Fatalf("open account step = %q", steps["acct_open"])
	}
	if steps["acct_search"] != "continue_linkedin_contact_search:executive_delivery_broad" {
		t.Fatalf("search account step = %q", steps["acct_search"])
	}
	if steps["acct_website"] != "website_enrichment" {
		t.Fatalf("website account step = %q", steps["acct_website"])
	}
	if steps["acct_exhausted"] != "website_enrichment" {
		t.Fatalf("exhausted account step = %q", steps["acct_exhausted"])
	}
	text := RenderAgencyPoolDiagnosisText(diagnosis)
	if !strings.Contains(text, "website_candidates=all 2; qualified 1; exhausted 1") || !strings.Contains(text, "acct_website") {
		t.Fatalf("diagnosis text = %s", text)
	}
}

func TestBuildAgencyPoolNextActionPrioritizesDraftValidation(t *testing.T) {
	state := OutreachState{
		AgencyAccounts: []AgencyAccount{{
			ID:     "acct_oktana",
			Name:   "Oktana",
			Status: AgencyAccountStatusQualified,
		}},
		Leads: []Lead{{
			ID:              "lead_lorenzo",
			Name:            "Lorenzo Fernandez",
			Status:          LeadStatusEligible,
			MessageStatus:   MessageStatusDrafted,
			LeadType:        LeadTypeAgencyDelivery,
			FitScore:        88,
			ProfileURL:      strPtr("https://www.linkedin.com/in/lorenzo-fernandez-297017b/"),
			Draft:           &MessageDraft{Body: "body"},
			AgencyAccountID: strPtr("acct_oktana"),
		}},
		AgencyContactCandidates: []AgencyContactCandidate{{
			ID:                "agc_needs_review",
			AgencyAccountID:   "acct_oktana",
			AgencyAccountName: "Oktana",
			Status:            AgencyContactCandidateStatusWebsiteContactCandidate,
			ReviewStatus:      AgencyContactReviewStatusNeedsReview,
			ProfileURL:        strPtr("https://www.linkedin.com/in/other/"),
		}},
	}
	next := BuildAgencyPoolNextAction(state, "/tmp/outreach.sqlite")
	if next.Action != "validate_drafted_agency_lead" || next.Lead == nil || next.Lead.ID != "lead_lorenzo" {
		t.Fatalf("next = %#v", next)
	}
	if !strings.Contains(next.Command, "send-message --lead-id lead_lorenzo --session auto") || strings.Contains(next.Command, "--allow-send") {
		t.Fatalf("command = %q", next.Command)
	}
	text := RenderAgencyPoolNextActionText(next)
	if !strings.Contains(text, "action=validate_drafted_agency_lead") || !strings.Contains(text, "lead=lead_lorenzo") {
		t.Fatalf("text = %s", text)
	}
}

func TestImportAgencySourceCaptureCreatesReviewOnlyAccountsAndCandidates(t *testing.T) {
	capturedAt := "2026-06-23T12:00:00Z"
	sourceURL := "https://webflow.com/agencies/bright-studio"
	capture := AgencySourceCapture{
		Source:     "Webflow partners",
		SourceType: "webflow_partner",
		CapturedAt: &capturedAt,
		Rows: []AgencySourceRow{{
			Name:      "Bright Studio",
			Website:   strPtr("bright.example.com"),
			SourceURL: &sourceURL,
			Services:  []string{"Web Development"},
			Contacts: []AgencySourceContactRow{
				{Email: strPtr("hello@bright.example.com"), Evidence: []string{"directory mailto"}},
				{Name: strPtr("Jane Doe"), ProfileURL: strPtr("https://www.linkedin.com/in/jane-doe/?trk=directory")},
				{ContactURL: strPtr("https://bright.example.com/contact"), FormAction: strPtr("https://bright.example.com/contact")},
			},
		}},
	}
	state := OutreachState{}
	summary, err := ImportAgencySourceCapture(&state, capture)
	if err != nil {
		t.Fatal(err)
	}
	if summary.Stored != 1 || summary.Qualified != 1 || summary.ContactCandidatesStored != 3 {
		t.Fatalf("summary = %#v", summary)
	}
	if len(state.Leads) != 0 {
		t.Fatalf("source import should not create sendable leads: %#v", state.Leads)
	}
	if len(state.AgencyAccounts) != 1 || state.AgencyAccounts[0].Status != AgencyAccountStatusQualified {
		t.Fatalf("accounts = %#v", state.AgencyAccounts)
	}
	if state.AgencyAccounts[0].Domain == nil || *state.AgencyAccounts[0].Domain != "bright.example.com" {
		t.Fatalf("domain = %v", state.AgencyAccounts[0].Domain)
	}
	statuses := map[AgencyContactCandidateStatus]int{}
	for _, candidate := range state.AgencyContactCandidates {
		if candidate.ReviewStatus != AgencyContactReviewStatusNeedsReview {
			t.Fatalf("candidate should be review-only = %#v", candidate)
		}
		statuses[candidate.Status]++
	}
	if statuses[AgencyContactCandidateStatusGenericInbox] != 1 || statuses[AgencyContactCandidateStatusWebsiteContactCandidate] != 1 || statuses[AgencyContactCandidateStatusContactForm] != 1 {
		t.Fatalf("candidate statuses = %#v candidates=%#v", statuses, state.AgencyContactCandidates)
	}
	store := Store{Dir: t.TempDir()}
	if err := store.Save(state); err != nil {
		t.Fatal(err)
	}
	reloaded, err := store.Load()
	if err != nil {
		t.Fatal(err)
	}
	if len(reloaded.AgencyContactCandidates) != 3 {
		t.Fatalf("reloaded candidates = %#v", reloaded.AgencyContactCandidates)
	}
}

func TestAgencySourceCSVReplenishDedupeAndReport(t *testing.T) {
	dir := t.TempDir()
	csvPath := filepath.Join(dir, "reviewed-agencies.csv")
	csvBody := strings.Join([]string{
		"name,website,source_url,services,contact_name,contact_title,contact_profile_url",
		"Bright Studio,https://bright.example.com,https://directory.example.com/bright,Web Development|Custom API Integrations,Jane Doe,Founder,https://www.linkedin.com/in/jane-doe/",
		"Bright Studio LLC,https://bright.example.com,https://directory.example.com/bright-duplicate,Web Development,Jane Doe,Founder,https://www.linkedin.com/in/jane-doe/",
	}, "\n")
	if err := os.WriteFile(csvPath, []byte(csvBody), 0o644); err != nil {
		t.Fatal(err)
	}
	capture, err := LoadAgencySourceCSV(csvPath, AgencySourceCSVOptions{
		Source:     "Reviewed agency directory",
		SourceType: "manual_directory",
		CapturedAt: time.Date(2026, time.June, 23, 12, 0, 0, 0, time.UTC),
	})
	if err != nil {
		t.Fatal(err)
	}
	warnings := ValidateAgencySourceCapture(capture)
	if len(warnings) != 1 || !strings.Contains(warnings[0].Message, "duplicates row 1") {
		t.Fatalf("warnings = %#v", warnings)
	}
	store := Store{Dir: dir}
	artifactPath := filepath.Join(store.AgencySourceDir(), "reviewed-agencies.json")
	if err := WriteAgencySourceCapture(artifactPath, capture); err != nil {
		t.Fatal(err)
	}
	summary, err := ReplenishAgencyPool(context.Background(), &store, AgencySourceReplenishmentOptions{
		SourceDir:              store.AgencySourceDir(),
		ImportLimit:            5,
		WebsiteEnrichmentLimit: 0,
	})
	if err != nil {
		t.Fatal(err)
	}
	if summary.ImportedArtifacts != 1 || len(summary.ImportedSources) != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	if summary.ImportedSources[0].Stored != 1 || summary.ImportedSources[0].Updated != 1 || summary.ImportedSources[0].Qualified != 2 {
		t.Fatalf("import summary = %#v", summary.ImportedSources[0])
	}
	state, err := store.Load()
	if err != nil {
		t.Fatal(err)
	}
	if len(state.AgencyAccounts) != 1 || len(state.AgencyContactCandidates) != 1 {
		t.Fatalf("state accounts=%#v candidates=%#v", state.AgencyAccounts, state.AgencyContactCandidates)
	}
	reportPath := filepath.Join(dir, "source-report.json")
	report := BuildAgencySourceReport(state, store.StatePath(), reportPath)
	if report.Totals.Accounts != 1 || report.Totals.QualifiedAccounts != 1 || report.Totals.ContactCandidates != 1 || report.Totals.WebsiteContactCandidates != 1 {
		t.Fatalf("report = %#v", report)
	}
	if err := WriteAgencySourceReport(reportPath, report); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(reportPath); err != nil {
		t.Fatal(err)
	}
	text := RenderAgencySourceReportText(report)
	if !strings.Contains(text, "Reviewed agency directory") || !strings.Contains(text, "qualified") {
		t.Fatalf("report text = %s", text)
	}
}

func TestShopifyPartnerParserBuildsSourceRowsAndProfileWebsite(t *testing.T) {
	directoryNode, err := html.Parse(strings.NewReader(`<html><body>
		<a href="/partners/directory/partner/bright-studio"><img alt="Bright Studio"></a>
		<a href="/partners/directory/partner/bright-studio"><img alt="Bright Studio"></a>
	</body></html>`))
	if err != nil {
		t.Fatal(err)
	}
	rows := parseShopifyPartnerDirectoryRows(directoryNode)
	if len(rows) != 1 || rows[0].Name != "Bright Studio" || rows[0].SourceURL == nil || !strings.Contains(*rows[0].SourceURL, "/partners/directory/partner/bright-studio") {
		t.Fatalf("rows = %#v", rows)
	}
	profileNode, err := html.Parse(strings.NewReader(`<html><head>
		<meta property="og:title" content="Bright Studio">
		<meta name="description" content="Shopify Plus design and development partner.">
	</head><body>
		<a href="https://bright.example.com/?utm_source=sref">bright.example.com</a>
		<a href="https://customer.example.com/">View featured work</a>
		<a href="https://www.linkedin.com/company/shopify">LinkedIn</a>
	</body></html>`))
	if err != nil {
		t.Fatal(err)
	}
	profile := parseShopifyPartnerProfile(profileNode)
	if profile.Name != "Bright Studio" || profile.Description == "" || profile.Website != "https://bright.example.com/?utm_source=sref" {
		t.Fatalf("profile = %#v", profile)
	}
}

func TestAgencyContactCandidateRankingPrefersReviewedExecutives(t *testing.T) {
	candidates := []AgencyContactCandidate{
		{ID: "agc_generic", AgencyAccountID: "acct", AgencyAccountName: "Bright Studio", Status: AgencyContactCandidateStatusGenericInbox, ReviewStatus: AgencyContactReviewStatusNeedsReview, Email: strPtr("hello@example.com")},
		{ID: "agc_director", AgencyAccountID: "acct", AgencyAccountName: "Bright Studio", Status: AgencyContactCandidateStatusWebsiteContactCandidate, ReviewStatus: AgencyContactReviewStatusNeedsReview, Name: strPtr("Dana Director"), Title: strPtr("Director of Delivery"), ProfileURL: strPtr("https://www.linkedin.com/in/dana-director/")},
		{ID: "agc_founder", AgencyAccountID: "acct", AgencyAccountName: "Bright Studio", Status: AgencyContactCandidateStatusWebsiteContactCandidate, ReviewStatus: AgencyContactReviewStatusNeedsReview, Name: strPtr("Fran Founder"), Title: strPtr("Founder"), ProfileURL: strPtr("https://www.linkedin.com/in/fran-founder/")},
	}
	sortAgencyContactCandidates(candidates)
	if candidates[0].ID != "agc_founder" || agencyContactCandidateRank(candidates[0]) <= agencyContactCandidateRank(candidates[1]) {
		t.Fatalf("ranked candidates = %#v", candidates)
	}
	text := RenderAgencyContactCandidatesText(candidates)
	if !strings.Contains(text, "rank") || !strings.Contains(text, "Founder") {
		t.Fatalf("text = %s", text)
	}
}

func TestEnrichAgencyWebsitesExtractsExplicitLinksAndForms(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		switch r.URL.Path {
		case "/":
			fmt.Fprint(w, `<html><body><a href="mailto:info@example.com">info@example.com</a><form action="/newsletter"></form></body></html>`)
		case "/team":
			fmt.Fprint(w, `<html><body><a href="https://www.linkedin.com/in/jane-doe/?trk=team">Jane Doe</a></body></html>`)
		case "/contact":
			fmt.Fprint(w, `<html><body><form action="/contact"><input name="email"></form></body></html>`)
		default:
			fmt.Fprint(w, `<html><body></body></html>`)
		}
	}))
	defer server.Close()

	state := OutreachState{AgencyAccounts: []AgencyAccount{{
		ID:      "acct_bright",
		Name:    "Bright Studio",
		Source:  "LinkedIn account search",
		Status:  AgencyAccountStatusQualified,
		Website: strPtr(server.URL),
	}}}
	summary := EnrichAgencyWebsites(context.Background(), &state, AgencyWebsiteEnrichmentOptions{
		Limit:  1,
		Client: server.Client(),
		Now:    time.Date(2026, time.June, 23, 12, 0, 0, 0, time.UTC),
	})
	if summary.Checked != 1 || summary.ContactCandidatesStored != 3 || summary.Errors != 0 {
		t.Fatalf("summary = %#v candidates=%#v", summary, state.AgencyContactCandidates)
	}
	if state.AgencyAccounts[0].WebsiteEnrichmentCount != 1 || state.AgencyAccounts[0].LastWebsiteEnrichedAt == nil || state.AgencyAccounts[0].LastWebsiteEnrichmentError != nil {
		t.Fatalf("account enrichment fields = %#v", state.AgencyAccounts[0])
	}
	statuses := map[AgencyContactCandidateStatus]int{}
	for _, candidate := range state.AgencyContactCandidates {
		statuses[candidate.Status]++
	}
	if statuses[AgencyContactCandidateStatusGenericInbox] != 1 || statuses[AgencyContactCandidateStatusWebsiteContactCandidate] != 1 || statuses[AgencyContactCandidateStatusContactForm] != 1 {
		t.Fatalf("candidate statuses = %#v candidates=%#v", statuses, state.AgencyContactCandidates)
	}
	repeat := EnrichAgencyWebsites(context.Background(), &state, AgencyWebsiteEnrichmentOptions{
		Limit:  1,
		Client: server.Client(),
		Now:    time.Date(2026, time.June, 23, 12, 5, 0, 0, time.UTC),
	})
	if repeat.Checked != 0 || repeat.Skipped != 1 {
		t.Fatalf("repeat summary = %#v", repeat)
	}
	forced := EnrichAgencyWebsites(context.Background(), &state, AgencyWebsiteEnrichmentOptions{
		Limit:  1,
		Force:  true,
		Client: server.Client(),
		Now:    time.Date(2026, time.June, 23, 12, 10, 0, 0, time.UTC),
	})
	if forced.Checked != 1 || forced.ContactCandidatesUpdated != 3 {
		t.Fatalf("forced summary = %#v", forced)
	}
}

func TestDashboardIncludesAgencyContactCandidateCounts(t *testing.T) {
	state := OutreachState{
		AgencyAccounts: []AgencyAccount{{
			ID:     "acct_bright",
			Name:   "Bright Studio",
			Source: "Webflow partners",
			Status: AgencyAccountStatusQualified,
		}},
		AgencyContactCandidates: []AgencyContactCandidate{
			{ID: "agc_email", AgencyAccountID: "acct_bright", AgencyAccountName: "Bright Studio", Source: "website_enrichment", Status: AgencyContactCandidateStatusGenericInbox, ReviewStatus: AgencyContactReviewStatusNeedsReview, Email: strPtr("info@example.com")},
			{ID: "agc_profile", AgencyAccountID: "acct_bright", AgencyAccountName: "Bright Studio", Source: "website_enrichment", Status: AgencyContactCandidateStatusWebsiteContactCandidate, ReviewStatus: AgencyContactReviewStatusApproved, ProfileURL: strPtr("https://www.linkedin.com/in/jane-doe/")},
			{ID: "agc_form", AgencyAccountID: "acct_bright", AgencyAccountName: "Bright Studio", Source: "Webflow partners", Status: AgencyContactCandidateStatusContactForm, ReviewStatus: AgencyContactReviewStatusNeedsReview, ContactURL: strPtr("https://bright.example.com/contact")},
		},
	}
	report := BuildDashboardReport(state, "/tmp/outreach.sqlite", 5, 5, true, nil)
	if report.Counts.ByAgencyContactCandidateStatus[AgencyContactCandidateStatusGenericInbox] != 1 || report.Counts.ByAgencyContactCandidateReviewStatus[AgencyContactReviewStatusNeedsReview] != 2 {
		t.Fatalf("counts = %#v", report.Counts)
	}
	if len(report.AgencySourceYields) != 2 {
		t.Fatalf("source yields = %#v", report.AgencySourceYields)
	}
	markdown := RenderDashboardMarkdown(report)
	for _, want := range []string{
		"Agency review-only contacts: `1` website_contact_candidate, `1` generic_inbox, `1` contact_form",
		"Agency contact review: `2` needs_review, `1` approved",
		"Agency source yield:",
		"website_enrichment accounts q0/nr0/r0/ex0 contacts website_contact_candidate1/generic_inbox1/contact_form0",
	} {
		if !strings.Contains(markdown, want) {
			t.Fatalf("markdown missing %q:\n%s", want, markdown)
		}
	}
}

func TestReviewAndPromoteAgencyContactCandidateCreatesDraftedLead(t *testing.T) {
	state := OutreachState{
		AgencyAccounts: []AgencyAccount{{
			ID:           "acct_oktana",
			Name:         "Oktana",
			Source:       "ASAP - Agency Accounts Digital Agency",
			AccountURL:   strPtr("https://www.linkedin.com/sales/company/3880229"),
			Status:       AgencyAccountStatusQualified,
			FitScore:     80,
			FitReasons:   []string{"software/product delivery account signal"},
			EvidenceText: "Oktana custom software development and digital engineering",
		}},
		AgencyContactCandidates: []AgencyContactCandidate{{
			ID:                "agc_gaston",
			AgencyAccountID:   "acct_oktana",
			AgencyAccountName: "Oktana",
			Source:            "website_enrichment",
			SourceURL:         strPtr("https://oktana.com/about-us/"),
			Status:            AgencyContactCandidateStatusWebsiteContactCandidate,
			ReviewStatus:      AgencyContactReviewStatusNeedsReview,
			Name:              strPtr("Linkedin"),
			ProfileURL:        strPtr("https://www.linkedin.com/in/gaston-falco/"),
			Evidence:          []string{"explicit LinkedIn profile link on https://oktana.com/about-us/"},
		}},
	}
	reviewed, err := ReviewAgencyContactCandidate(&state, AgencyContactReviewOptions{
		CandidateID:  "agc_gaston",
		ReviewStatus: AgencyContactReviewStatusApproved,
		Name:         "Gaston Falco",
		Title:        "Practice Lead Manager",
		Note:         "Oktana leadership page lists this role.",
		Now:          time.Date(2026, time.June, 23, 20, 0, 0, 0, time.UTC),
	})
	if err != nil {
		t.Fatal(err)
	}
	if reviewed.ReviewStatus != AgencyContactReviewStatusApproved || reviewed.Name == nil || *reviewed.Name != "Gaston Falco" || reviewed.Title == nil || *reviewed.Title != "Practice Lead Manager" {
		t.Fatalf("reviewed = %#v", reviewed)
	}
	summary, err := PromoteAgencyContactCandidates(&state, AgencyContactPromotionOptions{
		CandidateIDs: []string{"agc_gaston"},
		Draft:        true,
		Now:          time.Date(2026, time.June, 23, 20, 1, 0, 0, time.UTC),
	})
	if err != nil {
		t.Fatal(err)
	}
	if summary.Stored != 1 || summary.Updated != 0 || summary.Drafted != 1 || len(summary.Skipped) != 0 || len(summary.Leads) != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	lead := summary.Leads[0]
	if lead.Name != "Gaston Falco" || lead.LeadType != LeadTypeAgencyDelivery || lead.MessageStatus != MessageStatusDrafted || lead.Draft == nil {
		t.Fatalf("lead = %#v", lead)
	}
	if lead.AgencyAccountName == nil || *lead.AgencyAccountName != "Oktana" || lead.AgencyAccountURL == nil {
		t.Fatalf("agency context = %#v", lead)
	}
	if !strings.Contains(lead.EvidenceText, "Agency contact candidate: agc_gaston") || !strings.Contains(strings.Join(lead.FitReasons, "\n"), "reviewed website contact candidate") {
		t.Fatalf("lead evidence/reasons = %#v %#v", lead.EvidenceText, lead.FitReasons)
	}
	if !strings.Contains(lead.Draft.Body, "Are you the right person to ask about this kind of project support?") {
		t.Fatalf("draft = %q", lead.Draft.Body)
	}
	candidateIndex := findAgencyContactCandidateByID(state.AgencyContactCandidates, "agc_gaston")
	if candidateIndex < 0 {
		t.Fatal("candidate missing")
	}
	candidate := state.AgencyContactCandidates[candidateIndex]
	if candidate.ReviewStatus != AgencyContactReviewStatusConverted || candidate.Status != AgencyContactCandidateStatusConverted || candidate.PromotedLeadID == nil || *candidate.PromotedLeadID != lead.ID {
		t.Fatalf("candidate = %#v", candidate)
	}
}

func TestPromoteAgencyContactCandidatesLimitsActiveLeadsPerAgency(t *testing.T) {
	state := OutreachState{
		AgencyAccounts: []AgencyAccount{{
			ID:         "acct_oktana",
			Name:       "Oktana",
			Status:     AgencyAccountStatusQualified,
			AccountURL: strPtr("https://www.linkedin.com/sales/company/3880229"),
		}},
		AgencyContactCandidates: []AgencyContactCandidate{
			{
				ID:                "agc_lorenzo",
				AgencyAccountID:   "acct_oktana",
				AgencyAccountName: "Oktana",
				Source:            "website_enrichment",
				SourceURL:         strPtr("https://oktana.com/about-us/"),
				Status:            AgencyContactCandidateStatusWebsiteContactCandidate,
				ReviewStatus:      AgencyContactReviewStatusApproved,
				Name:              strPtr("Lorenzo Fernandez"),
				Title:             strPtr("Sales Engineering"),
				ProfileURL:        strPtr("https://www.linkedin.com/in/lorenzo-fernandez-297017b/"),
			},
			{
				ID:                "agc_gaston",
				AgencyAccountID:   "acct_oktana",
				AgencyAccountName: "Oktana",
				Source:            "website_enrichment",
				SourceURL:         strPtr("https://oktana.com/about-us/"),
				Status:            AgencyContactCandidateStatusWebsiteContactCandidate,
				ReviewStatus:      AgencyContactReviewStatusApproved,
				Name:              strPtr("Gaston Falco"),
				Title:             strPtr("Practice Lead Manager"),
				ProfileURL:        strPtr("https://www.linkedin.com/in/gaston-falco/"),
			},
		},
	}
	summary, err := PromoteAgencyContactCandidates(&state, AgencyContactPromotionOptions{
		Limit: 10,
		Draft: true,
		Now:   time.Date(2026, time.June, 23, 20, 1, 0, 0, time.UTC),
	})
	if err != nil {
		t.Fatal(err)
	}
	if summary.Stored != 1 || summary.Drafted != 1 || len(summary.Leads) != 1 || len(summary.Skipped) != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	if !strings.Contains(summary.Skipped[0].Reason, "max per agency is 1") || !strings.Contains(summary.Skipped[0].Reason, "active lead(s):") || !strings.Contains(summary.Skipped[0].Reason, summary.Leads[0].ID) {
		t.Fatalf("skip reason = %#v", summary.Skipped)
	}
	converted := 0
	approved := 0
	for _, candidate := range state.AgencyContactCandidates {
		switch candidate.ReviewStatus {
		case AgencyContactReviewStatusConverted:
			converted++
		case AgencyContactReviewStatusApproved:
			approved++
		}
	}
	if converted != 1 || approved != 1 {
		t.Fatalf("candidate statuses converted=%d approved=%d candidates=%#v", converted, approved, state.AgencyContactCandidates)
	}

	remainingID := summary.Skipped[0].CandidateID
	overrideSummary, err := PromoteAgencyContactCandidates(&state, AgencyContactPromotionOptions{
		CandidateIDs:           []string{remainingID},
		Draft:                  true,
		AllowMultiplePerAgency: true,
		Now:                    time.Date(2026, time.June, 23, 20, 2, 0, 0, time.UTC),
	})
	if err != nil {
		t.Fatal(err)
	}
	if overrideSummary.Stored != 1 || overrideSummary.Drafted != 1 || len(overrideSummary.Skipped) != 0 || len(state.Leads) != 2 {
		t.Fatalf("override summary = %#v leads=%#v", overrideSummary, state.Leads)
	}
}

func TestPromoteAgencyContactCandidatesSkipsUnsafeCandidates(t *testing.T) {
	state := OutreachState{
		AgencyAccounts: []AgencyAccount{{
			ID:     "acct_bright",
			Name:   "Bright Studio",
			Status: AgencyAccountStatusQualified,
		}},
		AgencyContactCandidates: []AgencyContactCandidate{
			{
				ID:                "agc_unapproved",
				AgencyAccountID:   "acct_bright",
				AgencyAccountName: "Bright Studio",
				Source:            "website_enrichment",
				Status:            AgencyContactCandidateStatusWebsiteContactCandidate,
				ReviewStatus:      AgencyContactReviewStatusNeedsReview,
				Name:              strPtr("Dana Delivery"),
				Title:             strPtr("Head of Delivery"),
				ProfileURL:        strPtr("https://www.linkedin.com/in/dana-delivery/"),
			},
			{
				ID:                "agc_inbox",
				AgencyAccountID:   "acct_bright",
				AgencyAccountName: "Bright Studio",
				Source:            "website_enrichment",
				Status:            AgencyContactCandidateStatusGenericInbox,
				ReviewStatus:      AgencyContactReviewStatusApproved,
				Email:             strPtr("hello@bright.example.com"),
			},
			{
				ID:                "agc_placeholder",
				AgencyAccountID:   "acct_bright",
				AgencyAccountName: "Bright Studio",
				Source:            "website_enrichment",
				Status:            AgencyContactCandidateStatusWebsiteContactCandidate,
				ReviewStatus:      AgencyContactReviewStatusApproved,
				Name:              strPtr("Linkedin"),
				Title:             strPtr("Head of Delivery"),
				ProfileURL:        strPtr("https://www.linkedin.com/in/placeholder/"),
			},
		},
	}
	summary, err := PromoteAgencyContactCandidates(&state, AgencyContactPromotionOptions{Limit: 10})
	if err != nil {
		t.Fatal(err)
	}
	if summary.Stored != 0 || summary.Updated != 0 || len(summary.Leads) != 0 || len(summary.Skipped) != 2 {
		t.Fatalf("summary = %#v", summary)
	}
	reasons := strings.Join([]string{summary.Skipped[0].Reason, summary.Skipped[1].Reason}, "\n")
	if !strings.Contains(reasons, "only personal LinkedIn profile candidates can be promoted") || !strings.Contains(reasons, "candidate needs a reviewed person name") {
		t.Fatalf("skips = %#v", summary.Skipped)
	}
	if len(state.Leads) != 0 {
		t.Fatalf("leads = %#v", state.Leads)
	}
}

func TestLeadsForMessageValidationOnlyReturnsDraftableStatuses(t *testing.T) {
	state := OutreachState{Leads: []Lead{
		{ID: "drafted", Name: "Drafted", LeadType: LeadTypeContractRecruiter, Status: LeadStatusEligible, MessageStatus: MessageStatusDrafted, FitScore: 90, ProfileURL: strPtr("https://linkedin.com/sales/lead/a"), Draft: &MessageDraft{Body: "body"}},
		{ID: "failed", Name: "Failed", LeadType: LeadTypeContractRecruiter, Status: LeadStatusEligible, MessageStatus: MessageStatusSendFailed, FitScore: 95, ProfileURL: strPtr("https://linkedin.com/sales/lead/b"), Draft: &MessageDraft{Body: "body"}},
		{ID: "ready", Name: "Ready", LeadType: LeadTypeContractRecruiter, Status: LeadStatusEligible, MessageStatus: MessageStatusDryRunReady, FitScore: 99, ProfileURL: strPtr("https://linkedin.com/sales/lead/c"), Draft: &MessageDraft{Body: "body"}},
		{ID: "thread", Name: "Thread", LeadType: LeadTypeContractRecruiter, Status: LeadStatusEligible, MessageStatus: MessageStatusConversationExists, FitScore: 100, ProfileURL: strPtr("https://linkedin.com/sales/lead/d"), Draft: &MessageDraft{Body: "body"}},
		{ID: "agency", Name: "Agency", LeadType: LeadTypeAgencyDelivery, Status: LeadStatusEligible, MessageStatus: MessageStatusDrafted, FitScore: 100, ProfileURL: strPtr("https://linkedin.com/sales/lead/e"), Draft: &MessageDraft{Body: "body"}},
	}}
	leads := leadsForMessageValidation(state, "recruiter")
	if len(leads) != 1 {
		t.Fatalf("leads = %#v", leads)
	}
	if leads[0].ID != "drafted" {
		t.Fatalf("validation order = %#v", leads)
	}
}

func TestDraftMessagesDoesNotResetDryRunReadyLeads(t *testing.T) {
	state := OutreachState{Leads: []Lead{
		{
			ID:            "ready",
			Name:          "Ready",
			FirstName:     "Ready",
			LeadType:      LeadTypeAgencyFounder,
			Status:        LeadStatusEligible,
			MessageStatus: MessageStatusDryRunReady,
			FitScore:      95,
			Draft:         &MessageDraft{Body: "approved body"},
		},
		{
			ID:            "new",
			Name:          "New",
			FirstName:     "New",
			LeadType:      LeadTypeAgencyFounder,
			Status:        LeadStatusEligible,
			MessageStatus: MessageStatusNone,
			FitScore:      90,
		},
		{
			ID:            "failed",
			Name:          "Failed",
			FirstName:     "Failed",
			LeadType:      LeadTypeAgencyFounder,
			Status:        LeadStatusEligible,
			MessageStatus: MessageStatusSendFailed,
			FitScore:      85,
			Draft:         &MessageDraft{Body: "failed body"},
		},
		{
			ID:            "approved",
			Name:          "Approved",
			FirstName:     "Approved",
			LeadType:      LeadTypeAgencyFounder,
			Status:        LeadStatusEligible,
			MessageStatus: MessageStatusApproved,
			FitScore:      80,
			Draft:         &MessageDraft{Body: "approved body"},
		},
		{
			ID:            "needs_edit",
			Name:          "Needs Edit",
			FirstName:     "Needs",
			LeadType:      LeadTypeAgencyFounder,
			Status:        LeadStatusEligible,
			MessageStatus: MessageStatusNeedsEdit,
			FitScore:      75,
			Draft:         &MessageDraft{Body: "needs edit body"},
		},
	}}
	report := DraftMessages(&state, 0)
	if len(report.Items) != 1 || report.Items[0].ID != "new" {
		t.Fatalf("drafted items = %#v", report.Items)
	}
	if state.Leads[0].MessageStatus != MessageStatusDryRunReady {
		t.Fatalf("ready lead status = %q", state.Leads[0].MessageStatus)
	}
	if state.Leads[0].Draft == nil || state.Leads[0].Draft.Body != "approved body" {
		t.Fatalf("ready lead draft = %#v", state.Leads[0].Draft)
	}
	failedIndex := findLeadByID(state.Leads, "failed")
	if failedIndex < 0 {
		t.Fatal("failed lead missing")
	}
	if state.Leads[failedIndex].MessageStatus != MessageStatusSendFailed {
		t.Fatalf("failed lead status = %q", state.Leads[failedIndex].MessageStatus)
	}
	for _, id := range []string{"approved", "needs_edit"} {
		index := findLeadByID(state.Leads, id)
		if index < 0 {
			t.Fatalf("%s lead missing", id)
		}
		if state.Leads[index].MessageStatus == MessageStatusDrafted {
			t.Fatalf("%s lead was redrafted", id)
		}
	}
}

func TestRenderDraftMarkdownPreservesDraftWhitespace(t *testing.T) {
	body := "Hi Lead,\n\nLine two\nLine three"
	report := DraftReport{Items: []QueueItem{{
		ID:     "lead",
		Name:   "Lead",
		Draft:  &body,
		Status: LeadStatusEligible,
	}}}
	markdown := RenderDraftMarkdown(report)
	for _, want := range []string{"> Hi Lead,", ">", "> Line two", "> Line three"} {
		if !strings.Contains(markdown, want) {
			t.Fatalf("markdown missing %q:\n%s", want, markdown)
		}
	}
}

func TestSendMessageRequiresDryRunReadyForRealSend(t *testing.T) {
	store := Store{Dir: t.TempDir()}
	state := OutreachState{Leads: []Lead{{
		ID:            "lead",
		Name:          "Lead",
		LeadType:      LeadTypeContractRecruiter,
		Status:        LeadStatusEligible,
		MessageStatus: MessageStatusDrafted,
		ProfileURL:    strPtr("https://linkedin.com/sales/lead/lead"),
		Draft: &MessageDraft{
			Subject: "Subject",
			Body:    "Body",
		},
	}}}
	if err := store.Save(state); err != nil {
		t.Fatal(err)
	}
	err := SendMessage(&store, SendMessageOptions{
		LeadID:    "lead",
		Session:   "1",
		Script:    "/tmp/send.js",
		AllowSend: true,
		OutDir:    t.TempDir(),
	})
	if err == nil || !strings.Contains(err.Error(), "real sends require dry_run_ready") {
		t.Fatalf("SendMessage error = %v", err)
	}
}

func TestReviewUIUpdatesDraftAndApproval(t *testing.T) {
	store := Store{Dir: t.TempDir()}
	state := OutreachState{Leads: []Lead{{
		ID:            "lead",
		Name:          "Lead",
		FirstName:     "Lead",
		LeadType:      LeadTypeContractRecruiter,
		Status:        LeadStatusEligible,
		MessageStatus: MessageStatusDryRunReady,
		ProfileURL:    strPtr("https://linkedin.com/sales/lead/lead"),
		Draft: &MessageDraft{
			Subject: "Old subject",
			Body:    "Old body",
			Angle:   "contract recruiter routing for remote C2C/1099 product-engineering work",
		},
	}}}
	if err := store.Save(state); err != nil {
		t.Fatal(err)
	}
	server, err := newReviewServer(&store)
	if err != nil {
		t.Fatal(err)
	}
	handler := server.routes()

	form := url.Values{}
	form.Set("subject", "New subject")
	form.Set("body", "Line one\n\nLine two")
	req := httptest.NewRequest(http.MethodPost, "/leads/lead/draft", strings.NewReader(form.Encode()))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusSeeOther {
		t.Fatalf("draft save status = %d body=%s", rec.Code, rec.Body.String())
	}
	loaded, err := store.Load()
	if err != nil {
		t.Fatal(err)
	}
	lead := loaded.Leads[findLeadByID(loaded.Leads, "lead")]
	if lead.MessageStatus != MessageStatusDrafted || lead.Draft == nil || lead.Draft.Subject != "New subject" || lead.Draft.Body != "Line one\n\nLine two" {
		t.Fatalf("saved lead = %#v", lead)
	}

	form = url.Values{}
	form.Set("status", string(MessageStatusApproved))
	req = httptest.NewRequest(http.MethodPost, "/leads/lead/status", strings.NewReader(form.Encode()))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusSeeOther {
		t.Fatalf("approval status = %d body=%s", rec.Code, rec.Body.String())
	}
	loaded, err = store.Load()
	if err != nil {
		t.Fatal(err)
	}
	lead = loaded.Leads[findLeadByID(loaded.Leads, "lead")]
	if lead.MessageStatus != MessageStatusApproved {
		t.Fatalf("message status = %q", lead.MessageStatus)
	}
}

func TestLatestAttemptIsBlankLeadPageFailure(t *testing.T) {
	store := Store{Dir: t.TempDir()}
	outPath := filepath.Join(store.Dir, "message-result.json")
	if err := os.WriteFile(outPath, []byte(`{"status":"identity-mismatch","body":""}`), 0o644); err != nil {
		t.Fatal(err)
	}
	state := OutreachState{Leads: []Lead{{
		ID:            "lead",
		Name:          "Lead",
		MessageStatus: MessageStatusSendFailed,
		SendAttempts: []SendAttempt{{
			Status:  "identity-mismatch",
			OutPath: outPath,
		}},
	}}}
	if err := store.Save(state); err != nil {
		t.Fatal(err)
	}
	if !latestAttemptIsBlankLeadPageFailure(&store, "lead") {
		t.Fatal("expected blank lead-page failure")
	}
}

func TestStoreImportsLegacyJSONAndPersistsSQLite(t *testing.T) {
	store := Store{Dir: t.TempDir()}
	state := OutreachState{
		SchemaVersion: 1,
		Leads: []Lead{{
			ID:            "lead",
			Name:          "Dana Delivery",
			FirstName:     "Dana",
			LeadType:      LeadTypeAgencyDelivery,
			Status:        LeadStatusEligible,
			MessageStatus: MessageStatusDrafted,
			Draft: &MessageDraft{
				Body:        "draft body",
				Angle:       "agency delivery",
				Evidence:    []string{"Title: Head of Delivery"},
				GeneratedAt: time.Date(2026, time.June, 22, 10, 0, 0, 0, time.UTC),
			},
			SendAttempts: []SendAttempt{{
				At:      time.Date(2026, time.June, 22, 10, 1, 0, 0, time.UTC),
				DryRun:  true,
				Status:  "dry-run-messageable",
				OutPath: "/tmp/result.json",
			}},
		}},
		CaptureCursors: map[string]CaptureCursor{
			"source": {Source: "source", RawRowCount: 3},
		},
		AgencyAccounts: []AgencyAccount{{
			ID:     "acct_bright",
			Name:   "Bright Studio",
			Source: "source",
			Status: AgencyAccountStatusQualified,
		}},
		AgencyContactCandidates: []AgencyContactCandidate{{
			ID:                "agc_email",
			AgencyAccountID:   "acct_bright",
			AgencyAccountName: "Bright Studio",
			Source:            "website_enrichment",
			Status:            AgencyContactCandidateStatusGenericInbox,
			ReviewStatus:      AgencyContactReviewStatusNeedsReview,
			Email:             strPtr("info@example.com"),
		}},
	}
	raw, err := json.Marshal(state)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(store.JSONStatePath(), raw, 0o644); err != nil {
		t.Fatal(err)
	}
	loaded, err := store.Load()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(store.DatabasePath()); err != nil {
		t.Fatalf("sqlite database was not created: %v", err)
	}
	if len(loaded.Leads) != 1 || loaded.Leads[0].Draft == nil || loaded.Leads[0].Draft.Body != "draft body" || len(loaded.Leads[0].SendAttempts) != 1 {
		t.Fatalf("loaded state = %#v", loaded)
	}
	if len(loaded.AgencyContactCandidates) != 1 || loaded.AgencyContactCandidates[0].Email == nil || *loaded.AgencyContactCandidates[0].Email != "info@example.com" {
		t.Fatalf("loaded candidates = %#v", loaded.AgencyContactCandidates)
	}
	loaded.Leads[0].Draft.Body = "updated draft"
	if err := store.Save(loaded); err != nil {
		t.Fatal(err)
	}
	reloaded, err := store.Load()
	if err != nil {
		t.Fatal(err)
	}
	if reloaded.Leads[0].Draft == nil || reloaded.Leads[0].Draft.Body != "updated draft" {
		t.Fatalf("reloaded state = %#v", reloaded)
	}
	if len(reloaded.AgencyContactCandidates) != 1 {
		t.Fatalf("reloaded candidates = %#v", reloaded.AgencyContactCandidates)
	}
}

func TestStoreOpenDBSetsBusyTimeout(t *testing.T) {
	store := Store{Dir: t.TempDir()}
	db, err := store.openDB()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var timeout int
	if err := db.QueryRow("PRAGMA busy_timeout").Scan(&timeout); err != nil {
		t.Fatal(err)
	}
	if timeout != sqliteBusyTimeoutMS {
		t.Fatalf("busy_timeout = %d, want %d", timeout, sqliteBusyTimeoutMS)
	}
}

func TestMessageSubjectByLeadType(t *testing.T) {
	recruiterSubject := "Full-Stack + AI Product Engineer | Open to Contract Work"
	if got := messageSubject(Lead{LeadType: LeadTypeContractRecruiter}); got != recruiterSubject {
		t.Fatalf("recruiter subject = %q", got)
	}
	agencySubject := "Full-Stack Product Engineer Available for Project Work"
	if got := messageSubject(Lead{LeadType: LeadTypeAgencyFounder}); got != agencySubject {
		t.Fatalf("agency subject = %q", got)
	}
}

func TestDefaultOutreachSourceURLUsesValidatedRecruiterFilters(t *testing.T) {
	got, ok := defaultOutreachSourceURL(RecruiterSource)
	if !ok {
		t.Fatal("recruiter source URL missing")
	}
	for _, want := range []string{
		"type%3ACURRENT_TITLE",
		"id%3A1711",
		"Contract%2520Recruiter",
		"id%3A16659",
		"Contract%2520Technical%2520Recruiter",
		"type%3APOSTED_ON_LINKEDIN",
		"id%3ARPOL",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("recruiter URL missing %q: %s", want, got)
		}
	}
}

func TestDefaultOutreachSourceURLUsesValidatedAgencyFilters(t *testing.T) {
	got, ok := defaultOutreachSourceURL(AgencySource)
	if !ok {
		t.Fatal("agency source URL missing")
	}
	for _, want := range []string{
		"type%3ACURRENT_TITLE",
		"id%3A35",
		"Founder",
		"id%3A154",
		"Managing%2520Partner",
		"type%3AINDUSTRY",
		"id%3A4",
		"Software%2520Development",
		"keywords%3Adigital%2520agency",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("agency URL missing %q: %s", want, got)
		}
	}
}

func TestDailyBucketsUseAccountFirstAgencySourcing(t *testing.T) {
	buckets := dailyBuckets(DailyOptions{TargetAgencies: 5, TargetRecruiters: 5})
	if len(buckets) == 0 || buckets[0].Name != "agency" {
		t.Fatalf("buckets = %#v", buckets)
	}
	if len(buckets[0].Sources) != 0 {
		t.Fatalf("agency people-search fallback sources = %#v", buckets[0].Sources)
	}
}

func TestNormalizeDailyOptionsAllowsZeroTargets(t *testing.T) {
	store := Store{Dir: t.TempDir()}
	options := normalizeDailyOptions(&store, DailyOptions{TargetAgencies: 0, TargetRecruiters: -1})
	if options.TargetAgencies != 0 || options.TargetRecruiters != 0 {
		t.Fatalf("targets = agencies:%d recruiters:%d", options.TargetAgencies, options.TargetRecruiters)
	}
}

func TestDefaultOutreachAccountSourceURLUsesAccountFilters(t *testing.T) {
	got, ok := defaultOutreachAccountSourceURL(AgencyAccountDevelopmentSource)
	if !ok {
		t.Fatal("account source URL missing")
	}
	for _, want := range []string{
		"/sales/search/company",
		"type%3AINDUSTRY",
		"id%3A4",
		"type%3ACOMPANY_HEADCOUNT",
		"id%3AC",
		"id%3AD",
		"keywords%3Acustom%2520software%2520development%2520agency",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("account URL missing %q: %s", want, got)
		}
	}
}

func TestAgencyAccountContactSearchURLUsesCurrentCompany(t *testing.T) {
	got, err := agencyAccountContactSearchURL(AgencyAccount{
		ID:         "acct_bright",
		Name:       "Bright Product Studio",
		AccountURL: strPtr("https://www.linkedin.com/sales/company/12345?_ntb=x"),
	})
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		"/sales/search/people",
		"type%3ACURRENT_COMPANY",
		"id%3A12345",
		"Bright%2520Product%2520Studio",
		"type%3ACURRENT_TITLE",
		"id%3A35",
		"type%3APOSTED_ON_LINKEDIN",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("contact URL missing %q: %s", want, got)
		}
	}
}

func TestAgencyAccountContactSearchStrategiesBroadenAfterFirstPass(t *testing.T) {
	account := AgencyAccount{
		ID:                  "acct_bright",
		Name:                "Bright Product Studio",
		AccountURL:          strPtr("https://www.linkedin.com/sales/company/12345?_ntb=x"),
		FitScore:            80,
		ContactCaptureCount: 1,
	}
	strategy, ok := nextAgencyContactSearchStrategy(account)
	if !ok {
		t.Fatal("missing second contact strategy")
	}
	got, err := agencyAccountContactSearchURLForStrategy(account, strategy)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		"/sales/search/people",
		"type%3ACURRENT_COMPANY",
		"id%3A12345",
		"keywords%3ACEO%2520President%2520Managing%2520Director",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("broadened URL missing %q: %s", want, got)
		}
	}
	if strings.Contains(got, "POSTED_ON_LINKEDIN") || strings.Contains(got, "RPOL") {
		t.Fatalf("broadened URL should not require recent posts: %s", got)
	}
}

func TestAgencyAccountContactSearchStrategiesUseResourceFallbackOnlyForStrongAccounts(t *testing.T) {
	strong := AgencyAccount{ID: "strong", Name: "Strong Studio", FitScore: 80}
	weak := AgencyAccount{ID: "weak", Name: "Weak Studio", FitScore: 65}
	if got := agencyAccountContactStrategyCount(strong); got != 3 {
		t.Fatalf("strong strategy count = %d", got)
	}
	if got := agencyAccountContactStrategyCount(weak); got != 2 {
		t.Fatalf("weak strategy count = %d", got)
	}
}

func TestAgencyAccountsForContactCapturePrefersAccountsWithoutActiveLeads(t *testing.T) {
	state := OutreachState{
		AgencyAccounts: []AgencyAccount{
			{ID: "acct_active", Name: "Active Studio", Status: AgencyAccountStatusQualified, FitScore: 100},
			{ID: "acct_fresh", Name: "Fresh Studio", Status: AgencyAccountStatusQualified, FitScore: 80},
		},
		Leads: []Lead{{
			ID:                "lead_active",
			Name:              "Active Founder",
			Status:            LeadStatusEligible,
			MessageStatus:     MessageStatusDrafted,
			LeadType:          LeadTypeAgencyFounder,
			AgencyAccountID:   strPtr("acct_active"),
			AgencyAccountName: strPtr("Active Studio"),
		}},
	}
	accounts := agencyAccountsForContactCapture(state, 2)
	if len(accounts) != 2 || accounts[0].ID != "acct_fresh" {
		t.Fatalf("accounts = %#v", accounts)
	}
}

func TestAgencyAccountsNeedingContactCaptureIgnoresSentLeads(t *testing.T) {
	state := OutreachState{
		AgencyAccounts: []AgencyAccount{
			{ID: "acct_sent", Name: "Sent Studio", Status: AgencyAccountStatusQualified, FitScore: 100},
			{ID: "acct_drafted", Name: "Drafted Studio", Status: AgencyAccountStatusQualified, FitScore: 90},
			{ID: "acct_fresh", Name: "Fresh Studio", Status: AgencyAccountStatusQualified, FitScore: 80},
		},
		Leads: []Lead{
			{
				ID:              "lead_sent",
				Name:            "Sent Founder",
				Status:          LeadStatusEligible,
				MessageStatus:   MessageStatusSent,
				LeadType:        LeadTypeAgencyFounder,
				AgencyAccountID: strPtr("acct_sent"),
			},
			{
				ID:              "lead_drafted",
				Name:            "Drafted Founder",
				Status:          LeadStatusEligible,
				MessageStatus:   MessageStatusDrafted,
				LeadType:        LeadTypeAgencyFounder,
				AgencyAccountID: strPtr("acct_drafted"),
			},
		},
	}
	accounts := agencyAccountsNeedingContactCapture(state, 3)
	if len(accounts) != 2 {
		t.Fatalf("accounts = %#v", accounts)
	}
	got := map[string]bool{}
	for _, account := range accounts {
		got[account.ID] = true
	}
	if !got["acct_sent"] || !got["acct_fresh"] || got["acct_drafted"] {
		t.Fatalf("accounts = %#v", accounts)
	}
}

func TestAgencyAccountOpenLeadCountExcludesAlreadySentDuplicates(t *testing.T) {
	state := OutreachState{
		Leads: []Lead{
			{
				ID:              "lead_sent",
				Name:            "Sent Founder",
				Status:          LeadStatusEligible,
				MessageStatus:   MessageStatusSent,
				LeadType:        LeadTypeAgencyFounder,
				AgencyAccountID: strPtr("acct_1"),
			},
			{
				ID:              "lead_ready",
				Name:            "Ready Founder",
				Status:          LeadStatusEligible,
				MessageStatus:   MessageStatusDryRunReady,
				LeadType:        LeadTypeAgencyFounder,
				AgencyAccountID: strPtr("acct_1"),
			},
			{
				ID:              "lead_rejected",
				Name:            "Rejected Founder",
				Status:          LeadStatusRejected,
				MessageStatus:   MessageStatusNone,
				LeadType:        LeadTypeBadFit,
				AgencyAccountID: strPtr("acct_1"),
			},
		},
	}
	if got := agencyAccountOpenLeadCount(state, "acct_1"); got != 1 {
		t.Fatalf("open lead count = %d", got)
	}
}

func TestRetireStaleAgencyAccountsExhaustsSentOnlyAccounts(t *testing.T) {
	store, err := NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	state := OutreachState{
		AgencyAccounts: []AgencyAccount{
			{ID: "acct_sent", Name: "Sent Studio", Status: AgencyAccountStatusQualified, ContactCaptureCount: 2},
			{ID: "acct_open", Name: "Open Studio", Status: AgencyAccountStatusQualified, ContactCaptureCount: 2},
		},
		Leads: []Lead{
			{
				ID:              "lead_sent",
				Name:            "Sent Founder",
				Status:          LeadStatusEligible,
				MessageStatus:   MessageStatusSent,
				LeadType:        LeadTypeAgencyFounder,
				AgencyAccountID: strPtr("acct_sent"),
			},
			{
				ID:              "lead_open",
				Name:            "Open Founder",
				Status:          LeadStatusEligible,
				MessageStatus:   MessageStatusDrafted,
				LeadType:        LeadTypeAgencyFounder,
				AgencyAccountID: strPtr("acct_open"),
			},
		},
	}
	if err := store.Save(state); err != nil {
		t.Fatal(err)
	}
	if err := retireStaleAgencyAccounts(store); err != nil {
		t.Fatal(err)
	}
	got, err := store.Load()
	if err != nil {
		t.Fatal(err)
	}
	sentIndex := findAgencyAccountByID(got.AgencyAccounts, "acct_sent")
	openIndex := findAgencyAccountByID(got.AgencyAccounts, "acct_open")
	if sentIndex < 0 || got.AgencyAccounts[sentIndex].Status != AgencyAccountStatusExhausted {
		t.Fatalf("sent account = %#v", got.AgencyAccounts)
	}
	if openIndex < 0 || got.AgencyAccounts[openIndex].Status != AgencyAccountStatusQualified {
		t.Fatalf("open account = %#v", got.AgencyAccounts)
	}
}

func TestRecordAgencyContactCaptureErrorPersistsResumeMarker(t *testing.T) {
	store, err := NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	state := OutreachState{AgencyAccounts: []AgencyAccount{{
		ID:     "acct_error",
		Name:   "Error Studio",
		Status: AgencyAccountStatusQualified,
	}}}
	if err := store.Save(state); err != nil {
		t.Fatal(err)
	}
	strategy := agencyContactSearchStrategy{Name: "founder_recent"}
	if err := recordAgencyContactCaptureError(store, "acct_error", strategy, errors.New("browser closed")); err != nil {
		t.Fatal(err)
	}
	got, err := store.Load()
	if err != nil {
		t.Fatal(err)
	}
	index := findAgencyAccountByID(got.AgencyAccounts, "acct_error")
	if index < 0 {
		t.Fatal("account missing")
	}
	account := got.AgencyAccounts[index]
	if account.ContactErrorCount != 1 || account.LastContactError == nil || !strings.Contains(*account.LastContactError, "browser closed") {
		t.Fatalf("account = %#v", account)
	}
	if account.LastContactStrategy == nil || *account.LastContactStrategy != "founder_recent" || account.LastContactCaptureAt == nil {
		t.Fatalf("account = %#v", account)
	}
}

func TestAgencyContactAccountLimitUsesBuffer(t *testing.T) {
	if got := agencyContactAccountLimit(1); got != 5 {
		t.Fatalf("limit for one needed = %d", got)
	}
	if got := agencyContactAccountLimit(5); got != 10 {
		t.Fatalf("limit for five needed = %d", got)
	}
}

func TestDefaultOutreachSourceURLUsesProductStudioFallback(t *testing.T) {
	got, ok := defaultOutreachSourceURL(AgencyProductStudioSource)
	if !ok {
		t.Fatal("product studio source URL missing")
	}
	for _, want := range []string{
		"type%3ACURRENT_TITLE",
		"id%3A35",
		"type%3AINDUSTRY",
		"id%3A99",
		"Design%2520Services",
		"keywords%3Aproduct%2520studio",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("product studio URL missing %q: %s", want, got)
		}
	}
}

func TestSalesNavMessageSenderPreservesConfiguredLineBreaks(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "scripts", "salesnav-send-message-one.js"))
	if err != nil {
		t.Fatal(err)
	}
	source := string(raw)
	if strings.Contains(source, `cleanText(configValue("message"`) {
		t.Fatal("sender must not cleanText the configured message; that collapses draft line breaks")
	}
	if !strings.Contains(source, `replace(/\r\n/g, "\n").trim()`) {
		t.Fatal("sender should normalize CRLF while preserving internal line breaks")
	}
	if !strings.Contains(source, "acceptanceFollowupMessageConfig") {
		t.Fatal("sender should accept the acceptance follow-up message config namespace")
	}
	if !strings.Contains(source, `previewFill`) || !strings.Contains(source, `status: "preview-filled"`) {
		t.Fatal("sender should support a fill-only preview status before any send click")
	}
}

func strPtr(value string) *string {
	return &value
}
