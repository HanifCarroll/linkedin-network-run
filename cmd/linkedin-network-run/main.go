package main

import (
	"context"
	"fmt"
	"os"

	"github.com/hanifcarroll/linkedin-network-run/internal/app"
)

func main() {
	if err := app.Execute(context.Background(), os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
