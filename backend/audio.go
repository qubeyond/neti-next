package main

import (
	"bufio"
	"bytes"
	"context"
	"io"
	"math"
	"net/http"
	"os/exec"
	"strconv"
	"strings"
	"time"
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
		return problem(502, "Не удалось проверить аудио.")
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
			return problem(413, "Аудио превышает допустимую длительность.")
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
		return problem(422, "Не удалось определить длительность аудио.")
	}
	return nil
}

func (s *server) transcribeDraft(w http.ResponseWriter, r *http.Request) error {
	who, err := s.actor(r)
	if err != nil {
		return err
	}
	provider, ok := s.agent.(legacyAgent)
	if !ok || provider.inference == nil {
		return problem(503, "Распознавание речи недоступно.")
	}
	mime, _, _ := strings.Cut(r.Header.Get("Content-Type"), ";")
	if mime == "audio/wave" || mime == "audio/x-wav" {
		mime = "audio/wav"
	}
	if !strings.HasPrefix(mime, "audio/") || !supportedMedia(mime) {
		return problem(415, "Нужна поддерживаемая аудиозапись.")
	}
	data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxFileBytes))
	if err != nil {
		return err
	}
	if len(data) == 0 {
		return problem(422, "Аудиозапись пуста.")
	}

	id, _ := r.Context().Value(requestIDKey{}).(string)
	job := &runJob{who: who}
	if err := s.turns.admit(job, id); err != nil {
		return err
	}
	defer s.turns.release(job)
	ctx, cancel := context.WithCancel(job.ctx)
	defer cancel()
	stop := context.AfterFunc(r.Context(), cancel)
	defer stop()

	if err := probeAudio(ctx, data, provider.inference.audioLimit); err != nil {
		return err
	}
	result, err := provider.inference.recognize(ctx, attachment{MIME: mime}, data)
	if err != nil {
		return err
	}
	writeJSON(w, 200, map[string]string{"text": result.Text})
	return nil
}
