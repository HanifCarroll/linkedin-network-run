package app

import (
	"encoding/json"
	"fmt"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
)

func parseUUID(value string) (uuid.UUID, error) {
	parsed, err := uuid.Parse(value)
	if err != nil {
		return uuid.Nil, err
	}
	return parsed, nil
}

func PrettyJSON(value any) (string, error) {
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return "", err
	}
	return string(raw), nil
}

func PercentageSuffix(numerator, denominator uint32) string {
	if denominator == 0 {
		return ""
	}
	return fmt.Sprintf(" (%.1f%%)", float64(numerator)*100.0/float64(denominator))
}

func FormatDurationMS(durationMS uint64) string {
	if durationMS < 1000 {
		return fmt.Sprintf("%dms", durationMS)
	}
	seconds := float64(durationMS) / 1000.0
	if seconds < 60.0 {
		return fmt.Sprintf("%.1fs", seconds)
	}
	return fmt.Sprintf("%.1fm", seconds/60.0)
}

func NormalizeLinkedInURL(value string) string {
	trimmed := strings.TrimSpace(value)
	if parsed, err := url.Parse(trimmed); err == nil && parsed.Scheme != "" {
		parsed.RawQuery = ""
		parsed.Fragment = ""
		return strings.TrimRight(parsed.String(), "/")
	}
	head := strings.Split(strings.Split(trimmed, "?")[0], "#")[0]
	return strings.TrimRight(head, "/")
}

func OptionalString(value string) *string {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return &value
}

func FormatOption[T any](value *T) string {
	if value == nil {
		return "None"
	}
	return fmt.Sprintf("Some(%v)", *value)
}

func FormatDelta(value *int64) string {
	if value == nil {
		return "None"
	}
	return fmt.Sprintf("Some(%d)", *value)
}

func FormatU32Option(value *uint32) string {
	if value == nil {
		return "None"
	}
	return fmt.Sprintf("Some(%d)", *value)
}

func SourceRef(value string) *string {
	copy := value
	return &copy
}

func ParseUint32Flag(value string, name string) (uint32, error) {
	parsed, err := strconv.ParseUint(value, 10, 32)
	if err != nil {
		return 0, fmt.Errorf("invalid %s: %w", name, err)
	}
	return uint32(parsed), nil
}

func TimePtr(value time.Time) *time.Time {
	copy := value
	return &copy
}
