package outreach

import (
	"fmt"
	"os"
	"strings"

	"github.com/hanifcarroll/linkedin-network-run/internal/app"
)

func resolvePlaywriterSession(playwriter string, session string) (string, error) {
	cleaned := cleanText(session)
	if cleaned != "" && !strings.EqualFold(cleaned, "auto") {
		return cleaned, nil
	}
	workspace, err := os.Getwd()
	if err != nil {
		return "", fmt.Errorf("resolving workspace for Playwriter session auto-discovery: %w", err)
	}
	resolved, created, err := app.ResolvePlaywriterSession(playwriter, workspace)
	if err != nil {
		return "", err
	}
	if created {
		fmt.Printf("session=%s auto_created=true\n", resolved)
	} else {
		fmt.Printf("session=%s auto_created=false\n", resolved)
	}
	return resolved, nil
}
