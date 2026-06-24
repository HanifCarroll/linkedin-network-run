package app

import "testing"

func TestParsePlaywriterSessions(t *testing.T) {
	output := `
ID  Workspace
1   /Users/hanifcarroll/projects/other
2   /Users/hanifcarroll/projects/linkedin-network-automation
`
	sessions := parsePlaywriterSessions(output)
	if len(sessions) != 2 {
		t.Fatalf("sessions = %#v", sessions)
	}
	if sessions[0].ID != "1" || sessions[0].Workspace != "/Users/hanifcarroll/projects/other" {
		t.Fatalf("first session = %#v", sessions[0])
	}
	if sessions[1].ID != "2" || sessions[1].Workspace != "/Users/hanifcarroll/projects/linkedin-network-automation" {
		t.Fatalf("second session = %#v", sessions[1])
	}
}

func TestFirstIntegerToken(t *testing.T) {
	for input, want := range map[string]string{
		"Created session 12": "12",
		"* 7 active":         "7",
		"no id":              "",
	} {
		if got := firstIntegerToken(input); got != want {
			t.Fatalf("firstIntegerToken(%q) = %q want %q", input, got, want)
		}
	}
}
