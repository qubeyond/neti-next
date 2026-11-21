package agentclient

import (
	"bufio"
	"bytes"
	"context"
	"io"
	"math"
	"os/exec"
	"strconv"
	"strings"
	"time"

	"github.com/qubeyond/neti-next/backend/internal/apperr"
)

func probeAudio(ctx context.Context, data []byte, maximum time.Duration) error {
	probe, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	command := exec.CommandContext(probe, "ffprobe", "-v", "error", "-protocol_whitelist", "pipe",
		"-max_alloc", "33554432", "-select_streams", "a:0", "-show_entries",
		"packet=pts_time,duration_time:format=duration", "-of", "compact=p=0:nk=0", "pipe:0")
	command.Stdin = bytes.NewReader(data)
	stdout, err := command.StdoutPipe()
	if err != nil || command.Start() != nil {
		return apperr.HTTP(502, "Не удалось проверить аудио.")
	}

	output := &io.LimitedReader{R: stdout, N: 2 << 20}
	scan := bufio.NewScanner(output)
	longest, audio := 0.0, false
	for scan.Scan() {
		var timestamp, duration float64
		for _, field := range strings.Split(scan.Text(), "|") {
			name, raw, _ := strings.Cut(field, "=")
			value, err := strconv.ParseFloat(raw, 64)
			if err != nil || math.IsNaN(value) || math.IsInf(value, 0) {
				continue
			}
			switch name {
			case "pts_time":
				timestamp, audio = value, true
			case "duration_time":
				duration, audio = value, true
			case "duration":
				longest = max(longest, value)
			}
		}
		longest = max(longest, timestamp+duration)
		if longest > maximum.Seconds() {
			cancel()
			command.Wait()
			return apperr.HTTP(413, "Аудио превышает допустимую длительность.")
		}
	}
	if scan.Err() != nil || output.N == 0 {
		cancel()
	}
	err = command.Wait()
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if scan.Err() != nil || output.N == 0 || err != nil || !audio || longest <= 0 {
		return apperr.HTTP(422, "Не удалось определить длительность аудио.")
	}
	return nil
}
