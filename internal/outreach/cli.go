package outreach

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/hanifcarroll/linkedin-network-run/internal/app"
	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

const (
	defaultPlaywriter          = "/Users/hanifcarroll/.bun/bin/playwriter"
	defaultCaptureScript       = "/Users/hanifcarroll/projects/linkedin-network-automation/scripts/salesnav-capture.js"
	defaultMessageScript       = "/Users/hanifcarroll/projects/linkedin-network-automation/scripts/salesnav-send-message-one.js"
	defaultSavedSearchesScript = "/Users/hanifcarroll/projects/linkedin-network-automation/scripts/salesnav-saved-searches.js"
	defaultSavedSearches       = "/tmp/linkedin-network-run-saved-searches.json"
	defaultCaptureOutDir       = "/tmp/recruiter-agency-outreach-capture"
	defaultMessageOutDir       = "/tmp/recruiter-agency-outreach-message"
)

func Execute(ctx context.Context, args []string) error {
	var stateDir string
	root := &cobra.Command{
		Use:           "recruiter-agency-outreach",
		Short:         "Recruiter and agency sourcing, drafting, and guarded messages",
		SilenceUsage:  true,
		SilenceErrors: true,
	}
	root.PersistentFlags().StringVar(&stateDir, "state-dir", "", "state directory")

	withStore := func(fn func(*Store) error) func(*cobra.Command, []string) error {
		return func(_ *cobra.Command, _ []string) error {
			store, err := NewStore(stateDir)
			if err != nil {
				return err
			}
			return fn(store)
		}
	}

	root.AddCommand(runDailyCommand(withStore))
	root.AddCommand(captureCommand(withStore))
	root.AddCommand(importCaptureCommand(withStore))
	root.AddCommand(queueCommand(withStore))
	root.AddCommand(draftCommand(withStore))
	root.AddCommand(dashboardCommand(withStore))
	root.AddCommand(reviseCommand(withStore))
	root.AddCommand(sendReadyCommand(withStore))
	root.AddCommand(sendMessageCommand(withStore))
	root.AddCommand(markMessageCommand(withStore))
	root.AddCommand(rejectCommand(withStore))
	root.AddCommand(reportCommand(withStore))

	root.SetContext(ctx)
	root.SetArgs(args)
	return root.Execute()
}

func captureCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var session, playwriter, script, savedSearches, source, url, outDir string
	var pages, stopAfterConnectable, limit, rowScrollDelayMS uint32
	var onlyConnectable bool
	cmd := &cobra.Command{
		Use: "capture",
		RunE: withStore(func(store *Store) error {
			if strings.TrimSpace(session) == "" {
				return fmt.Errorf("--session is required")
			}
			if strings.TrimSpace(source) == "" {
				return fmt.Errorf("--source is required")
			}
			resolvedURL, err := app.ResolveCaptureURL(app.OptionalString(url), savedSearches, source, "--url")
			if err != nil {
				return err
			}
			path, err := app.RunPlaywriterCapture(playwriter, session, script, outDir, source, resolvedURL, app.CaptureRunOptions{
				Pages:                pages,
				StopAfterConnectable: stopAfterConnectable,
				Limit:                limit,
				RowScrollDelayMS:     rowScrollDelayMS,
				OnlyConnectable:      onlyConnectable,
			})
			if err != nil {
				return err
			}
			return importCapturePath(store, path, onlyConnectable)
		}),
	}
	cmd.Flags().StringVar(&session, "session", "", "Playwriter session")
	addPlaywriterFlag(cmd.Flags(), &playwriter)
	cmd.Flags().StringVar(&script, "script", defaultCaptureScript, "Sales Navigator capture script")
	cmd.Flags().StringVar(&savedSearches, "saved-searches", defaultSavedSearches, "saved-search resolver artifact")
	cmd.Flags().StringVar(&source, "source", "", "Sales Navigator saved search/source name")
	cmd.Flags().StringVar(&url, "url", "", "explicit Sales Navigator URL")
	cmd.Flags().StringVar(&outDir, "out-dir", defaultCaptureOutDir, "capture output directory")
	cmd.Flags().Uint32Var(&pages, "pages", 2, "pages to capture")
	cmd.Flags().Uint32Var(&stopAfterConnectable, "stop-after-connectable", 0, "stop after N connectable rows")
	cmd.Flags().Uint32Var(&limit, "limit", 25, "rows per page")
	cmd.Flags().Uint32Var(&rowScrollDelayMS, "row-scroll-delay-ms", 250, "row scroll delay")
	cmd.Flags().BoolVar(&onlyConnectable, "only-connectable", false, "import only connectable rows")
	return cmd
}

func importCaptureCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var onlyConnectable bool
	cmd := &cobra.Command{
		Use:  "import-capture <path>",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error {
				return importCapturePath(store, args[0], onlyConnectable)
			})(cmd, args)
		},
	}
	cmd.Flags().BoolVar(&onlyConnectable, "only-connectable", false, "import only connectable rows")
	return cmd
}

func importCapturePath(store *Store, path string, onlyConnectable bool) error {
	capture, err := app.LoadSalesNavCapture(path)
	if err != nil {
		return err
	}
	state, err := store.Load()
	if err != nil {
		return err
	}
	summary, err := ImportCapture(&state, capture, ImportOptions{OnlyConnectable: onlyConnectable})
	if err != nil {
		return err
	}
	if err := store.Save(state); err != nil {
		return err
	}
	fmt.Printf("source=%s stored=%d updated=%d eligible=%d needs_review=%d rejected=%d total=%d\n", summary.Source, summary.Stored, summary.Updated, summary.Eligible, summary.Reviewed, summary.Rejected, summary.TotalLeads)
	return nil
}

func runDailyCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var session, playwriter, captureScript, messageScript, savedSearchesScript, savedSearches, captureOutDir, messageOutDir, dashboardPath string
	var targetAgencies, targetRecruiters, maxCaptureRounds int
	var pages, limit, stopAfterConnectable, rowScrollDelayMS, timeoutMS uint32
	var allowSend, refreshSavedSearches, skipSessionReset, printMarkdown bool
	cmd := &cobra.Command{
		Use: "run-daily",
		RunE: withStore(func(store *Store) error {
			result, err := RunDaily(store, DailyOptions{
				Session:              session,
				Playwriter:           playwriter,
				CaptureScript:        captureScript,
				MessageScript:        messageScript,
				SavedSearchesScript:  savedSearchesScript,
				SavedSearches:        savedSearches,
				TargetAgencies:       targetAgencies,
				TargetRecruiters:     targetRecruiters,
				PagesPerCapture:      pages,
				Limit:                limit,
				StopAfterConnectable: stopAfterConnectable,
				RowScrollDelayMS:     rowScrollDelayMS,
				MaxCaptureRounds:     maxCaptureRounds,
				AllowSend:            allowSend,
				RefreshSavedSearches: refreshSavedSearches,
				SkipSessionReset:     skipSessionReset,
				CaptureOutDir:        captureOutDir,
				MessageOutDir:        messageOutDir,
				DashboardPath:        dashboardPath,
				PrintMarkdown:        printMarkdown,
				TimeoutMS:            timeoutMS,
			})
			if err != nil {
				return err
			}
			fmt.Printf("dashboard=%s\n", result.DashboardPath)
			if printMarkdown {
				fmt.Println(result.Markdown)
			}
			return nil
		}),
	}
	addDailyFlags(cmd, &session, &playwriter, &captureScript, &messageScript, &savedSearchesScript, &savedSearches, &captureOutDir, &messageOutDir, &dashboardPath, &targetAgencies, &targetRecruiters, &maxCaptureRounds, &pages, &limit, &stopAfterConnectable, &rowScrollDelayMS, &timeoutMS, &allowSend, &refreshSavedSearches, &skipSessionReset, &printMarkdown)
	return cmd
}

func queueCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var limit int
	var statuses []string
	var asJSON, includeDrafts bool
	cmd := &cobra.Command{
		Use: "queue",
		RunE: withStore(func(store *Store) error {
			state, err := store.Load()
			if err != nil {
				return err
			}
			parsed, err := parseStatuses(statuses)
			if err != nil {
				return err
			}
			items := Queue(state, parsed, limit, includeDrafts)
			if asJSON {
				raw, err := json.MarshalIndent(items, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			printQueue(items)
			return nil
		}),
	}
	cmd.Flags().IntVar(&limit, "limit", 20, "max rows")
	cmd.Flags().StringSliceVar(&statuses, "status", []string{string(LeadStatusEligible)}, "lead status filter")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	cmd.Flags().BoolVar(&includeDrafts, "include-drafts", false, "include draft text")
	return cmd
}

func draftCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var limit int
	var out string
	var asJSON bool
	cmd := &cobra.Command{
		Use: "draft",
		RunE: withStore(func(store *Store) error {
			state, err := store.Load()
			if err != nil {
				return err
			}
			report := DraftMessages(&state, limit)
			if err := store.Save(state); err != nil {
				return err
			}
			if out == "" {
				out = store.DefaultDraftReportPath()
			}
			if err := WriteDraftMarkdown(out, report); err != nil {
				return err
			}
			if asJSON {
				raw, err := json.MarshalIndent(report, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
			}
			fmt.Printf("drafted=%d out=%s\n", len(report.Items), out)
			return nil
		}),
	}
	cmd.Flags().IntVar(&limit, "limit", 20, "max drafts")
	cmd.Flags().StringVar(&out, "out", "", "markdown output path")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func dashboardCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var out string
	var printMarkdown bool
	var targetAgencies, targetRecruiters int
	var allowSend bool
	cmd := &cobra.Command{
		Use: "dashboard",
		RunE: withStore(func(store *Store) error {
			state, err := store.Load()
			if err != nil {
				return err
			}
			if out == "" {
				out = store.DefaultDailyDashboardPath()
			}
			if targetAgencies == 0 {
				targetAgencies = 5
			}
			if targetRecruiters == 0 {
				targetRecruiters = 5
			}
			report := BuildDashboardReport(state, store.StatePath(), targetAgencies, targetRecruiters, allowSend, nil)
			if err := WriteDashboardMarkdown(out, report); err != nil {
				return err
			}
			fmt.Printf("dashboard=%s\n", out)
			if printMarkdown {
				fmt.Println(RenderDashboardMarkdown(report))
			}
			return nil
		}),
	}
	cmd.Flags().StringVar(&out, "out", "", "markdown output path")
	cmd.Flags().BoolVar(&printMarkdown, "print-markdown", false, "print dashboard markdown")
	cmd.Flags().IntVar(&targetAgencies, "target-agencies", 5, "agency target")
	cmd.Flags().IntVar(&targetRecruiters, "target-recruiters", 5, "recruiter target")
	cmd.Flags().BoolVar(&allowSend, "allow-send", false, "dashboard reflects real-send run")
	return cmd
}

func reviseCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var id, bodyFile, angle string
	cmd := &cobra.Command{
		Use: "revise",
		RunE: withStore(func(store *Store) error {
			raw, err := os.ReadFile(bodyFile)
			if err != nil {
				return fmt.Errorf("reading %s: %w", bodyFile, err)
			}
			body := cleanText(string(raw))
			if body == "" {
				return fmt.Errorf("revision body is empty")
			}
			state, err := store.Load()
			if err != nil {
				return err
			}
			index := findLeadByID(state.Leads, id)
			if index < 0 {
				return fmt.Errorf("unknown lead id %q", id)
			}
			if angle == "" && state.Leads[index].Draft != nil {
				angle = state.Leads[index].Draft.Angle
			}
			state.Leads[index].Draft = &MessageDraft{
				Body:        body,
				Angle:       angle,
				Evidence:    draftEvidence(state.Leads[index]),
				GeneratedAt: time.Now(),
			}
			state.Leads[index].MessageStatus = MessageStatusDrafted
			state.Leads[index].UpdatedAt = time.Now()
			if err := store.Save(state); err != nil {
				return err
			}
			fmt.Printf("revised=%s\n", id)
			return nil
		}),
	}
	cmd.Flags().StringVar(&id, "lead-id", "", "lead id")
	cmd.Flags().StringVar(&bodyFile, "body-file", "", "file with revised message body")
	cmd.Flags().StringVar(&angle, "angle", "", "revision angle note")
	must(cmd.MarkFlagRequired("lead-id"))
	must(cmd.MarkFlagRequired("body-file"))
	return cmd
}

func sendReadyCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var session, playwriter, messageScript, messageOutDir, dashboardPath string
	var targetAgencies, targetRecruiters int
	var timeoutMS uint32
	var allowSend, printMarkdown bool
	cmd := &cobra.Command{
		Use: "send-ready",
		RunE: withStore(func(store *Store) error {
			if !allowSend {
				return fmt.Errorf("send-ready requires --allow-send")
			}
			options := normalizeDailyOptions(store, DailyOptions{
				Session:          session,
				Playwriter:       playwriter,
				MessageScript:    messageScript,
				TargetAgencies:   targetAgencies,
				TargetRecruiters: targetRecruiters,
				AllowSend:        true,
				MessageOutDir:    messageOutDir,
				DashboardPath:    dashboardPath,
				TimeoutMS:        timeoutMS,
			})
			actions := []DailyLeadAction{}
			if err := sendBucket(store, options, "agency", options.TargetAgencies, &actions); err != nil {
				return err
			}
			if err := sendBucket(store, options, "recruiter", options.TargetRecruiters, &actions); err != nil {
				return err
			}
			state, err := store.Load()
			if err != nil {
				return err
			}
			report := BuildDashboardReport(state, store.StatePath(), options.TargetAgencies, options.TargetRecruiters, true, actions)
			if err := WriteDashboardMarkdown(options.DashboardPath, report); err != nil {
				return err
			}
			fmt.Printf("dashboard=%s\n", options.DashboardPath)
			if printMarkdown {
				fmt.Println(RenderDashboardMarkdown(report))
			}
			return nil
		}),
	}
	cmd.Flags().StringVar(&session, "session", "", "Playwriter session")
	addPlaywriterFlag(cmd.Flags(), &playwriter)
	cmd.Flags().StringVar(&messageScript, "message-script", defaultMessageScript, "message script")
	cmd.Flags().StringVar(&messageOutDir, "message-out-dir", defaultMessageOutDir, "message output directory")
	cmd.Flags().StringVar(&dashboardPath, "dashboard", "", "dashboard output path")
	cmd.Flags().IntVar(&targetAgencies, "target-agencies", 5, "agency target")
	cmd.Flags().IntVar(&targetRecruiters, "target-recruiters", 5, "recruiter target")
	cmd.Flags().Uint32Var(&timeoutMS, "timeout-ms", 90000, "Playwriter timeout")
	cmd.Flags().BoolVar(&allowSend, "allow-send", false, "allow real sends")
	cmd.Flags().BoolVar(&printMarkdown, "print-markdown", false, "print dashboard markdown")
	must(cmd.MarkFlagRequired("session"))
	return cmd
}

func sendMessageCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var leadID, session, playwriter, script, outDir string
	var dryRun, allowSend bool
	var timeoutMS uint32
	cmd := &cobra.Command{
		Use: "send-message",
		RunE: withStore(func(store *Store) error {
			return SendMessage(store, SendMessageOptions{
				LeadID:     leadID,
				Session:    session,
				Playwriter: playwriter,
				Script:     script,
				OutDir:     outDir,
				DryRun:     dryRun,
				AllowSend:  allowSend,
				TimeoutMS:  timeoutMS,
			})
		}),
	}
	cmd.Flags().StringVar(&leadID, "lead-id", "", "lead id")
	cmd.Flags().StringVar(&session, "session", "", "Playwriter session")
	addPlaywriterFlag(cmd.Flags(), &playwriter)
	cmd.Flags().StringVar(&script, "script", defaultMessageScript, "message script")
	cmd.Flags().StringVar(&outDir, "out-dir", defaultMessageOutDir, "message result output directory")
	cmd.Flags().BoolVar(&dryRun, "dry-run", false, "force dry run")
	cmd.Flags().BoolVar(&allowSend, "allow-send", false, "allow real message send")
	cmd.Flags().Uint32Var(&timeoutMS, "timeout-ms", 60000, "Playwriter script timeout")
	must(cmd.MarkFlagRequired("lead-id"))
	must(cmd.MarkFlagRequired("session"))
	return cmd
}

func markMessageCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var id, status, note string
	cmd := &cobra.Command{
		Use: "mark-message",
		RunE: withStore(func(store *Store) error {
			messageStatus, err := parseMessageStatus(status)
			if err != nil {
				return err
			}
			state, err := store.Load()
			if err != nil {
				return err
			}
			index := findLeadByID(state.Leads, id)
			if index < 0 {
				return fmt.Errorf("unknown lead id %q", id)
			}
			state.Leads[index].MessageStatus = messageStatus
			state.Leads[index].UpdatedAt = time.Now()
			if strings.TrimSpace(note) != "" {
				state.Leads[index].Notes = append(state.Leads[index].Notes, cleanText(note))
			}
			if err := store.Save(state); err != nil {
				return err
			}
			fmt.Printf("lead=%s message_status=%s\n", id, messageStatus)
			return nil
		}),
	}
	cmd.Flags().StringVar(&id, "lead-id", "", "lead id")
	cmd.Flags().StringVar(&status, "status", "", "message status")
	cmd.Flags().StringVar(&note, "note", "", "note")
	must(cmd.MarkFlagRequired("lead-id"))
	must(cmd.MarkFlagRequired("status"))
	return cmd
}

func rejectCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var id, reason string
	cmd := &cobra.Command{
		Use: "reject",
		RunE: withStore(func(store *Store) error {
			state, err := store.Load()
			if err != nil {
				return err
			}
			index := findLeadByID(state.Leads, id)
			if index < 0 {
				return fmt.Errorf("unknown lead id %q", id)
			}
			state.Leads[index].Status = LeadStatusRejected
			state.Leads[index].RejectReasons = append(state.Leads[index].RejectReasons, cleanText(reason))
			if err := store.Save(state); err != nil {
				return err
			}
			fmt.Printf("rejected=%s\n", id)
			return nil
		}),
	}
	cmd.Flags().StringVar(&id, "lead-id", "", "lead id")
	cmd.Flags().StringVar(&reason, "reason", "", "reason")
	must(cmd.MarkFlagRequired("lead-id"))
	must(cmd.MarkFlagRequired("reason"))
	return cmd
}

func reportCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use: "report",
		RunE: withStore(func(store *Store) error {
			state, err := store.Load()
			if err != nil {
				return err
			}
			counts := Counts(state)
			if asJSON {
				raw, err := json.MarshalIndent(counts, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Printf("state: %s\n", store.StatePath())
			printMap("by status", counts.ByStatus)
			printMap("by lead type", counts.ByLeadType)
			printMap("by message status", counts.ByMessageStatus)
			printStringMap("by source", counts.BySource)
			return nil
		}),
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func parseStatuses(values []string) ([]LeadStatus, error) {
	if len(values) == 0 {
		return nil, nil
	}
	statuses := []LeadStatus{}
	for _, value := range values {
		switch LeadStatus(value) {
		case LeadStatusCaptured, LeadStatusEligible, LeadStatusNeedsReview, LeadStatusRejected:
			statuses = append(statuses, LeadStatus(value))
		default:
			return nil, fmt.Errorf("invalid status %q", value)
		}
	}
	return statuses, nil
}

func parseMessageStatus(value string) (MessageStatus, error) {
	switch MessageStatus(value) {
	case MessageStatusNone, MessageStatusDrafted, MessageStatusDryRunReady, MessageStatusSent, MessageStatusManuallySent, MessageStatusNotMessageable, MessageStatusConversationExists, MessageStatusSendFailed, MessageStatusBlocked, MessageStatusReplied, MessageStatusRepliedNotFit, MessageStatusRepliedFuture, MessageStatusRepliedUnknown:
		return MessageStatus(value), nil
	default:
		return "", fmt.Errorf("invalid message status %q", value)
	}
}

func printQueue(items []QueueItem) {
	for _, item := range items {
		title := "-"
		if item.Title != nil {
			title = *item.Title
		}
		company := "-"
		if item.Company != nil {
			company = *item.Company
		}
		url := "-"
		if item.ProfileURL != nil {
			url = *item.ProfileURL
		}
		fmt.Printf("%s\t%d\t%s\t%s\t%s\t%s\t%s\n", item.ID, item.FitScore, item.LeadType, item.Name, title, company, url)
	}
}

func printMap[K ~string](label string, values map[K]int) {
	keys := []string{}
	for key := range values {
		keys = append(keys, string(key))
	}
	sort.Strings(keys)
	fmt.Println(label + ":")
	for _, key := range keys {
		fmt.Printf("- %s: %d\n", key, values[K(key)])
	}
}

func printStringMap(label string, values map[string]int) {
	keys := []string{}
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	fmt.Println(label + ":")
	for _, key := range keys {
		fmt.Printf("- %s: %d\n", key, values[key])
	}
}

func addPlaywriterFlag(flags *pflag.FlagSet, target *string) {
	flags.StringVar(target, "playwriter", defaultPlaywriter, "Playwriter executable")
	flags.StringVar(target, "bunx", defaultPlaywriter, "Playwriter executable alias")
}

func addDailyFlags(cmd *cobra.Command, session *string, playwriter *string, captureScript *string, messageScript *string, savedSearchesScript *string, savedSearches *string, captureOutDir *string, messageOutDir *string, dashboardPath *string, targetAgencies *int, targetRecruiters *int, maxCaptureRounds *int, pages *uint32, limit *uint32, stopAfterConnectable *uint32, rowScrollDelayMS *uint32, timeoutMS *uint32, allowSend *bool, refreshSavedSearches *bool, skipSessionReset *bool, printMarkdown *bool) {
	cmd.Flags().StringVar(session, "session", "", "Playwriter session")
	addPlaywriterFlag(cmd.Flags(), playwriter)
	cmd.Flags().StringVar(captureScript, "capture-script", defaultCaptureScript, "Sales Navigator capture script")
	cmd.Flags().StringVar(messageScript, "message-script", defaultMessageScript, "message script")
	cmd.Flags().StringVar(savedSearchesScript, "saved-searches-script", defaultSavedSearchesScript, "saved searches discovery script")
	cmd.Flags().StringVar(savedSearches, "saved-searches", defaultSavedSearches, "saved-search resolver artifact")
	cmd.Flags().StringVar(captureOutDir, "capture-out-dir", defaultCaptureOutDir, "capture output directory")
	cmd.Flags().StringVar(messageOutDir, "message-out-dir", defaultMessageOutDir, "message result output directory")
	cmd.Flags().StringVar(dashboardPath, "dashboard", "", "dashboard output path")
	cmd.Flags().IntVar(targetAgencies, "target-agencies", 5, "agency target")
	cmd.Flags().IntVar(targetRecruiters, "target-recruiters", 5, "recruiter target")
	cmd.Flags().IntVar(maxCaptureRounds, "max-capture-rounds", 4, "max capture and validation rounds per bucket")
	cmd.Flags().Uint32Var(pages, "pages", 2, "pages to capture per round")
	cmd.Flags().Uint32Var(limit, "limit", 25, "rows per page")
	cmd.Flags().Uint32Var(stopAfterConnectable, "stop-after-connectable", 0, "stop after N connectable rows")
	cmd.Flags().Uint32Var(rowScrollDelayMS, "row-scroll-delay-ms", 250, "row scroll delay")
	cmd.Flags().Uint32Var(timeoutMS, "timeout-ms", 90000, "Playwriter timeout")
	cmd.Flags().BoolVar(allowSend, "allow-send", false, "allow real message sends")
	cmd.Flags().BoolVar(refreshSavedSearches, "refresh-saved-searches", false, "refresh saved-search resolver before capture")
	cmd.Flags().BoolVar(skipSessionReset, "skip-session-reset", false, "skip the default Playwriter session reset before the daily run")
	cmd.Flags().BoolVar(printMarkdown, "print-markdown", false, "print dashboard markdown")
	must(cmd.MarkFlagRequired("session"))
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}
