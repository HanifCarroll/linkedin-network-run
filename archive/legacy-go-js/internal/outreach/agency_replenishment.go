package outreach

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type AgencySourceReplenishmentOptions struct {
	SourceDir              string
	ImportLimit            int
	WebsiteEnrichmentLimit int
	WebsiteMaxPages        int
	ForceWebsiteEnrichment bool
	TimeoutMS              int
}

type AgencySourceReplenishmentSummary struct {
	SourceDir         string                         `json:"source_dir"`
	ImportedArtifacts int                            `json:"imported_artifacts"`
	ImportedSources   []AgencySourceImportSummary    `json:"imported_sources"`
	WebsiteEnrichment AgencyWebsiteEnrichmentSummary `json:"website_enrichment"`
	TotalAccounts     int                            `json:"total_accounts"`
	TotalCandidates   int                            `json:"total_candidates"`
}

func ReplenishAgencyPool(ctx context.Context, store *Store, options AgencySourceReplenishmentOptions) (AgencySourceReplenishmentSummary, error) {
	sourceDir := cleanText(options.SourceDir)
	if sourceDir == "" {
		sourceDir = store.AgencySourceDir()
	}
	summary := AgencySourceReplenishmentSummary{
		SourceDir:       sourceDir,
		ImportedSources: []AgencySourceImportSummary{},
	}
	state, err := store.Load()
	if err != nil {
		return summary, err
	}
	paths, err := agencySourceArtifactPaths(sourceDir, options.ImportLimit)
	if err != nil {
		return summary, err
	}
	for _, path := range paths {
		capture, err := LoadAgencySourceCapture(path)
		if err != nil {
			return summary, err
		}
		importSummary, err := ImportAgencySourceCapture(&state, capture)
		if err != nil {
			return summary, err
		}
		summary.ImportedArtifacts++
		summary.ImportedSources = append(summary.ImportedSources, importSummary)
	}
	if options.WebsiteEnrichmentLimit != 0 {
		enrichLimit := options.WebsiteEnrichmentLimit
		if enrichLimit < 0 {
			enrichLimit = 0
		}
		summary.WebsiteEnrichment = EnrichAgencyWebsites(ctx, &state, AgencyWebsiteEnrichmentOptions{
			Limit:     enrichLimit,
			TimeoutMS: options.TimeoutMS,
			MaxPages:  options.WebsiteMaxPages,
			Force:     options.ForceWebsiteEnrichment,
		})
	}
	summary.TotalAccounts = len(state.AgencyAccounts)
	summary.TotalCandidates = len(state.AgencyContactCandidates)
	if len(paths) > 0 || summary.WebsiteEnrichment.Checked > 0 || summary.WebsiteEnrichment.Skipped > 0 {
		if err := store.Save(state); err != nil {
			return summary, err
		}
	}
	return summary, nil
}

func agencySourceArtifactPaths(sourceDir string, limit int) ([]string, error) {
	if limit == 0 {
		return []string{}, nil
	}
	if cleanText(sourceDir) == "" {
		return []string{}, nil
	}
	info, err := os.Stat(sourceDir)
	if os.IsNotExist(err) {
		return []string{}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("checking agency source dir %s: %w", sourceDir, err)
	}
	if !info.IsDir() {
		return nil, fmt.Errorf("agency source dir %s is not a directory", sourceDir)
	}
	entries, err := os.ReadDir(sourceDir)
	if err != nil {
		return nil, fmt.Errorf("reading agency source dir %s: %w", sourceDir, err)
	}
	paths := []string{}
	for _, entry := range entries {
		if entry.IsDir() || !strings.EqualFold(filepath.Ext(entry.Name()), ".json") {
			continue
		}
		paths = append(paths, filepath.Join(sourceDir, entry.Name()))
	}
	sort.Strings(paths)
	if limit > 0 && len(paths) > limit {
		paths = paths[:limit]
	}
	return paths, nil
}
