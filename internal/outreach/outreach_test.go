package outreach

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/hanifcarroll/linkedin-network-run/internal/app"
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
	if strings.Contains(body, "profile mentions") || !strings.Contains(body, "I'm a full-stack product engineer (8 YoE) that builds and launches AI-powered web & mobile products. I'm reaching out about contract work.") {
		t.Fatalf("body = %q", body)
	}
	if !strings.Contains(body, "Turned an AI media MVP into a production agent platform") {
		t.Fatalf("body = %q", body)
	}
	if !strings.Contains(body, "Recent projects:") || strings.Contains(body, "Recent wins:") {
		t.Fatalf("body = %q", body)
	}
	if !strings.Contains(body, "Are you the right person to ask about this type of role?") || strings.Contains(body, "Would you like me to send my resume and project examples?") {
		t.Fatalf("body = %q", body)
	}
	if strings.Contains(body, "Best,") || strings.Contains(body, "Hanif Carroll") {
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
	if !strings.Contains(body, "I'm a full-stack product engineer (8 YoE) that builds and launches AI-powered web & mobile products. I'm reaching out about project or overflow work.") || !strings.Contains(body, "Comfortable collaborating with design and product teams.") {
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
	} {
		if !strings.Contains(markdown, want) {
			t.Fatalf("markdown missing %q:\n%s", want, markdown)
		}
	}
}

func TestLatestRunSummaryAndRecommendationPreferAgencyRetry(t *testing.T) {
	started := time.Date(2026, time.June, 23, 12, 0, 0, 0, time.UTC)
	state := OutreachState{RunEvents: []RunEvent{
		{At: started, RunID: "daily-2", Phase: "run-start", Command: "run-daily", StartedAt: started, TargetAgencies: 5, TargetRecruiters: 5, AllowSend: true, DashboardPath: "/tmp/run.md", StatePath: "/tmp/state.sqlite"},
		{At: started.Add(time.Second), RunID: "daily-2", Phase: "send-message", Bucket: "recruiter", LeadID: "lead_1", Name: "Riley", Result: "sent-clicked"},
		{At: started.Add(2 * time.Second), RunID: "daily-2", Phase: "send-message", Bucket: "agency", LeadID: "lead_2", Name: "Dana", Result: "sent-clicked"},
		{At: started.Add(time.Minute), RunID: "daily-2", Phase: "run-finish", Command: "run-daily", Result: "completed", StartedAt: started, CompletedAt: started.Add(time.Minute), TargetAgencies: 5, TargetRecruiters: 5, AllowSend: true, DashboardPath: "/tmp/run.md", StatePath: "/tmp/state.sqlite"},
	}}
	summary, ok := LatestRunSummary(state, "/tmp/state.sqlite")
	if !ok {
		t.Fatal("missing summary")
	}
	if summary.Counts.Sent.Agencies != 1 || summary.Counts.Sent.Recruiters != 1 {
		t.Fatalf("counts = %#v", summary.Counts.Sent)
	}
	if !summary.Recommendation.ShouldRetry || !strings.Contains(summary.Recommendation.Command, "--target-agencies 5 --target-recruiters 0") {
		t.Fatalf("recommendation = %#v", summary.Recommendation)
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
