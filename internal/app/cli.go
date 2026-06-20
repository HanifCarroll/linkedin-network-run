package app

import (
	"context"
	"fmt"
	"strconv"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

const (
	defaultPlaywriter                = "/Users/hanifcarroll/.bun/bin/playwriter"
	defaultSendScript                = "/Users/hanifcarroll/projects/tool/scripts/salesnav-send-one.js"
	defaultAuditScript               = "/Users/hanifcarroll/projects/tool/scripts/salesnav-audit.js"
	defaultCaptureScript             = "/Users/hanifcarroll/projects/tool/scripts/salesnav-capture.js"
	defaultAcceptedResearchScript    = "/Users/hanifcarroll/projects/tool/scripts/salesnav-accepted-research.js"
	defaultPendingWithdrawScript     = "/Users/hanifcarroll/projects/tool/scripts/salesnav-pending-withdraw-one.js"
	defaultSavedSearches             = "/tmp/linkedin-network-run-saved-searches.json"
	defaultSendNextOutDir            = "/tmp/linkedin-network-run-send-next"
	defaultSendGuardedOutDir         = "/tmp/linkedin-network-run-send-guarded"
	defaultReconcileAuditOutDir      = "/tmp/linkedin-network-run-reconcile-audit"
	defaultTopUpReconcileOutDir      = "/tmp/linkedin-network-run-top-up-reconcile"
	defaultAcceptanceCandidatesPath  = "/tmp/linkedin-acceptance-candidates.json"
	defaultAcceptedFollowupsOutDir   = "/tmp/linkedin-accepted-followups"
	defaultReservoirCaptureOutDir    = "/tmp/linkedin-network-run-reservoir-capture"
	defaultPendingWithdrawNextOutDir = "/tmp/linkedin-pending-cleanup-withdraw-next"
)

func Execute(ctx context.Context, args []string) error {
	var stateDir string
	root := &cobra.Command{
		Use:           "linkedin-network-run",
		Short:         "Durable run controller for LinkedIn Sales Navigator networking runs",
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

	root.AddCommand(startCommand(withStore))
	root.AddCommand(auditCommand(withStore))
	root.AddCommand(importAuditCommand(withStore))
	root.AddCommand(&cobra.Command{Use: "next", RunE: withStore(PrintRunNext)})
	root.AddCommand(recordCommand(withStore))
	root.AddCommand(recordSendResultCommand(withStore))
	root.AddCommand(sendNextCommand(withStore))
	root.AddCommand(sendGuardedCommand(withStore))
	root.AddCommand(drainStaleCandidatesCommand(withStore))
	root.AddCommand(reconcileAuditCommand(withStore))
	root.AddCommand(topUpReconcileCommand(withStore))
	root.AddCommand(sourceExhaustedCommand(withStore))
	root.AddCommand(needsReauditCommand(withStore))
	root.AddCommand(importCaptureCommand(withStore))
	root.AddCommand(recordTopUpResultCommand(withStore))
	root.AddCommand(nextCandidateCommand(withStore))
	root.AddCommand(candidatesCommand(withStore))
	root.AddCommand(planCommand(withStore))
	root.AddCommand(statusCommand(withStore))
	root.AddCommand(&cobra.Command{Use: "report", RunE: withStore(func(store *Store) error {
		run, err := store.Load()
		if err != nil {
			return err
		}
		fmt.Println(RenderReport(run))
		return nil
	})})
	root.AddCommand(finishCommand(withStore))
	root.AddCommand(acceptanceCommand(withStore))
	root.AddCommand(reservoirCommand(withStore))
	root.AddCommand(tuneSourcesCommand(withStore))
	root.AddCommand(pendingCleanupCommand(withStore))

	root.SetContext(ctx)
	root.SetArgs(args)
	return root.Execute()
}

func startCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var target uint32 = 30
	var dateFlag string
	var force bool
	var maxRealSends uint32
	var hasMaxRealSends bool
	cmd := &cobra.Command{
		Use: "start",
		RunE: withStore(func(store *Store) error {
			date, err := parseDateFlagOrToday(dateFlag)
			if err != nil {
				return err
			}
			var max *uint32
			if hasMaxRealSends {
				max = &maxRealSends
			}
			return StartRun(store, target, date, force, max)
		}),
	}
	cmd.Flags().Uint32Var(&target, "target", 30, "target sends")
	cmd.Flags().StringVar(&dateFlag, "date", "", "run date")
	cmd.Flags().BoolVar(&force, "force", false, "replace active run")
	cmd.Flags().Uint32Var(&maxRealSends, "max-real-sends", 0, "maximum real sends")
	cmd.PreRun = func(cmd *cobra.Command, _ []string) {
		hasMaxRealSends = cmd.Flags().Changed("max-real-sends")
	}
	return cmd
}

func auditCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var note string
	cmd := &cobra.Command{
		Use:  "audit <people-count>",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			peopleCount, err := parseUint32Arg(args[0], "people-count")
			if err != nil {
				return err
			}
			return withStore(func(store *Store) error {
				return RecordAudit(store, peopleCount, OptionalString(note))
			})(cmd, args)
		},
	}
	cmd.Flags().StringVar(&note, "note", "", "audit note")
	return cmd
}

func importAuditCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	return &cobra.Command{
		Use:  "import-audit <path>",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error { return ImportAudit(store, args[0]) })(cmd, args)
		},
	}
}

func recordCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var source, name, profileURL, statusValue, note string
	cmd := &cobra.Command{
		Use: "record",
		RunE: withStore(func(store *Store) error {
			status, err := ParseCandidateStatus(statusValue)
			if err != nil {
				return err
			}
			return RecordCandidate(store, source, name, OptionalString(profileURL), status, OptionalString(note))
		}),
	}
	cmd.Flags().StringVar(&source, "source", "", "source")
	cmd.Flags().StringVar(&name, "name", "", "name")
	cmd.Flags().StringVar(&profileURL, "profile-url", "", "profile url")
	cmd.Flags().StringVar(&statusValue, "status", "", "status")
	cmd.Flags().StringVar(&note, "note", "", "note")
	must(cmd.MarkFlagRequired("source"))
	must(cmd.MarkFlagRequired("name"))
	must(cmd.MarkFlagRequired("status"))
	return cmd
}

func recordSendResultCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	return &cobra.Command{
		Use:  "record-send-result <path>",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error { return RecordSendResultCommand(store, args[0]) })(cmd, args)
		},
	}
}

func sendNextCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var session, playwriter, script, outDir string
	var dryRun, allowSend, noRecord bool
	cmd := &cobra.Command{
		Use: "send-next",
		RunE: withStore(func(store *Store) error {
			return SendNext(store, SendNextOptions{
				Session:    OptionalString(session),
				Playwriter: playwriter,
				Script:     script,
				OutDir:     outDir,
				DryRun:     dryRun,
				AllowSend:  allowSend,
				NoRecord:   noRecord,
			})
		}),
	}
	cmd.Flags().StringVar(&session, "session", "", "Playwriter session")
	addPlaywriterFlag(cmd.Flags(), &playwriter)
	cmd.Flags().StringVar(&script, "script", defaultSendScript, "send script")
	cmd.Flags().StringVar(&outDir, "out-dir", defaultSendNextOutDir, "output directory")
	cmd.Flags().BoolVar(&dryRun, "dry-run", false, "dry run")
	cmd.Flags().BoolVar(&allowSend, "allow-send", false, "allow send")
	cmd.Flags().BoolVar(&noRecord, "no-record", false, "do not record")
	return cmd
}

func sendGuardedCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var session, playwriter, script, outDir string
	var maxAttempts uint32 = 30
	var dryRun, singlePass, allowSend, noRecord bool
	cmd := &cobra.Command{
		Use: "send-guarded",
		RunE: withStore(func(store *Store) error {
			return HandleSendGuarded(store, SendGuardedOptions{
				Session:     OptionalString(session),
				Playwriter:  playwriter,
				Script:      script,
				OutDir:      outDir,
				MaxAttempts: maxAttempts,
				DryRun:      dryRun,
				SinglePass:  singlePass,
				AllowSend:   allowSend,
				NoRecord:    noRecord,
			})
		}),
	}
	cmd.Flags().StringVar(&session, "session", "", "Playwriter session")
	addPlaywriterFlag(cmd.Flags(), &playwriter)
	cmd.Flags().StringVar(&script, "script", defaultSendScript, "send script")
	cmd.Flags().StringVar(&outDir, "out-dir", defaultSendGuardedOutDir, "output directory")
	cmd.Flags().Uint32Var(&maxAttempts, "max-attempts", 30, "max attempts")
	cmd.Flags().BoolVar(&dryRun, "dry-run", false, "dry run")
	cmd.Flags().BoolVar(&singlePass, "single-pass", false, "single pass")
	cmd.Flags().BoolVar(&allowSend, "allow-send", false, "allow send")
	cmd.Flags().BoolVar(&noRecord, "no-record", false, "do not record")
	return cmd
}

func drainStaleCandidatesCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var source string
	cmd := &cobra.Command{
		Use: "drain-stale-candidates",
		RunE: withStore(func(store *Store) error {
			return DrainStaleCandidatesCommand(store, OptionalString(source))
		}),
	}
	cmd.Flags().StringVar(&source, "source", "", "source")
	return cmd
}

func reconcileAuditCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var session, playwriter, script, outDir string
	var attempts uint32 = 3
	var delayMS uint64 = 5000
	var finish bool
	cmd := &cobra.Command{
		Use: "reconcile-audit",
		RunE: withStore(func(store *Store) error {
			return HandleReconcileAudit(store, ReconcileAuditOptions{
				Session:    OptionalString(session),
				Playwriter: playwriter,
				Script:     script,
				OutDir:     outDir,
				Attempts:   attempts,
				DelayMS:    delayMS,
				Finish:     finish,
			})
		}),
	}
	cmd.Flags().StringVar(&session, "session", "", "Playwriter session")
	addPlaywriterFlag(cmd.Flags(), &playwriter)
	cmd.Flags().StringVar(&script, "script", defaultAuditScript, "audit script")
	cmd.Flags().StringVar(&outDir, "out-dir", defaultReconcileAuditOutDir, "output directory")
	cmd.Flags().Uint32Var(&attempts, "attempts", 3, "attempts")
	cmd.Flags().Uint64Var(&delayMS, "delay-ms", 5000, "delay ms")
	cmd.Flags().BoolVar(&finish, "finish", false, "finish")
	return cmd
}

func topUpReconcileCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var session, playwriter, sendScript, auditScript, captureScript, savedSearches, fallbackSource, fallbackURL, outDir string
	var fallbackPages uint32 = 5
	var fallbackStopAfter uint32 = 10
	var fallbackLimit uint32 = 18
	var fallbackRowScrollDelay uint32 = 250
	var noFallbackCapture bool
	var maxAttempts uint32 = 20
	var delayMS uint64 = 1000
	var allowSend, finish bool
	cmd := &cobra.Command{
		Use: "top-up-reconcile",
		RunE: withStore(func(store *Store) error {
			return HandleTopUpReconcile(store, TopUpReconcileOptions{
				Session:     OptionalString(session),
				Playwriter:  playwriter,
				SendScript:  sendScript,
				AuditScript: auditScript,
				Fallback: TopUpFallbackOptions{
					CaptureScript:        captureScript,
					SavedSearches:        savedSearches,
					Source:               fallbackSource,
					URL:                  OptionalString(fallbackURL),
					Pages:                fallbackPages,
					StopAfterConnectable: fallbackStopAfter,
					Limit:                fallbackLimit,
					RowScrollDelayMS:     fallbackRowScrollDelay,
					CaptureEnabled:       !noFallbackCapture,
				},
				OutDir:      outDir,
				MaxAttempts: maxAttempts,
				DelayMS:     delayMS,
				AllowSend:   allowSend,
				Finish:      finish,
			})
		}),
	}
	cmd.Flags().StringVar(&session, "session", "", "Playwriter session")
	addPlaywriterFlag(cmd.Flags(), &playwriter)
	cmd.Flags().StringVar(&sendScript, "send-script", defaultSendScript, "send script")
	cmd.Flags().StringVar(&auditScript, "audit-script", defaultAuditScript, "audit script")
	cmd.Flags().StringVar(&captureScript, "capture-script", defaultCaptureScript, "capture script")
	cmd.Flags().StringVar(&savedSearches, "saved-searches", defaultSavedSearches, "saved searches")
	cmd.Flags().StringVar(&fallbackSource, "fallback-source", "FO - Founders - Urgent", "fallback source")
	cmd.Flags().StringVar(&fallbackURL, "fallback-url", "", "fallback url")
	cmd.Flags().Uint32Var(&fallbackPages, "fallback-pages", 5, "fallback pages")
	cmd.Flags().Uint32Var(&fallbackStopAfter, "fallback-stop-after-connectable", 10, "fallback stop after connectable")
	cmd.Flags().Uint32Var(&fallbackLimit, "fallback-limit", 18, "fallback limit")
	cmd.Flags().Uint32Var(&fallbackRowScrollDelay, "fallback-row-scroll-delay-ms", 250, "fallback row scroll delay")
	cmd.Flags().BoolVar(&noFallbackCapture, "no-fallback-capture", false, "disable fallback capture")
	cmd.Flags().StringVar(&outDir, "out-dir", defaultTopUpReconcileOutDir, "output directory")
	cmd.Flags().Uint32Var(&maxAttempts, "max-attempts", 20, "max attempts")
	cmd.Flags().Uint64Var(&delayMS, "delay-ms", 1000, "delay ms")
	cmd.Flags().BoolVar(&allowSend, "allow-send", false, "allow send")
	cmd.Flags().BoolVar(&finish, "finish", false, "finish")
	return cmd
}

func sourceExhaustedCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var source, note string
	cmd := &cobra.Command{
		Use: "source-exhausted",
		RunE: withStore(func(store *Store) error {
			return SourceExhausted(store, source, OptionalString(note))
		}),
	}
	cmd.Flags().StringVar(&source, "source", "", "source")
	cmd.Flags().StringVar(&note, "note", "", "note")
	must(cmd.MarkFlagRequired("source"))
	return cmd
}

func needsReauditCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var reason string
	cmd := &cobra.Command{
		Use:  "needs-reaudit",
		RunE: withStore(func(store *Store) error { return NeedsReaudit(store, reason) }),
	}
	cmd.Flags().StringVar(&reason, "reason", "", "reason")
	must(cmd.MarkFlagRequired("reason"))
	return cmd
}

func importCaptureCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var onlyConnectable bool
	cmd := &cobra.Command{
		Use:  "import-capture <path>",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error { return ImportCaptureCommand(store, args[0], onlyConnectable) })(cmd, args)
		},
	}
	cmd.Flags().BoolVar(&onlyConnectable, "only-connectable", false, "only connectable")
	return cmd
}

func recordTopUpResultCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var note string
	cmd := &cobra.Command{
		Use:  "record-top-up-result <path>",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error { return RecordTopUpResult(store, args[0], OptionalString(note)) })(cmd, args)
		},
	}
	cmd.Flags().StringVar(&note, "note", "", "note")
	return cmd
}

func nextCandidateCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:  "next-candidate",
		RunE: withStore(func(store *Store) error { return PrintNextCandidate(store, asJSON) }),
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "json")
	return cmd
}

func candidatesCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var asJSON bool
	var status string
	cmd := &cobra.Command{
		Use: "candidates",
		RunE: withStore(func(store *Store) error {
			return PrintCandidates(store, asJSON, OptionalString(status))
		}),
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "json")
	cmd.Flags().StringVar(&status, "status", "", "status")
	return cmd
}

func planCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:  "plan",
		RunE: withStore(func(store *Store) error { return PrintPlan(store, asJSON) }),
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "json")
	return cmd
}

func statusCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:  "status",
		RunE: withStore(func(store *Store) error { return PrintRunStatus(store, asJSON) }),
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "json")
	return cmd
}

func finishCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var force bool
	cmd := &cobra.Command{
		Use:  "finish",
		RunE: withStore(func(store *Store) error { return FinishRun(store, force) }),
	}
	cmd.Flags().BoolVar(&force, "force", false, "force")
	return cmd
}

func acceptanceCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	cmd := &cobra.Command{Use: "acceptance"}
	cmd.AddCommand(acceptanceSeedCommand(withStore))
	cmd.AddCommand(&cobra.Command{Use: "seed-history", RunE: withStore(HandleAcceptanceSeedHistory)})
	cmd.AddCommand(acceptanceExportCommand(withStore))
	cmd.AddCommand(&cobra.Command{
		Use:  "import <path>",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error { return HandleAcceptanceImport(store, args[0]) })(cmd, args)
		},
	})
	cmd.AddCommand(acceptanceReportCommand(withStore))
	cmd.AddCommand(acceptanceDraftFollowupsCommand(withStore))
	return cmd
}

func acceptanceSeedCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var includeUnfinished bool
	cmd := &cobra.Command{Use: "seed", RunE: withStore(func(store *Store) error {
		return HandleAcceptanceSeed(store, includeUnfinished)
	})}
	cmd.Flags().BoolVar(&includeUnfinished, "include-unfinished", false, "include unfinished")
	return cmd
}

func acceptanceExportCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var minAgeDays int64 = 7
	var maxAgeDays int64
	var maxAgeSet bool
	var out string
	cmd := &cobra.Command{
		Use: "export",
		RunE: withStore(func(store *Store) error {
			var max *int64
			if maxAgeSet {
				max = &maxAgeDays
			}
			return HandleAcceptanceExport(store, minAgeDays, max, out)
		}),
	}
	cmd.Flags().Int64Var(&minAgeDays, "min-age-days", 7, "min age days")
	cmd.Flags().Int64Var(&maxAgeDays, "max-age-days", 0, "max age days")
	cmd.Flags().StringVar(&out, "out", defaultAcceptanceCandidatesPath, "output")
	cmd.PreRun = func(cmd *cobra.Command, _ []string) {
		maxAgeSet = cmd.Flags().Changed("max-age-days")
	}
	return cmd
}

func acceptanceReportCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var minAgeDays int64
	var maxAgeDays int64
	var maxAgeSet bool
	var asJSON bool
	cmd := &cobra.Command{
		Use: "report",
		RunE: withStore(func(store *Store) error {
			var max *int64
			if maxAgeSet {
				max = &maxAgeDays
			}
			return HandleAcceptanceReport(store, minAgeDays, max, asJSON)
		}),
	}
	cmd.Flags().Int64Var(&minAgeDays, "min-age-days", 0, "min age days")
	cmd.Flags().Int64Var(&maxAgeDays, "max-age-days", 0, "max age days")
	cmd.Flags().BoolVar(&asJSON, "json", false, "json")
	cmd.PreRun = func(cmd *cobra.Command, _ []string) {
		maxAgeSet = cmd.Flags().Changed("max-age-days")
	}
	return cmd
}

func acceptanceDraftFollowupsCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var session, playwriter, researchScript, research, out, outDir, strategyValue string
	var includeDrafted, noPublicWeb bool
	var maxWebResults uint32 = 5
	var delayMS uint64 = 500
	var timeoutMS uint32 = 120000
	cmd := &cobra.Command{
		Use: "draft-followups",
		RunE: withStore(func(store *Store) error {
			strategy, err := ParseDraftStrategy(strategyValue)
			if err != nil {
				return err
			}
			return HandleAcceptanceDraftFollowups(store, AcceptanceDraftFollowupsOptions{
				Session:             OptionalString(session),
				Playwriter:          playwriter,
				ResearchScript:      researchScript,
				Research:            OptionalString(research),
				Out:                 OptionalString(out),
				OutDir:              outDir,
				Strategy:            strategy,
				IncludeDrafted:      includeDrafted,
				PublicWeb:           !noPublicWeb,
				MaxWebResults:       maxWebResults,
				DelayMS:             delayMS,
				PlaywriterTimeoutMS: timeoutMS,
			})
		}),
	}
	cmd.Flags().StringVar(&session, "session", "", "Playwriter session")
	addPlaywriterFlag(cmd.Flags(), &playwriter)
	cmd.Flags().StringVar(&researchScript, "research-script", defaultAcceptedResearchScript, "research script")
	cmd.Flags().StringVar(&research, "research", "", "research artifact")
	cmd.Flags().StringVar(&out, "out", "", "output")
	cmd.Flags().StringVar(&outDir, "out-dir", defaultAcceptedFollowupsOutDir, "output directory")
	cmd.Flags().StringVar(&strategyValue, "strategy", string(DraftStrategyAsapContractV1), "strategy")
	cmd.Flags().BoolVar(&includeDrafted, "include-drafted", false, "include drafted")
	cmd.Flags().BoolVar(&noPublicWeb, "no-public-web", false, "disable public web")
	cmd.Flags().Uint32Var(&maxWebResults, "max-web-results", 5, "max web results")
	cmd.Flags().Uint64Var(&delayMS, "delay-ms", 500, "delay ms")
	cmd.Flags().Uint32Var(&timeoutMS, "playwriter-timeout-ms", 120000, "Playwriter timeout ms")
	return cmd
}

func reservoirCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	cmd := &cobra.Command{Use: "reservoir"}
	cmd.AddCommand(reservoirCaptureCommand(withStore))
	cmd.AddCommand(reservoirImportCaptureCommand(withStore))
	cmd.AddCommand(reservoirFillRunCommand(withStore))
	cmd.AddCommand(reservoirReportCommand(withStore))
	cmd.AddCommand(reservoirClearCommand(withStore))
	return cmd
}

func reservoirCaptureCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var session, playwriter, script, savedSearches, source, url, outDir string
	var pages uint32 = 5
	var stopAfter uint32 = 10
	var limit uint32 = 18
	var rowScrollDelay uint32 = 250
	var onlyConnectable bool
	cmd := &cobra.Command{
		Use: "capture",
		RunE: withStore(func(store *Store) error {
			return HandleReservoirCapture(store, OptionalString(session), playwriter, script, savedSearches, source, OptionalString(url), outDir, CaptureRunOptions{
				Pages:                pages,
				StopAfterConnectable: stopAfter,
				Limit:                limit,
				RowScrollDelayMS:     rowScrollDelay,
				OnlyConnectable:      onlyConnectable,
			})
		}),
	}
	cmd.Flags().StringVar(&session, "session", "", "Playwriter session")
	addPlaywriterFlag(cmd.Flags(), &playwriter)
	cmd.Flags().StringVar(&script, "script", defaultCaptureScript, "capture script")
	cmd.Flags().StringVar(&savedSearches, "saved-searches", defaultSavedSearches, "saved searches")
	cmd.Flags().StringVar(&source, "source", "", "source")
	cmd.Flags().StringVar(&url, "url", "", "url")
	cmd.Flags().StringVar(&outDir, "out-dir", defaultReservoirCaptureOutDir, "output directory")
	cmd.Flags().Uint32Var(&pages, "pages", 5, "pages")
	cmd.Flags().Uint32Var(&stopAfter, "stop-after-connectable", 10, "stop after connectable")
	cmd.Flags().Uint32Var(&limit, "limit", 18, "limit")
	cmd.Flags().Uint32Var(&rowScrollDelay, "row-scroll-delay-ms", 250, "row scroll delay")
	cmd.Flags().BoolVar(&onlyConnectable, "only-connectable", false, "only connectable")
	must(cmd.MarkFlagRequired("source"))
	return cmd
}

func reservoirImportCaptureCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var onlyConnectable bool
	cmd := &cobra.Command{
		Use:  "import-capture <path>",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error { return HandleReservoirImportCapture(store, args[0], onlyConnectable) })(cmd, args)
		},
	}
	cmd.Flags().BoolVar(&onlyConnectable, "only-connectable", false, "only connectable")
	return cmd
}

func reservoirFillRunCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var source string
	var limit int
	var limitSet bool
	cmd := &cobra.Command{
		Use: "fill-run",
		RunE: withStore(func(store *Store) error {
			var limitPtr *int
			if limitSet {
				limitPtr = &limit
			}
			return HandleReservoirFillRun(store, OptionalString(source), limitPtr)
		}),
	}
	cmd.Flags().StringVar(&source, "source", "", "source")
	cmd.Flags().IntVar(&limit, "limit", 0, "limit")
	cmd.PreRun = func(cmd *cobra.Command, _ []string) {
		limitSet = cmd.Flags().Changed("limit")
	}
	return cmd
}

func reservoirReportCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{Use: "report", RunE: withStore(func(store *Store) error {
		return HandleReservoirReport(store, asJSON)
	})}
	cmd.Flags().BoolVar(&asJSON, "json", false, "json")
	return cmd
}

func reservoirClearCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var source string
	cmd := &cobra.Command{Use: "clear", RunE: withStore(func(store *Store) error {
		return HandleReservoirClear(store, OptionalString(source))
	})}
	cmd.Flags().StringVar(&source, "source", "", "source")
	return cmd
}

func tuneSourcesCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var minRawRows uint32 = 50
	var maxYield float64 = 0.05
	var apply bool
	cmd := &cobra.Command{Use: "tune-sources", RunE: withStore(func(store *Store) error {
		return TuneSources(store, minRawRows, maxYield, apply)
	})}
	cmd.Flags().Uint32Var(&minRawRows, "min-raw-rows", 50, "min raw rows")
	cmd.Flags().Float64Var(&maxYield, "max-connectable-yield", 0.05, "max connectable yield")
	cmd.Flags().BoolVar(&apply, "apply", false, "apply")
	return cmd
}

func pendingCleanupCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	cmd := &cobra.Command{Use: "pending-cleanup"}
	cmd.AddCommand(pendingCleanupStartCommand(withStore))
	cmd.AddCommand(&cobra.Command{
		Use:  "import-audit <path>",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error { return PendingCleanupImportAudit(store, args[0]) })(cmd, args)
		},
	})
	cmd.AddCommand(&cobra.Command{
		Use:  "import-capture <path>",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error { return PendingCleanupImportCapture(store, args[0]) })(cmd, args)
		},
	})
	cmd.AddCommand(pendingCleanupPlanCommand(withStore))
	cmd.AddCommand(pendingCleanupNextCommand(withStore))
	cmd.AddCommand(&cobra.Command{
		Use:  "record-withdraw-result <path>",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error { return PendingCleanupRecordWithdrawResult(store, args[0]) })(cmd, args)
		},
	})
	cmd.AddCommand(pendingCleanupWithdrawNextCommand(withStore))
	cmd.AddCommand(pendingCleanupStatusCommand(withStore))
	cmd.AddCommand(&cobra.Command{Use: "report", RunE: withStore(func(store *Store) error {
		run, err := store.LoadPending()
		if err != nil {
			return err
		}
		fmt.Println(RenderPendingReport(run))
		return nil
	})})
	cmd.AddCommand(pendingCleanupFinishCommand(withStore))
	return cmd
}

func pendingCleanupStartCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var maxWithdrawals uint32 = 75
	var thresholdMonths uint32 = 2
	var dateFlag string
	var force bool
	cmd := &cobra.Command{Use: "start", RunE: withStore(func(store *Store) error {
		date, err := parseDateFlagOrToday(dateFlag)
		if err != nil {
			return err
		}
		return PendingCleanupStart(store, maxWithdrawals, thresholdMonths, date, force)
	})}
	cmd.Flags().Uint32Var(&maxWithdrawals, "max-withdrawals", 75, "max withdrawals")
	cmd.Flags().Uint32Var(&thresholdMonths, "threshold-months", 2, "threshold months")
	cmd.Flags().StringVar(&dateFlag, "date", "", "date")
	cmd.Flags().BoolVar(&force, "force", false, "force")
	return cmd
}

func pendingCleanupPlanCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{Use: "plan", RunE: withStore(func(store *Store) error {
		return PendingCleanupPlanCommand(store, asJSON)
	})}
	cmd.Flags().BoolVar(&asJSON, "json", false, "json")
	return cmd
}

func pendingCleanupNextCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{Use: "next", RunE: withStore(func(store *Store) error {
		return PendingCleanupNext(store, asJSON)
	})}
	cmd.Flags().BoolVar(&asJSON, "json", false, "json")
	return cmd
}

func pendingCleanupWithdrawNextCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var session, playwriter, script, outDir string
	var dryRun, allowWithdraw, noRecord bool
	cmd := &cobra.Command{Use: "withdraw-next", RunE: withStore(func(store *Store) error {
		return PendingCleanupWithdrawNext(store, PendingWithdrawNextOptions{
			Session:       OptionalString(session),
			Playwriter:    playwriter,
			Script:        script,
			OutDir:        outDir,
			DryRun:        dryRun,
			AllowWithdraw: allowWithdraw,
			NoRecord:      noRecord,
		})
	})}
	cmd.Flags().StringVar(&session, "session", "", "Playwriter session")
	addPlaywriterFlag(cmd.Flags(), &playwriter)
	cmd.Flags().StringVar(&script, "script", defaultPendingWithdrawScript, "script")
	cmd.Flags().StringVar(&outDir, "out-dir", defaultPendingWithdrawNextOutDir, "out dir")
	cmd.Flags().BoolVar(&dryRun, "dry-run", false, "dry run")
	cmd.Flags().BoolVar(&allowWithdraw, "allow-withdraw", false, "allow withdraw")
	cmd.Flags().BoolVar(&noRecord, "no-record", false, "no record")
	return cmd
}

func pendingCleanupStatusCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{Use: "status", RunE: withStore(func(store *Store) error {
		return PendingCleanupStatus(store, asJSON)
	})}
	cmd.Flags().BoolVar(&asJSON, "json", false, "json")
	return cmd
}

func pendingCleanupFinishCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var force bool
	cmd := &cobra.Command{Use: "finish", RunE: withStore(func(store *Store) error {
		return PendingCleanupFinish(store, force)
	})}
	cmd.Flags().BoolVar(&force, "force", false, "force")
	return cmd
}

func addPlaywriterFlag(flags *pflag.FlagSet, target *string) {
	flags.StringVar(target, "playwriter", defaultPlaywriter, "Playwriter executable")
	flags.StringVar(target, "bunx", defaultPlaywriter, "Playwriter executable alias")
}

func parseDateFlagOrToday(value string) (Date, error) {
	if value == "" {
		return Today(), nil
	}
	return ParseDate(value)
}

func parseUint32Arg(value string, name string) (uint32, error) {
	parsed, err := strconv.ParseUint(value, 10, 32)
	if err != nil {
		return 0, fmt.Errorf("invalid %s: %w", name, err)
	}
	return uint32(parsed), nil
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}
