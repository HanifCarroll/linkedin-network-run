package outreach

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

type AgencyPoolDiagnosis struct {
	GeneratedAt                   time.Time                            `json:"generated_at"`
	StatePath                     string                               `json:"state_path"`
	Counts                        StatusCounts                         `json:"counts"`
	Funnel                        AgencyAccountFunnel                  `json:"funnel"`
	Drilldown                     AgencyDrilldownCounts                `json:"drilldown"`
	ContactCandidateCounts        map[AgencyContactCandidateStatus]int `json:"contact_candidate_counts"`
	ContactCandidateReviewCounts  map[AgencyContactReviewStatus]int    `json:"contact_candidate_review_counts"`
	ContactCandidateSourceCounts  map[string]int                       `json:"contact_candidate_source_counts"`
	WebsiteCandidates             int                                  `json:"website_candidates"`
	QualifiedWebsiteCandidates    int                                  `json:"qualified_website_candidates"`
	ExhaustedWebsiteCandidates    int                                  `json:"exhausted_website_candidates"`
	RetryableBrowserErrorAccounts int                                  `json:"retryable_browser_error_accounts"`
	Accounts                      []AgencyPoolAccountDiagnosis         `json:"accounts"`
}

type AgencyPoolNextAction struct {
	GeneratedAt time.Time               `json:"generated_at"`
	StatePath   string                  `json:"state_path"`
	Action      string                  `json:"action"`
	Reason      string                  `json:"reason"`
	Command     string                  `json:"command,omitempty"`
	Lead        *Lead                   `json:"lead,omitempty"`
	Candidate   *AgencyContactCandidate `json:"candidate,omitempty"`
	Account     *AgencyAccount          `json:"account,omitempty"`
}

type AgencyPoolAccountDiagnosis struct {
	ID                   string              `json:"id"`
	Name                 string              `json:"name"`
	Status               AgencyAccountStatus `json:"status"`
	FitScore             int                 `json:"fit_score"`
	Website              *string             `json:"website,omitempty"`
	Domain               *string             `json:"domain,omitempty"`
	ContactCaptureCount  int                 `json:"contact_capture_count"`
	LastContactStrategy  *string             `json:"last_contact_strategy,omitempty"`
	LastContactError     *string             `json:"last_contact_error,omitempty"`
	Contacts             int                 `json:"contacts"`
	OpenLeads            int                 `json:"open_leads"`
	MessageableOrSent    int                 `json:"messageable_or_sent"`
	NextLinkedInStrategy *string             `json:"next_linkedin_strategy,omitempty"`
	NextStep             string              `json:"next_step"`
}

type agencyPoolLeadCounts struct {
	Contacts          int
	OpenLeads         int
	MessageableOrSent int
}

func agencyPoolCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "agency-pool",
		Short: "Inspect agency account sourcing and contactability",
	}
	cmd.AddCommand(agencyPoolImportSourceCommand(withStore))
	cmd.AddCommand(agencyPoolSourceContractCommand())
	cmd.AddCommand(agencyPoolBuildSourceCommand())
	cmd.AddCommand(agencyPoolImportDirectoryCommand(withStore))
	cmd.AddCommand(agencyPoolCollectShopifyPartnersCommand(withStore))
	cmd.AddCommand(agencyPoolReplenishCommand(withStore))
	cmd.AddCommand(agencyPoolSourceReportCommand(withStore))
	cmd.AddCommand(agencyPoolEnrichWebsitesCommand(withStore))
	cmd.AddCommand(agencyPoolContactsCommand(withStore))
	cmd.AddCommand(agencyPoolReviewContactCommand(withStore))
	cmd.AddCommand(agencyPoolPromoteContactCommand(withStore))
	cmd.AddCommand(agencyPoolPromoteContactsCommand(withStore))
	cmd.AddCommand(agencyPoolNextCommand(withStore))
	cmd.AddCommand(agencyPoolDiagnoseCommand(withStore))
	return cmd
}

func agencyPoolImportSourceCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "import-source <path>",
		Short: "Import structured agency accounts and review-only contact candidates",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error {
				capture, err := LoadAgencySourceCapture(args[0])
				if err != nil {
					return err
				}
				state, err := store.Load()
				if err != nil {
					return err
				}
				summary, err := ImportAgencySourceCapture(&state, capture)
				if err != nil {
					return err
				}
				if err := store.Save(state); err != nil {
					return err
				}
				if asJSON {
					raw, err := json.MarshalIndent(summary, "", "  ")
					if err != nil {
						return err
					}
					fmt.Println(string(raw))
					return nil
				}
				fmt.Printf("source=%s stored=%d updated=%d qualified=%d needs_review=%d rejected=%d contact_candidates_stored=%d contact_candidates_updated=%d total_accounts=%d\n",
					summary.Source,
					summary.Stored,
					summary.Updated,
					summary.Qualified,
					summary.NeedsReview,
					summary.Rejected,
					summary.ContactCandidatesStored,
					summary.ContactCandidatesUpdated,
					summary.TotalAccounts,
				)
				return nil
			})(cmd, args)
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolSourceContractCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "source-contract",
		Short: "Show the canonical agency source artifact contract",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Println(AgencySourceContractMarkdown())
			return nil
		},
	}
}

