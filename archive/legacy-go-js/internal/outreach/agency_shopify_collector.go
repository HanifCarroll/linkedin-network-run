package outreach

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"

	"golang.org/x/net/html"
)

const shopifyPartnerDirectoryBaseURL = "https://www.shopify.com/partners/directory/services"

type ShopifyPartnerCollectOptions struct {
	Pages        int
	Limit        int
	ProfileLimit int
	TimeoutMS    int
	Now          time.Time
	Client       *http.Client
}

func CollectShopifyPartnerSource(ctx context.Context, options ShopifyPartnerCollectOptions) (AgencySourceCapture, error) {
	pages := options.Pages
	if pages <= 0 {
		pages = 13
	}
	limit := options.Limit
	if limit <= 0 {
		limit = 120
	}
	profileLimit := options.ProfileLimit
	if profileLimit < 0 {
		profileLimit = 0
	}
	if profileLimit == 0 {
		profileLimit = limit
	}
	now := options.Now
	if now.IsZero() {
		now = time.Now()
	}
	capturedAt := now.Format(time.RFC3339)
	sourceURL := shopifyPartnerDirectoryBaseURL
	client := options.Client
	if client == nil {
		timeout := 10 * time.Second
		if options.TimeoutMS > 0 {
			timeout = time.Duration(options.TimeoutMS) * time.Millisecond
		}
		client = &http.Client{Timeout: timeout}
	}
	rowsByURL := map[string]AgencySourceRow{}
	order := []string{}
	for page := 1; page <= pages && len(order) < limit; page++ {
		pageURL := shopifyPartnerDirectoryPageURL(page)
		node, err := fetchHTML(ctx, client, pageURL)
		if err != nil {
			return AgencySourceCapture{}, err
		}
		rows := parseShopifyPartnerDirectoryRows(node)
		for _, row := range rows {
			if row.SourceURL == nil || cleanText(*row.SourceURL) == "" {
				continue
			}
			key := cleanText(*row.SourceURL)
			if _, exists := rowsByURL[key]; exists {
				continue
			}
			rowsByURL[key] = row
			order = append(order, key)
			if len(order) >= limit {
				break
			}
		}
	}
	rows := []AgencySourceRow{}
	for index, key := range order {
		row := rowsByURL[key]
		if index < profileLimit && row.SourceURL != nil {
			if enriched, err := collectShopifyPartnerProfile(ctx, client, row); err == nil {
				row = enriched
			} else {
				row.Evidence = append(row.Evidence, "profile enrichment failed: "+err.Error())
			}
		}
		rows = append(rows, row)
	}
	return AgencySourceCapture{
		SchemaVersion: AgencySourceSchemaVersion,
		Source:        "Shopify partners - services",
		SourceType:    "shopify_partner",
		CapturedAt:    &capturedAt,
		URL:           &sourceURL,
		Rows:          rows,
	}, nil
}

func shopifyPartnerDirectoryPageURL(page int) string {
	parsed, _ := url.Parse(shopifyPartnerDirectoryBaseURL)
	query := parsed.Query()
	query.Set("page", fmt.Sprintf("%d", page))
	parsed.RawQuery = query.Encode()
	return parsed.String()
}

func parseShopifyPartnerDirectoryRows(node *html.Node) []AgencySourceRow {
	rows := []AgencySourceRow{}
	seen := map[string]bool{}
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if n.Type == html.ElementNode && strings.EqualFold(n.Data, "a") {
			href := attrValue(n, "href")
			if strings.HasPrefix(href, "/partners/directory/partner/") {
				sourceURL := resolveURL(shopifyPartnerDirectoryBaseURL, href)
				name := cleanShopifyPartnerName(firstImageAlt(n))
				if name == "" {
					name = cleanShopifyPartnerName(anchorText(n))
				}
				if cleanText(name) != "" && !seen[sourceURL] {
					seen[sourceURL] = true
					rows = append(rows, AgencySourceRow{
						Name:       cleanText(name),
						SourceURL:  &sourceURL,
						Services:   []string{"Shopify development", "Ecommerce development"},
						FitReasons: []string{"listed in Shopify service partner directory"},
						Evidence:   []string{"directory profile: " + sourceURL},
					})
				}
			}
		}
		for child := n.FirstChild; child != nil; child = child.NextSibling {
			walk(child)
		}
	}
	walk(node)
	sort.SliceStable(rows, func(i, j int) bool {
		return cleanText(rows[i].Name) < cleanText(rows[j].Name)
	})
	return rows
}

