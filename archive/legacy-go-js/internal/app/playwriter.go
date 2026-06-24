package app

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

func IsBunxPath(path string) bool {
	name := filepath.Base(path)
	return name == "bunx" || name == "bunx.exe"
}

func PlaywriterCommand(path string) *exec.Cmd {
	if runtime.GOOS == "windows" {
		return exec.Command(path)
	}
	return exec.Command(path)
}

func RunPlaywriterConfig(playwriter string, session string, configJS string) error {
	args := []string{}
	if IsBunxPath(playwriter) {
		args = append(args, "playwriter@latest")
	}
	args = append(args, "-s", session, "-e", configJS)
	cmd := exec.Command(playwriter, args...)
	if err := runCommand(cmd, "running Playwriter config"); err != nil {
		return err
	}
	return nil
}

func ResetPlaywriterSession(playwriter string, session string) error {
	args := []string{}
	if IsBunxPath(playwriter) {
		args = append(args, "playwriter@latest")
	}
	args = append(args, "session", "reset", session)
	cmd := exec.Command(playwriter, args...)
	if err := runCommand(cmd, "resetting Playwriter session"); err != nil {
		return err
	}
	return nil
}

type PlaywriterSession struct {
	ID        string
	RawLine   string
	Workspace string
}

func ResolvePlaywriterSession(playwriter string, workspace string) (string, bool, error) {
	workspace = strings.TrimSpace(workspace)
	sessions, err := ListPlaywriterSessions(playwriter, 8*time.Second)
	if err == nil {
		var firstUnknown string
		for _, session := range sessions {
			if workspace == "" || samePath(session.Workspace, workspace) {
				return session.ID, false, nil
			}
			if session.Workspace == "" && firstUnknown == "" {
				firstUnknown = session.ID
			}
		}
		if firstUnknown != "" {
			return firstUnknown, false, nil
		}
	}
	created, createErr := CreatePlaywriterSession(playwriter, workspace, 20*time.Second)
	if createErr != nil {
		if err != nil {
			return "", false, fmt.Errorf("listing Playwriter sessions failed (%v) and creating a new session failed: %w", err, createErr)
		}
		return "", false, createErr
	}
	return created, true, nil
}

func ListPlaywriterSessions(playwriter string, timeout time.Duration) ([]PlaywriterSession, error) {
	output, err := runPlaywriterCaptureOutput(playwriter, []string{"session", "list"}, "", timeout)
	if err != nil {
		return nil, err
	}
	return parsePlaywriterSessions(output), nil
}

func CreatePlaywriterSession(playwriter string, workspace string, timeout time.Duration) (string, error) {
	output, err := runPlaywriterCaptureOutput(playwriter, []string{"session", "new"}, workspace, timeout)
	if err != nil {
		return "", err
	}
	if id := firstIntegerToken(output); id != "" {
		return id, nil
	}
	sessions, err := ListPlaywriterSessions(playwriter, 8*time.Second)
	if err != nil {
		return "", err
	}
	for _, session := range sessions {
		if workspace == "" || session.Workspace == "" || samePath(session.Workspace, workspace) {
			return session.ID, nil
		}
	}
	return "", fmt.Errorf("created Playwriter session but could not determine session id")
}

func runPlaywriterCaptureOutput(playwriter string, args []string, workspace string, timeout time.Duration) (string, error) {
	fullArgs := []string{}
	if IsBunxPath(playwriter) {
		fullArgs = append(fullArgs, "playwriter@latest")
	}
	fullArgs = append(fullArgs, args...)
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, playwriter, fullArgs...)
	if strings.TrimSpace(workspace) != "" {
		cmd.Dir = workspace
	}
	var output bytes.Buffer
	cmd.Stdout = &output
	cmd.Stderr = &output
	if err := cmd.Run(); err != nil {
		if ctx.Err() == context.DeadlineExceeded {
			return output.String(), fmt.Errorf("Playwriter %s timed out after %s", strings.Join(args, " "), timeout)
		}
		if exit, ok := err.(*exec.ExitError); ok {
			return output.String(), fmt.Errorf("Playwriter %s failed with %s: %s", strings.Join(args, " "), exit.ProcessState.String(), strings.TrimSpace(output.String()))
		}
		return output.String(), fmt.Errorf("Playwriter %s: %w", strings.Join(args, " "), err)
	}
	return output.String(), nil
}

func parsePlaywriterSessions(output string) []PlaywriterSession {
	sessions := []PlaywriterSession{}
	for _, line := range strings.Split(output, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		id := firstIntegerToken(line)
		if id == "" {
			continue
		}
		sessions = append(sessions, PlaywriterSession{
			ID:        id,
			RawLine:   line,
			Workspace: workspaceFromSessionLine(line),
		})
	}
	return sessions
}

func workspaceFromSessionLine(line string) string {
	fields := strings.Fields(line)
	for _, field := range fields {
		if strings.HasPrefix(field, "/") {
			return strings.Trim(field, " ,")
		}
	}
	return ""
}

func firstIntegerToken(value string) string {
	for _, field := range strings.FieldsFunc(value, func(r rune) bool {
		return r < '0' || r > '9'
	}) {
		if field != "" {
			return field
		}
	}
	return ""
}

func samePath(a string, b string) bool {
	aClean := filepath.Clean(strings.TrimSpace(a))
	bClean := filepath.Clean(strings.TrimSpace(b))
	return aClean == bClean
}

func RunPlaywriterFile(playwriter string, session string, script string) error {
	return RunPlaywriterFileWithTimeout(playwriter, session, script, 45000)
}

func RunPlaywriterFileWithTimeout(playwriter string, session string, script string, timeoutMS uint32) error {
	args := []string{}
	if IsBunxPath(playwriter) {
		args = append(args, "playwriter@latest")
	}
	args = append(args, "-s", session, "--timeout", strconv.FormatUint(uint64(timeoutMS), 10), "-f", script)
	cmd := exec.Command(playwriter, args...)
	if err := runCommand(cmd, "running Playwriter script"); err != nil {
		return err
	}
	return nil
}

func runCommand(cmd *exec.Cmd, context string) error {
	var output bytes.Buffer
	cmd.Stdout = io.MultiWriter(os.Stdout, &output)
	cmd.Stderr = io.MultiWriter(os.Stderr, &output)
	cmd.Stdin = os.Stdin
	if err := cmd.Run(); err != nil {
		if exit, ok := err.(*exec.ExitError); ok {
			if isClosedPlaywriterSessionOutput(output.String()) {
				return fmt.Errorf("%s failed with %s: Playwriter browser session is closed; run `playwriter session reset <session>` or reopen the browser session and retry", context, exit.ProcessState.String())
			}
			return fmt.Errorf("%s failed with %s", context, exit.ProcessState.String())
		}
		return fmt.Errorf("%s: %w", context, err)
	}
	return nil
}

func isClosedPlaywriterSessionOutput(output string) bool {
	lower := strings.ToLower(output)
	return strings.Contains(lower, "target page, context or browser has been closed") || strings.Contains(lower, "call reset to reconnect")
}
