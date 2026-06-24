package main

import (
	"context"
	"fmt"
	"os"

	"github.com/hanifcarroll/linkedin-network-run/internal/outreach"
)

func main() {
	if err := outreach.Execute(context.Background(), os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