func collectShopifyPartnerProfile(ctx context.Context, client *http.Client, row AgencySourceRow) (AgencySourceRow, error) {
	if row.SourceURL == nil {
		return row, nil
	}
	node, err := fetchHTML(ctx, client, *row.SourceURL)
	if err != nil {
		return row, err
	}
	profile := parseShopifyPartnerProfile(node)
	if profile.Name != "" {
		row.Name = cleanShopifyPartnerName(profile.Name)
	}
	if profile.Description != "" {
		row.Description = &profile.Description
		row.Evidence = append(row.Evidence, "profile description: "+profile.Description)
	}
	if profile.Website != "" {
		row.Website = &profile.Website
		row.Evidence = append(row.Evidence, "profile website: "+profile.Website)
	}
	if profile.Location != "" {
		row.Location = &profile.Location
	}
	return row, nil
}

type shopifyPartnerProfile struct {
	Name        string
	Description string
	Website     string
	Location    string
}

func parseShopifyPartnerProfile(node *html.Node) shopifyPartnerProfile {
	profile := shopifyPartnerProfile{}
	links := []shopifyProfileLink{}
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if n.Type == html.ElementNode {
			switch strings.ToLower(n.Data) {
			case "meta":
				key := attrValue(n, "property")
				if key == "" {
					key = attrValue(n, "name")
				}
				content := attrValue(n, "content")
				switch key {
				case "og:title", "twitter:title":
					if profile.Name == "" {
						profile.Name = cleanText(content)
					}
				case "description", "og:description", "twitter:description":
					if profile.Description == "" {
						profile.Description = cleanText(content)
					}
				}
			case "a":
				href := resolveURL(shopifyPartnerDirectoryBaseURL, attrValue(n, "href"))
				if href != "" {
					links = append(links, shopifyProfileLink{Href: href, Text: cleanText(anchorText(n))})
				}
			}
		}
		for child := n.FirstChild; child != nil; child = child.NextSibling {
			walk(child)
		}
	}
	walk(node)
	profile.Website = selectShopifyPartnerWebsite(links)
	return profile
}

func cleanShopifyPartnerName(value string) string {
	cleaned := cleanText(value)
	cleaned = strings.TrimLeft(cleaned, "# ")
	if before, _, ok := strings.Cut(cleaned, " \""); ok && cleanText(before) != "" {
		cleaned = before
	}
	return cleanText(cleaned)
}

type shopifyProfileLink struct {
	Href string
	Text string
}

func selectShopifyPartnerWebsite(links []shopifyProfileLink) string {
	for _, link := range links {
		parsed, err := url.Parse(link.Href)
		if err != nil || parsed.Hostname() == "" {
			continue
		}
		host := strings.TrimPrefix(strings.ToLower(parsed.Hostname()), "www.")
		if shopifyDirectoryHostExcluded(host) {
			continue
		}
		if strings.EqualFold(cleanText(link.Text), "View featured work") {
			continue
		}
		parsed.Fragment = ""
		return parsed.String()
	}
	return ""
}

func shopifyDirectoryHostExcluded(host string) bool {
	if host == "" {
		return true
	}
	excluded := []string{
		"shopify.com",
		"cdn.shopify.com",
		"partners.shopify.com",
		"shopifystatus.com",
		"shopify.dev",
		"shopifyacademy.com",
		"themes.shopify.com",
		"apps.shopify.com",
		"changelog.shopify.com",
		"help.shopify.com",
		"community.shopify.com",
		"facebook.com",
		"twitter.com",
		"x.com",
		"youtube.com",
		"instagram.com",
		"tiktok.com",
		"linkedin.com",
		"pinterest.com",
	}
	for _, value := range excluded {
		if host == value || strings.HasSuffix(host, "."+value) {
			return true
		}
	}
	return false
}

func firstImageAlt(node *html.Node) string {
	var value string
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if value != "" {
			return
		}
		if n.Type == html.ElementNode && strings.EqualFold(n.Data, "img") {
			value = attrValue(n, "alt")
			return
		}
		for child := n.FirstChild; child != nil; child = child.NextSibling {
			walk(child)
		}
	}
	walk(node)
	return cleanText(value)
}

func fetchHTML(ctx context.Context, client *http.Client, pageURL string) (*html.Node, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, pageURL, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "recruiter-agency-outreach/1.0")
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("GET %s returned %d", pageURL, resp.StatusCode)
	}
	node, err := html.Parse(io.LimitReader(resp.Body, 2_000_000))
	if err != nil {
		return nil, fmt.Errorf("parsing %s: %w", pageURL, err)
	}
	return node, nil
}
