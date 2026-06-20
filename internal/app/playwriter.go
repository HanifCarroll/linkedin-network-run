package app

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
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