func agencyPoolBuildSourceCommand() *cobra.Command {
	var csvPath, source, sourceType, sourceURL, out string
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "build-source",
		Short: "Build a canonical agency source artifact from reviewed CSV",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			capture, err := LoadAgencySourceCSV(csvPath, AgencySourceCSVOptions{
				Source:     source,
				SourceType: sourceType,
				URL:        sourceURL,
				CapturedAt: time.Now(),
			})
			if err != nil {
				return err
			}
			if cleanText(out) == "" {
				return fmt.Errorf("--out is required")
			}
			if err := WriteAgencySourceCapture(out, capture); err != nil {
				return err
			}
			summary := SummarizeAgencySourceArtifact(out, capture)
			if asJSON {
				raw, err := json.MarshalIndent(summary, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Printf("artifact=%s source=%s source_type=%s rows=%d warnings=%d\n", summary.Path, summary.Source, summary.SourceType, summary.Rows, len(summary.Warnings))
			return nil
		},
	}
	cmd.Flags().StringVar(&csvPath, "csv", "", "reviewed agency source CSV path")
	cmd.Flags().StringVar(&source, "source", "", "source name")
	cmd.Flags().StringVar(&sourceType, "source-type", "manual_directory", "source type")
	cmd.Flags().StringVar(&sourceURL, "url", "", "source URL")
	cmd.Flags().StringVar(&out, "out", "", "artifact output path")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	must(cmd.MarkFlagRequired("csv"))
	must(cmd.MarkFlagRequired("source"))
	return cmd
}

func agencyPoolImportDirectoryCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var csvPath, source, sourceType, sourceURL, out string
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "import-directory",
		Short: "Build and import a reviewed agency directory CSV",
		Args:  cobra.NoArgs,
		RunE: withStore(func(store *Store) error {
			if cleanText(out) == "" {
				out = store.AgencySourceArtifactPath(source)
			}
			capture, err := LoadAgencySourceCSV(csvPath, AgencySourceCSVOptions{
				Source:     source,
				SourceType: sourceType,
				URL:        sourceURL,
				CapturedAt: time.Now(),
			})
			if err != nil {
				return err
			}
			if err := WriteAgencySourceCapture(out, capture); err != nil {
				return err
			}
			state, err := store.Load()
			if err != nil {
				return err
			}
			importSummary, err := ImportAgencySourceCapture(&state, capture)
			if err != nil {
				return err
			}
			if err := store.Save(state); err != nil {
				return err
			}
			result := struct {
				Artifact AgencySourceArtifactSummary `json:"artifact"`
				Import   AgencySourceImportSummary   `json:"import"`
			}{
				Artifact: SummarizeAgencySourceArtifact(out, capture),
				Import:   importSummary,
			}
			if asJSON {
				raw, err := json.MarshalIndent(result, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Printf("artifact=%s source=%s rows=%d stored=%d updated=%d qualified=%d needs_review=%d rejected=%d contact_candidates_stored=%d contact_candidates_updated=%d total_accounts=%d\n",
				result.Artifact.Path,
				importSummary.Source,
				result.Artifact.Rows,
				importSummary.Stored,
				importSummary.Updated,
				importSummary.Qualified,
				importSummary.NeedsReview,
				importSummary.Rejected,
				importSummary.ContactCandidatesStored,
				importSummary.ContactCandidatesUpdated,
				importSummary.TotalAccounts,
			)
			return nil
		}),
	}
	cmd.Flags().StringVar(&csvPath, "csv", "", "reviewed agency source CSV path")
	cmd.Flags().StringVar(&source, "source", "", "source name")
	cmd.Flags().StringVar(&sourceType, "source-type", "manual_directory", "source type")
	cmd.Flags().StringVar(&sourceURL, "url", "", "source URL")
	cmd.Flags().StringVar(&out, "out", "", "artifact output path; defaults to agency source dir")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	must(cmd.MarkFlagRequired("csv"))
	must(cmd.MarkFlagRequired("source"))
	return cmd
}

func agencyPoolCollectShopifyPartnersCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var pages, limit, profileLimit, timeoutMS int
	var out string
	var importArtifact, asJSON bool
	cmd := &cobra.Command{
		Use:   "collect-shopify-partners",
		Short: "Collect Shopify service partner profiles into a canonical source artifact",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error {
				capture, err := CollectShopifyPartnerSource(cmd.Context(), ShopifyPartnerCollectOptions{
					Pages:        pages,
					Limit:        limit,
					ProfileLimit: profileLimit,
					TimeoutMS:    timeoutMS,
				})
				if err != nil {
					return err
				}
				if cleanText(out) == "" {
					out = store.AgencySourceArtifactPath(capture.Source)
				}
				if err := WriteAgencySourceCapture(out, capture); err != nil {
					return err
				}
				result := struct {
					Artifact AgencySourceArtifactSummary `json:"artifact"`
					Import   *AgencySourceImportSummary  `json:"import,omitempty"`
				}{
					Artifact: SummarizeAgencySourceArtifact(out, capture),
				}
				if importArtifact {
					state, err := store.Load()
					if err != nil {
						return err
					}
					importSummary, err := ImportAgencySourceCapture(&state, capture)
					if err != nil {
						return err
					}
					if err := store.Save(state); err != nil {
						return err
					}
					result.Import = &importSummary
				}
				if asJSON {
					raw, err := json.MarshalIndent(result, "", "  ")
					if err != nil {
						return err
					}
					fmt.Println(string(raw))
					return nil
				}
				if result.Import != nil {
					fmt.Printf("artifact=%s source=%s rows=%d stored=%d updated=%d qualified=%d needs_review=%d rejected=%d total_accounts=%d\n",
						result.Artifact.Path,
						result.Import.Source,
						result.Artifact.Rows,
						result.Import.Stored,
						result.Import.Updated,
						result.Import.Qualified,
						result.Import.NeedsReview,
						result.Import.Rejected,
						result.Import.TotalAccounts,
					)
					return nil
				}
				fmt.Printf("artifact=%s source=%s source_type=%s rows=%d warnings=%d\n", result.Artifact.Path, result.Artifact.Source, result.Artifact.SourceType, result.Artifact.Rows, len(result.Artifact.Warnings))
				return nil
			})(cmd, args)
		},
	}
	cmd.Flags().IntVar(&pages, "pages", 13, "Shopify directory pages to collect")
	cmd.Flags().IntVar(&limit, "limit", 120, "max partner rows")
	cmd.Flags().IntVar(&profileLimit, "profile-limit", 120, "max partner profiles to enrich with website metadata")
	cmd.Flags().IntVar(&timeoutMS, "timeout-ms", 10000, "HTTP timeout per request in milliseconds")
	cmd.Flags().StringVar(&out, "out", "", "artifact output path; defaults to agency source dir")
	cmd.Flags().BoolVar(&importArtifact, "import", false, "import collected artifact into current state")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolReplenishCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var sourceDir string
	var importLimit, enrichLimit, maxPages, timeoutMS int
	var asJSON bool
	var force bool
	cmd := &cobra.Command{
		Use:   "replenish",
		Short: "Import agency source artifacts and run review-only website enrichment",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error {
				if cleanText(sourceDir) == "" {
					sourceDir = store.AgencySourceDir()
				}
				summary, err := ReplenishAgencyPool(cmd.Context(), store, AgencySourceReplenishmentOptions{
					SourceDir:              sourceDir,
					ImportLimit:            importLimit,
					WebsiteEnrichmentLimit: enrichLimit,
					WebsiteMaxPages:        maxPages,
					ForceWebsiteEnrichment: force,
					TimeoutMS:              timeoutMS,
				})
				if err != nil {
					return err
				}
				if asJSON {
					raw, err := json.MarshalIndent(summary, "", "  ")
					if err != nil {
						return err
					}
					fmt.Println(string(raw))
					return nil
				}
				fmt.Printf("source_dir=%s imported_artifacts=%d website_checked=%d website_candidates_stored=%d website_candidates_updated=%d website_errors=%d total_accounts=%d total_candidates=%d\n",
					summary.SourceDir,
					summary.ImportedArtifacts,
					summary.WebsiteEnrichment.Checked,
					summary.WebsiteEnrichment.ContactCandidatesStored,
					summary.WebsiteEnrichment.ContactCandidatesUpdated,
					summary.WebsiteEnrichment.Errors,
					summary.TotalAccounts,
					summary.TotalCandidates,
				)
				return nil
			})(cmd, args)
		},
	}
	cmd.Flags().StringVar(&sourceDir, "source-dir", "", "agency source artifact directory")
	cmd.Flags().IntVar(&importLimit, "import-limit", 10, "max source artifacts to import; 0 skips import, -1 means no cap")
	cmd.Flags().IntVar(&enrichLimit, "enrich-limit", 25, "max agency websites to enrich; -1 means no cap")
	cmd.Flags().IntVar(&maxPages, "max-pages", 8, "max public pages to check per agency website")
	cmd.Flags().IntVar(&timeoutMS, "timeout-ms", 10000, "HTTP timeout per request in milliseconds")
	cmd.Flags().BoolVar(&force, "force", false, "recheck accounts already website-enriched")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolSourceReportCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var out string
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "source-report",
		Short: "Report agency source yield across accounts, contacts, drafts, sends, and dead ends",
		Args:  cobra.NoArgs,
		RunE: withStore(func(store *Store) error {
			if cleanText(out) == "" {
				out = store.AgencySourceReportPath()
			}
			state, err := store.Load()
			if err != nil {
				return err
			}
			report := BuildAgencySourceReport(state, store.StatePath(), out)
			if err := WriteAgencySourceReport(out, report); err != nil {
				return err
			}
			if asJSON {
				raw, err := json.MarshalIndent(report, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Println(RenderAgencySourceReportText(report))
			return nil
		}),
	}
	cmd.Flags().StringVar(&out, "out", "", "source report JSON output path")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolEnrichWebsitesCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var limit int
	var timeoutMS int
	var maxPages int
	var asJSON bool
	var force bool
	cmd := &cobra.Command{
		Use:   "enrich-websites",
		Short: "Discover explicit review-only contacts from agency websites",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return withStore(func(store *Store) error {
				state, err := store.Load()
				if err != nil {
					return err
				}
				summary := EnrichAgencyWebsites(cmd.Context(), &state, AgencyWebsiteEnrichmentOptions{
					Limit:     limit,
					TimeoutMS: timeoutMS,
					MaxPages:  maxPages,
					Force:     force,
				})
				if err := store.Save(state); err != nil {
					return err
				}
				if asJSON {
					raw, err := json.MarshalIndent(summary, "", "  ")
					if err != nil {
						return err
					}
					fmt.Println(string(raw))
					return nil
				}
				fmt.Printf("checked=%d skipped=%d contact_candidates_stored=%d contact_candidates_updated=%d errors=%d\n",
					summary.Checked,
					summary.Skipped,
					summary.ContactCandidatesStored,
					summary.ContactCandidatesUpdated,
					summary.Errors,
				)
				return nil
			})(cmd, args)
		},
	}
	cmd.Flags().IntVar(&limit, "limit", 25, "max agency websites to check")
	cmd.Flags().IntVar(&timeoutMS, "timeout-ms", 10000, "HTTP timeout per request in milliseconds")
	cmd.Flags().IntVar(&maxPages, "max-pages", 8, "max public pages to check per agency website")
	cmd.Flags().BoolVar(&force, "force", false, "recheck accounts already website-enriched")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolContactsCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var limit int
	var status string
	var reviewStatus string
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "contacts",
		Short: "List review-only agency contact candidates",
		Args:  cobra.NoArgs,
		RunE: withStore(func(store *Store) error {
			state, err := store.Load()
			if err != nil {
				return err
			}
			candidates, err := agencyContactCandidatesForReview(state, status, reviewStatus, limit)
			if err != nil {
				return err
			}
			if asJSON {
				raw, err := json.MarshalIndent(candidates, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Println(RenderAgencyContactCandidatesText(candidates))
			return nil
		}),
	}
	cmd.Flags().IntVar(&limit, "limit", 20, "max contact candidate rows")
	cmd.Flags().StringVar(&status, "status", "", "candidate status filter")
	cmd.Flags().StringVar(&reviewStatus, "review-status", string(AgencyContactReviewStatusNeedsReview), "review status filter")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolReviewContactCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var candidateID string
	var reviewStatus string
	var name string
	var title string
	var note string
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "review-contact",
		Short: "Approve, reject, or annotate a review-only agency contact candidate",
		Args:  cobra.NoArgs,
		RunE: withStore(func(store *Store) error {
			parsedStatus, ok, err := parseAgencyContactReviewStatus(reviewStatus)
			if err != nil {
				return err
			}
			if !ok {
				return fmt.Errorf("--review-status is required")
			}
			state, err := store.Load()
			if err != nil {
				return err
			}
			candidate, err := ReviewAgencyContactCandidate(&state, AgencyContactReviewOptions{
				CandidateID:  candidateID,
				ReviewStatus: parsedStatus,
				Name:         name,
				Title:        title,
				Note:         note,
			})
			if err != nil {
				return err
			}
			if err := store.Save(state); err != nil {
				return err
			}
			if asJSON {
				raw, err := json.MarshalIndent(candidate, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Printf("candidate=%s review_status=%s status=%s name=%s title=%s\n",
				candidate.ID,
				candidate.ReviewStatus,
				candidate.Status,
				stringOrDash(candidate.Name),
				stringOrDash(candidate.Title),
			)
			return nil
		}),
	}
	cmd.Flags().StringVar(&candidateID, "candidate-id", "", "agency contact candidate id")
	cmd.Flags().StringVar(&reviewStatus, "review-status", string(AgencyContactReviewStatusApproved), "review status")
	cmd.Flags().StringVar(&name, "name", "", "reviewed person name")
	cmd.Flags().StringVar(&title, "title", "", "reviewed person title")
	cmd.Flags().StringVar(&note, "note", "", "review note")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolPromoteContactCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var candidateID string
	var draft bool
	var maxPerAgency int
	var allowMultiplePerAgency bool
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "promote-contact",
		Short: "Promote one approved LinkedIn-profile candidate into a draftable agency lead",
		Args:  cobra.NoArgs,
		RunE: withStore(func(store *Store) error {
			if cleanText(candidateID) == "" {
				return fmt.Errorf("--candidate-id is required")
			}
			state, err := store.Load()
			if err != nil {
				return err
			}
			summary, err := PromoteAgencyContactCandidates(&state, AgencyContactPromotionOptions{
				CandidateIDs:           []string{candidateID},
				Draft:                  draft,
				MaxPerAgency:           maxPerAgency,
				AllowMultiplePerAgency: allowMultiplePerAgency,
			})
			if err != nil {
				return err
			}
			if err := store.Save(state); err != nil {
				return err
			}
			if asJSON {
				raw, err := json.MarshalIndent(summary, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Println(RenderAgencyContactPromotionSummaryText(summary))
			return nil
		}),
	}
	cmd.Flags().StringVar(&candidateID, "candidate-id", "", "agency contact candidate id")
	cmd.Flags().BoolVar(&draft, "draft", false, "generate a draft for promoted leads")
	cmd.Flags().IntVar(&maxPerAgency, "max-per-agency", 1, "max active outreach leads per agency account")
	cmd.Flags().BoolVar(&allowMultiplePerAgency, "allow-multiple-per-agency", false, "disable the active lead cap for this promotion")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolPromoteContactsCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var candidateIDs []string
	var limit int
	var draft bool
	var maxPerAgency int
	var allowMultiplePerAgency bool
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "promote-contacts",
		Short: "Promote approved LinkedIn-profile candidates into draftable agency leads",
		Args:  cobra.NoArgs,
		RunE: withStore(func(store *Store) error {
			state, err := store.Load()
			if err != nil {
				return err
			}
			summary, err := PromoteAgencyContactCandidates(&state, AgencyContactPromotionOptions{
				CandidateIDs:           candidateIDs,
				Limit:                  limit,
				Draft:                  draft,
				MaxPerAgency:           maxPerAgency,
				AllowMultiplePerAgency: allowMultiplePerAgency,
			})
			if err != nil {
				return err
			}
			if err := store.Save(state); err != nil {
				return err
			}
			if asJSON {
				raw, err := json.MarshalIndent(summary, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Println(RenderAgencyContactPromotionSummaryText(summary))
			return nil
		}),
	}
	cmd.Flags().StringSliceVar(&candidateIDs, "candidate-id", []string{}, "candidate id to promote; repeat or comma-separate")
	cmd.Flags().IntVar(&limit, "limit", 20, "max approved candidates to promote when candidate ids are omitted")
	cmd.Flags().BoolVar(&draft, "draft", false, "generate drafts for promoted leads")
	cmd.Flags().IntVar(&maxPerAgency, "max-per-agency", 1, "max active outreach leads per agency account")
	cmd.Flags().BoolVar(&allowMultiplePerAgency, "allow-multiple-per-agency", false, "disable the active lead cap for this promotion")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolNextCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "next",
		Short: "Show the next agency-pool action and exact command",
		Args:  cobra.NoArgs,
		RunE: withStore(func(store *Store) error {
			state, err := store.Load()
			if err != nil {
				return err
			}
			next := BuildAgencyPoolNextAction(state, store.StatePath())
			if asJSON {
				raw, err := json.MarshalIndent(next, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Println(RenderAgencyPoolNextActionText(next))
			return nil
		}),
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func agencyPoolDiagnoseCommand(withStore func(func(*Store) error) func(*cobra.Command, []string) error) *cobra.Command {
	var limit int
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "diagnose",
		Short: "Show agency account pool health and next actions",
		Args:  cobra.NoArgs,
		RunE: withStore(func(store *Store) error {
			state, err := store.Load()
			if err != nil {
				return err
			}
			diagnosis := BuildAgencyPoolDiagnosis(state, store.StatePath(), limit)
			if asJSON {
				raw, err := json.MarshalIndent(diagnosis, "", "  ")
				if err != nil {
					return err
				}
				fmt.Println(string(raw))
				return nil
			}
			fmt.Println(RenderAgencyPoolDiagnosisText(diagnosis))
			return nil
		}),
	}
	cmd.Flags().IntVar(&limit, "limit", 20, "max account rows")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print JSON")
	return cmd
}

func BuildAgencyPoolNextAction(state OutreachState, statePath string) AgencyPoolNextAction {
	state.Normalize()
	now := time.Now()
	if leads := readyLeads(state, "agency"); len(leads) > 0 {
		lead := leads[0]
		return AgencyPoolNextAction{
			GeneratedAt: now,
			StatePath:   statePath,
			Action:      "send_ready_agency_lead",
			Reason:      "Agency lead is already validated as messageable.",
			Command:     fmt.Sprintf("/Users/hanifcarroll/.local/bin/recruiter-agency-outreach send-message --lead-id %s --session auto --allow-send --timeout-ms 60000", lead.ID),
			Lead:        &lead,
		}
	}
	if leads := leadsForMessageValidation(state, "agency"); len(leads) > 0 {
		lead := leads[0]
		return AgencyPoolNextAction{
			GeneratedAt: now,
			StatePath:   statePath,
			Action:      "validate_drafted_agency_lead",
			Reason:      "Agency lead has a draft and needs a dry-run messageability check before any real send.",
			Command:     fmt.Sprintf("/Users/hanifcarroll/.local/bin/recruiter-agency-outreach send-message --lead-id %s --session auto --timeout-ms 60000", lead.ID),
			Lead:        &lead,
		}
	}
	if candidates := agencyContactCandidatesReadyForPromotion(state); len(candidates) > 0 {
		candidate := candidates[0]
		return AgencyPoolNextAction{
			GeneratedAt: now,
			StatePath:   statePath,
			Action:      "promote_approved_agency_contact",
			Reason:      "Reviewed agency website contact can be promoted into a drafted lead.",
			Command:     fmt.Sprintf("/Users/hanifcarroll/.local/bin/recruiter-agency-outreach agency-pool promote-contact --candidate-id %s --draft", candidate.ID),
			Candidate:   &candidate,
		}
	}
	if candidates := agencyContactCandidatesNeedingReview(state); len(candidates) > 0 {
		candidate := candidates[0]
		return AgencyPoolNextAction{
			GeneratedAt: now,
			StatePath:   statePath,
			Action:      "review_agency_website_contacts",
			Reason:      "Agency website contacts need human review before promotion.",
			Command:     "/Users/hanifcarroll/.local/bin/recruiter-agency-outreach agency-pool contacts --status website_contact_candidate --review-status needs_review --limit 20",
			Candidate:   &candidate,
		}
	}
	diagnosis := BuildAgencyPoolDiagnosis(state, statePath, 20)
	if diagnosis.WebsiteCandidates > 0 {
		return AgencyPoolNextAction{
			GeneratedAt: now,
			StatePath:   statePath,
			Action:      "enrich_agency_websites",
			Reason:      fmt.Sprintf("%d agency account(s) have websites that can be checked for explicit contacts.", diagnosis.WebsiteCandidates),
			Command:     "/Users/hanifcarroll/.local/bin/recruiter-agency-outreach agency-pool enrich-websites --limit 25",
		}
	}
	if diagnosis.Funnel.Qualified == 0 || diagnosis.Drilldown.QualifiedRemaining == 0 {
		return AgencyPoolNextAction{
			GeneratedAt: now,
			StatePath:   statePath,
			Action:      "collect_import_agency_source_batch",
			Reason:      "No ready, drafted, reviewable, enrichable, or qualified agency account work is available; import a new source batch.",
			Command:     fmt.Sprintf("/Users/hanifcarroll/.local/bin/recruiter-agency-outreach agency-pool collect-shopify-partners --pages 13 --limit 120 --profile-limit 120 --out %s --import", filepath.Join(filepath.Dir(statePath), "agency-sources", time.Now().Format("2006-01-02")+"-shopify-partners-services.json")),
		}
	}
	recommendation := RecommendNextRun(state, statePath, 5, 5, true)
	if recommendation.ShouldRetry {
		return AgencyPoolNextAction{
			GeneratedAt: now,
			StatePath:   statePath,
			Action:      "run_agency_sourcing",
			Reason:      recommendation.Reason,
			Command:     recommendation.Command,
		}
	}
	return AgencyPoolNextAction{
		GeneratedAt: now,
		StatePath:   statePath,
		Action:      "no_action",
		Reason:      "No agency ready lead, drafted lead, reviewable contact, enrichable website, or retry recommendation is available.",
	}
}

func RenderAgencyPoolNextActionText(next AgencyPoolNextAction) string {
	lines := []string{
		"action=" + next.Action,
		"reason=" + cleanText(next.Reason),
		"state=" + cleanText(next.StatePath),
	}
	if next.Command != "" {
		lines = append(lines, "command="+next.Command)
	}
	if next.Lead != nil {
		lines = append(lines,
			"lead="+next.Lead.ID,
			"lead_name="+cleanText(next.Lead.Name),
			"lead_status="+string(next.Lead.Status),
			"message_status="+string(next.Lead.MessageStatus),
		)
		if next.Lead.AgencyAccountName != nil {
			lines = append(lines, "agency="+cleanText(*next.Lead.AgencyAccountName))
		}
	}
	if next.Candidate != nil {
		lines = append(lines,
			"candidate="+next.Candidate.ID,
			"candidate_status="+string(next.Candidate.Status),
			"candidate_review_status="+string(next.Candidate.ReviewStatus),
			"agency="+cleanText(next.Candidate.AgencyAccountName),
		)
		if next.Candidate.ProfileURL != nil {
			lines = append(lines, "profile_url="+cleanText(*next.Candidate.ProfileURL))
		}
		if next.Candidate.SourceURL != nil {
			lines = append(lines, "source_url="+cleanText(*next.Candidate.SourceURL))
		}
	}
	if next.Account != nil {
		lines = append(lines,
			"account="+next.Account.ID,
			"account_name="+cleanText(next.Account.Name),
			"account_status="+string(next.Account.Status),
		)
	}
	return strings.Join(lines, "\n")
}

func agencyContactCandidatesReadyForPromotion(state OutreachState) []AgencyContactCandidate {
	candidates := []AgencyContactCandidate{}
	for _, candidate := range state.AgencyContactCandidates {
		if candidate.Status == AgencyContactCandidateStatusWebsiteContactCandidate && candidate.ReviewStatus == AgencyContactReviewStatusApproved {
			candidates = append(candidates, candidate)
		}
	}
	sortAgencyContactCandidates(candidates)
	return candidates
}

func agencyContactCandidatesNeedingReview(state OutreachState) []AgencyContactCandidate {
	candidates := []AgencyContactCandidate{}
	for _, candidate := range state.AgencyContactCandidates {
		if candidate.Status == AgencyContactCandidateStatusWebsiteContactCandidate && candidate.ReviewStatus == AgencyContactReviewStatusNeedsReview {
			candidates = append(candidates, candidate)
		}
	}
	sortAgencyContactCandidates(candidates)
	return candidates
}

func BuildAgencyPoolDiagnosis(state OutreachState, statePath string, limit int) AgencyPoolDiagnosis {
	state.Normalize()
	counts := Counts(state)
	leadCounts := agencyPoolLeadCountsByAccount(state)
	diagnosis := AgencyPoolDiagnosis{
		GeneratedAt:                  time.Now(),
		StatePath:                    statePath,
		Counts:                       counts,
		Funnel:                       agencyAccountFunnelCounts(state),
		Drilldown:                    agencyDrilldownCounts(state),
		ContactCandidateCounts:       counts.ByAgencyContactCandidateStatus,
		ContactCandidateReviewCounts: counts.ByAgencyContactCandidateReviewStatus,
		ContactCandidateSourceCounts: counts.ByAgencyContactCandidateSource,
		Accounts:                     []AgencyPoolAccountDiagnosis{},
	}
	for _, account := range state.AgencyAccounts {
		counts := leadCounts[account.ID]
		item := buildAgencyPoolAccountDiagnosis(account, counts)
		if item.NextStep == "website_enrichment" {
			diagnosis.WebsiteCandidates++
			switch account.Status {
			case AgencyAccountStatusQualified:
				diagnosis.QualifiedWebsiteCandidates++
			case AgencyAccountStatusExhausted:
				diagnosis.ExhaustedWebsiteCandidates++
			}
		}
		if account.LastContactError != nil && cleanText(*account.LastContactError) != "" {
			diagnosis.RetryableBrowserErrorAccounts++
		}
		if item.NextStep == "no_action" {
			continue
		}
		diagnosis.Accounts = append(diagnosis.Accounts, item)
	}
	sort.SliceStable(diagnosis.Accounts, func(i, j int) bool {
		left := agencyPoolNextStepRank(diagnosis.Accounts[i].NextStep)
		right := agencyPoolNextStepRank(diagnosis.Accounts[j].NextStep)
		if left != right {
			return left < right
		}
		if diagnosis.Accounts[i].FitScore != diagnosis.Accounts[j].FitScore {
			return diagnosis.Accounts[i].FitScore > diagnosis.Accounts[j].FitScore
		}
		return diagnosis.Accounts[i].Name < diagnosis.Accounts[j].Name
	})
	if limit > 0 && len(diagnosis.Accounts) > limit {
		diagnosis.Accounts = diagnosis.Accounts[:limit]
	}
	return diagnosis
}

func buildAgencyPoolAccountDiagnosis(account AgencyAccount, counts agencyPoolLeadCounts) AgencyPoolAccountDiagnosis {
	item := AgencyPoolAccountDiagnosis{
		ID:                  account.ID,
		Name:                account.Name,
		Status:              account.Status,
		FitScore:            account.FitScore,
		Website:             account.Website,
		Domain:              account.Domain,
		ContactCaptureCount: account.ContactCaptureCount,
		LastContactStrategy: account.LastContactStrategy,
		LastContactError:    account.LastContactError,
		Contacts:            counts.Contacts,
		OpenLeads:           counts.OpenLeads,
		MessageableOrSent:   counts.MessageableOrSent,
		NextStep:            "no_action",
	}
	if strategy, ok := nextAgencyContactSearchStrategy(account); ok {
		item.NextLinkedInStrategy = &strategy.Name
	}
	switch {
	case account.Status == AgencyAccountStatusQualified && counts.OpenLeads > 0:
		item.NextStep = "validate_or_send_open_lead"
	case account.Status == AgencyAccountStatusQualified && account.LastContactError != nil && cleanText(*account.LastContactError) != "":
		item.NextStep = "retry_linkedin_contact_search"
	case account.Status == AgencyAccountStatusQualified && item.NextLinkedInStrategy != nil:
		item.NextStep = "continue_linkedin_contact_search:" + *item.NextLinkedInStrategy
	case accountHasWebsite(account) && counts.Contacts == 0 && agencyAccountWebsiteEnrichmentEligible(account):
		item.NextStep = "website_enrichment"
	case account.Status == AgencyAccountStatusNeedsReview:
		item.NextStep = "review_account_fit"
	}
	return item
}

func agencyPoolLeadCountsByAccount(state OutreachState) map[string]agencyPoolLeadCounts {
	state.Normalize()
	byAccount := map[string]agencyPoolLeadCounts{}
	for _, lead := range state.Leads {
		if lead.AgencyAccountID == nil || cleanText(*lead.AgencyAccountID) == "" || bucketForLead(lead) != "agency" || lead.Status != LeadStatusEligible {
			continue
		}
		accountID := cleanText(*lead.AgencyAccountID)
		counts := byAccount[accountID]
		counts.Contacts++
		if !isTerminalMessageStatus(lead.MessageStatus) || lead.MessageStatus == MessageStatusDryRunReady {
			counts.OpenLeads++
		}
		switch lead.MessageStatus {
		case MessageStatusDryRunReady, MessageStatusSent, MessageStatusManuallySent:
			counts.MessageableOrSent++
		}
		byAccount[accountID] = counts
	}
	return byAccount
}

func agencyAccountWebsiteEnrichmentEligible(account AgencyAccount) bool {
	return account.Status == AgencyAccountStatusQualified || account.Status == AgencyAccountStatusExhausted
}

func accountHasWebsite(account AgencyAccount) bool {
	return account.Website != nil && cleanText(*account.Website) != ""
}

func agencyPoolNextStepRank(step string) int {
	switch {
	case step == "validate_or_send_open_lead":
		return 0
	case step == "retry_linkedin_contact_search":
		return 1
	case strings.HasPrefix(step, "continue_linkedin_contact_search:"):
		return 2
	case step == "website_enrichment":
		return 3
	case step == "review_account_fit":
		return 4
	default:
		return 9
	}
}

func RenderAgencyPoolDiagnosisText(diagnosis AgencyPoolDiagnosis) string {
	lines := []string{
		fmt.Sprintf("state=%s", diagnosis.StatePath),
		fmt.Sprintf("agency_accounts=qualified %d; needs_review %d; rejected %d; exhausted %d",
			diagnosis.Counts.ByAgencyAccountStatus[AgencyAccountStatusQualified],
			diagnosis.Counts.ByAgencyAccountStatus[AgencyAccountStatusNeedsReview],
			diagnosis.Counts.ByAgencyAccountStatus[AgencyAccountStatusRejected],
			diagnosis.Counts.ByAgencyAccountStatus[AgencyAccountStatusExhausted],
		),
		fmt.Sprintf("contactability=qualified %d; with_contacts %d; with_messageable_or_sent %d; exhausted_without_contacts %d; exhausted_after_contact_attempts %d",
			diagnosis.Funnel.Qualified,
			diagnosis.Funnel.WithContacts,
			diagnosis.Funnel.WithMessageableOrSentContacts,
			diagnosis.Funnel.ExhaustedWithoutContacts,
			diagnosis.Funnel.ExhaustedAfterContactAttempts,
		),
		fmt.Sprintf("drilldown=not_searched %d; founder_recent %d; executive_broad %d; resource_broad %d; contacts_found %d; no_contacts_found %d; browser_error_retryable %d",
			diagnosis.Drilldown.NotSearchedYet,
			diagnosis.Drilldown.SearchedFounderRecent,
			diagnosis.Drilldown.SearchedExecutiveBroad,
			diagnosis.Drilldown.SearchedResourceBroad,
			diagnosis.Drilldown.ContactsFound,
			diagnosis.Drilldown.NoContactsFound,
			diagnosis.Drilldown.BrowserErrorRetryable,
		),
		fmt.Sprintf("website_candidates=all %d; qualified %d; exhausted %d",
			diagnosis.WebsiteCandidates,
			diagnosis.QualifiedWebsiteCandidates,
			diagnosis.ExhaustedWebsiteCandidates,
		),
		"review_only_contacts=" + renderAgencyContactCandidateStatusCounts(diagnosis.ContactCandidateCounts),
		"contact_review=" + renderAgencyContactReviewStatusCounts(diagnosis.ContactCandidateReviewCounts),
		"contact_sources=" + renderStringCounts(diagnosis.ContactCandidateSourceCounts),
		fmt.Sprintf("retryable_browser_error_accounts=%d", diagnosis.RetryableBrowserErrorAccounts),
		"next_accounts:",
		"id\tscore\tstatus\tcaptures\tcontacts\topen_leads\tlast_strategy\twebsite\tnext_step\tname",
	}
	for _, account := range diagnosis.Accounts {
		lines = append(lines, fmt.Sprintf("%s\t%d\t%s\t%d\t%d\t%d\t%s\t%s\t%s\t%s",
			account.ID,
			account.FitScore,
			account.Status,
			account.ContactCaptureCount,
			account.Contacts,
			account.OpenLeads,
			stringOrDash(account.LastContactStrategy),
			stringOrDash(account.Website),
			cleanText(account.NextStep),
			cleanText(account.Name),
		))
	}
	return strings.Join(lines, "\n")
}

func agencyContactCandidatesForReview(state OutreachState, status string, reviewStatus string, limit int) ([]AgencyContactCandidate, error) {
	state.Normalize()
	candidateStatus, filterByStatus, err := parseAgencyContactCandidateStatus(status)
	if err != nil {
		return nil, err
	}
	candidateReviewStatus, filterByReviewStatus, err := parseAgencyContactReviewStatus(reviewStatus)
	if err != nil {
		return nil, err
	}
	items := []AgencyContactCandidate{}
	for _, candidate := range state.AgencyContactCandidates {
		if filterByStatus && candidate.Status != candidateStatus {
			continue
		}
		if filterByReviewStatus && candidate.ReviewStatus != candidateReviewStatus {
			continue
		}
		items = append(items, candidate)
	}
	sortAgencyContactCandidates(items)
	if limit > 0 && len(items) > limit {
		items = items[:limit]
	}
	return items, nil
}

func parseAgencyContactCandidateStatus(value string) (AgencyContactCandidateStatus, bool, error) {
	cleaned := cleanText(value)
	if cleaned == "" {
		return "", false, nil
	}
	status := AgencyContactCandidateStatus(cleaned)
	if !validAgencyContactCandidateStatus(status) {
		return "", false, fmt.Errorf("invalid agency contact candidate status %q", cleaned)
	}
	return status, true, nil
}

func parseAgencyContactReviewStatus(value string) (AgencyContactReviewStatus, bool, error) {
	cleaned := cleanText(value)
	if cleaned == "" {
		return "", false, nil
	}
	status := AgencyContactReviewStatus(cleaned)
	switch status {
	case AgencyContactReviewStatusNeedsReview,
		AgencyContactReviewStatusApproved,
		AgencyContactReviewStatusRejected,
		AgencyContactReviewStatusConverted:
		return status, true, nil
	default:
		return "", false, fmt.Errorf("invalid agency contact review status %q", cleaned)
	}
}

func RenderAgencyContactCandidatesText(candidates []AgencyContactCandidate) string {
	lines := []string{
		fmt.Sprintf("agency_contact_candidates=%d", len(candidates)),
		"id\trank\treview_status\tstatus\tsource\tagency\temail\tprofile_url\tcontact_url\tform_action\tpromoted_lead\tname\ttitle",
	}
	for _, candidate := range candidates {
		lines = append(lines, fmt.Sprintf("%s\t%d\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s",
			candidate.ID,
			agencyContactCandidateRank(candidate),
			candidate.ReviewStatus,
			candidate.Status,
			cleanText(candidate.Source),
			cleanText(candidate.AgencyAccountName),
			stringOrDash(candidate.Email),
			stringOrDash(candidate.ProfileURL),
			stringOrDash(candidate.ContactURL),
			stringOrDash(candidate.FormAction),
			stringOrDash(candidate.PromotedLeadID),
			stringOrDash(candidate.Name),
			stringOrDash(candidate.Title),
		))
	}
	return strings.Join(lines, "\n")
}

func RenderAgencyContactPromotionSummaryText(summary AgencyContactPromotionSummary) string {
	lines := []string{
		fmt.Sprintf("stored=%d updated=%d drafted=%d skipped=%d", summary.Stored, summary.Updated, summary.Drafted, len(summary.Skipped)),
	}
	if len(summary.Leads) > 0 {
		lines = append(lines, "leads:")
		for _, lead := range summary.Leads {
			lines = append(lines, fmt.Sprintf("%s\t%s\t%s\t%s\t%s",
				lead.ID,
				lead.Name,
				lead.LeadType,
				stringOrDash(lead.Title),
				stringOrDash(lead.ProfileURL),
			))
		}
	}
	if len(summary.Skipped) > 0 {
		lines = append(lines, "skipped:")
		for _, skipped := range summary.Skipped {
			lines = append(lines, fmt.Sprintf("%s\t%s", skipped.CandidateID, skipped.Reason))
		}
	}
	return strings.Join(lines, "\n")
}

func renderAgencyContactCandidateStatusCounts(counts map[AgencyContactCandidateStatus]int) string {
	parts := []string{}
	for _, status := range []AgencyContactCandidateStatus{
		AgencyContactCandidateStatusWebsiteContactCandidate,
		AgencyContactCandidateStatusGenericInbox,
		AgencyContactCandidateStatusContactForm,
		AgencyContactCandidateStatusRejected,
		AgencyContactCandidateStatusConverted,
	} {
		parts = append(parts, fmt.Sprintf("%s %d", status, counts[status]))
	}
	return strings.Join(parts, "; ")
}

func renderAgencyContactReviewStatusCounts(counts map[AgencyContactReviewStatus]int) string {
	parts := []string{}
	for _, status := range []AgencyContactReviewStatus{
		AgencyContactReviewStatusNeedsReview,
		AgencyContactReviewStatusApproved,
		AgencyContactReviewStatusRejected,
		AgencyContactReviewStatusConverted,
	} {
		parts = append(parts, fmt.Sprintf("%s %d", status, counts[status]))
	}
	return strings.Join(parts, "; ")
}

func renderStringCounts(counts map[string]int) string {
	if len(counts) == 0 {
		return "-"
	}
	keys := []string{}
	for key := range counts {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	parts := []string{}
	for _, key := range keys {
		parts = append(parts, fmt.Sprintf("%s %d", key, counts[key]))
	}
	return strings.Join(parts, "; ")
}

func stringOrDash(value *string) string {
	if value == nil || cleanText(*value) == "" {
		return "-"
	}
	return cleanText(*value)
}
