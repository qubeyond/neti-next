package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
	"unicode/utf8"
)

const demoStreamType = "application/x-ndjson"

type demoUsage struct {
	Input  *int     `json:"prompt_tokens,omitempty"`
	Output *int     `json:"completion_tokens,omitempty"`
	Total  *int     `json:"total_tokens,omitempty"`
	Cost   *float64 `json:"cost,omitempty"`
}

type demoEvent struct {
	Type         string       `json:"type"`
	RequestID    string       `json:"request_id,omitempty"`
	ID           string       `json:"id,omitempty"`
	Parent       string       `json:"parent,omitempty"`
	Service      string       `json:"service,omitempty"`
	Operation    string       `json:"operation,omitempty"`
	At           *int64       `json:"at_ms,omitempty"`
	Duration     *int         `json:"duration_ms,omitempty"`
	Upstream     *int         `json:"upstream_ms,omitempty"`
	Status       int          `json:"http_status,omitempty"`
	Count        *int         `json:"count,omitempty"`
	CacheHit     *bool        `json:"cache_hit,omitempty"`
	Outcome      string       `json:"outcome,omitempty"`
	Decision     string       `json:"decision,omitempty"`
	Recovery     string       `json:"recovery,omitempty"`
	Model        string       `json:"model,omitempty"`
	Domains      []string     `json:"domains,omitempty"`
	ActiveSkills []string     `json:"active_skills,omitempty"`
	Provider     string       `json:"provider,omitempty"`
	Usage        *demoUsage   `json:"usage,omitempty"`
	Answer       *agentAnswer `json:"answer,omitempty"`
	Error        string       `json:"detail,omitempty"`
}

type demoAgent interface {
	trace(context.Context, agentRequest, func(demoEvent) error) error
}

func (s *server) demoOwner(request *http.Request) (identity, error) {
	who, err := s.account(request)
	if err != nil {
		return who, err
	}
	if id := os.Getenv("BACKEND_DEMO_ACCOUNT_ID"); id == "" || who.owner != id {
		return who, problem(403, "Режим демонстрации недоступен для этого аккаунта.")
	}
	return who, nil
}

func (s *server) demoAccess(w http.ResponseWriter, r *http.Request) error {
	if _, err := s.demoOwner(r); err != nil {
		return err
	}
	writeJSON(w, 200, map[string]bool{"enabled": true})
	return nil
}

func (s *server) demoRun(w http.ResponseWriter, r *http.Request) error {
	who, err := s.demoOwner(r)
	if err != nil {
		return err
	}
	runner, ok := s.agent.(demoAgent)
	if !ok {
		return problem(503, "Трассировка агента недоступна.")
	}

	input, err := readDemoInput(w, r)
	if err != nil {
		return err
	}

	job := &runJob{who: who}
	id, _ := r.Context().Value(requestIDKey{}).(string)
	if err := s.turns.admit(job, id); err != nil {
		return err
	}
	defer s.turns.release(job)
	ctx, cancel := context.WithCancel(job.ctx)
	defer cancel()
	stop := context.AfterFunc(r.Context(), cancel)
	defer stop()

	w.Header().Set("Content-Type", demoStreamType)
	w.Header().Set("X-Accel-Buffering", "no")
	controller := http.NewResponseController(w)
	writer := json.NewEncoder(w)
	start := time.Now()
	emit := func(event demoEvent) error {
		ms := time.Since(start).Milliseconds()
		event.At, event.RequestID = &ms, id
		if err := writer.Encode(event); err != nil {
			return err
		}
		return controller.Flush()
	}

	if err := emit(demoEvent{Type: "span_start", ID: "agent", Service: "backend", Operation: "agent_request"}); err != nil {
		return nil
	}
	var final demoEvent
	err = runner.trace(ctx, input, func(event demoEvent) error {
		if event.Type == "answer" || event.Type == "error" {
			final = event
			return nil
		}
		return emit(event)
	})
	if ctx.Err() != nil && r.Context().Err() != nil {
		return nil
	}
	duration := int(time.Since(start).Milliseconds())
	outcome := "ok"
	if err != nil {
		failure := safeFailure(err, id)
		final = demoEvent{Type: "error", Status: failure.code, Error: failure.text}
	}
	if final.Type == "error" {
		outcome = "error"
	} else if final.Type == "" {
		outcome = "error"
		final = demoEvent{Type: "error", Status: 502, Error: "Поток завершился без ответа."}
	}
	if err := emit(demoEvent{Type: "span_end", ID: "agent", Duration: &duration, Outcome: outcome, Status: final.Status}); err == nil {
		_ = emit(final)
	}
	return nil
}

type demoInput struct {
	Question string     `json:"question"`
	Quote    string     `json:"quoted_text"`
	History  []demoTurn `json:"history"`
}

type demoTurn struct {
	Role string `json:"role"`
	Text string `json:"text"`
}

func readDemoInput(w http.ResponseWriter, r *http.Request) (agentRequest, error) {
	var input demoInput
	var file attachment
	var data []byte
	var err error
	if strings.HasPrefix(r.Header.Get("Content-Type"), "multipart/form-data") {
		file, data, err = readDemoMultipart(w, r, &input)
	} else {
		err = readJSON(w, r, &input)
	}
	if err != nil {
		return agentRequest{}, err
	}
	input.Question = strings.TrimSpace(input.Question)
	if (input.Question == "" && len(data) == 0) ||
		utf8.RuneCountInString(input.Question) > 2000 || utf8.RuneCountInString(input.Quote) > 1200 ||
		len(input.History) > 6 {
		return agentRequest{}, problem(422, "Проверьте вопрос, цитату и историю демо.")
	}
	current := agentRequest{Message: message{Text: input.Question, Quote: input.Quote}}
	for _, turn := range input.History {
		if turn.Role != "user" && turn.Role != "assistant" || utf8.RuneCountInString(turn.Text) > 800 {
			return agentRequest{}, problem(422, "Некорректная реплика истории демо.")
		}
		current.History = append(current.History, message{Role: turn.Role, Text: turn.Text, State: "complete"})
	}
	if len(data) != 0 {
		current.Message.Attachments = []attachment{file}
		current.ReadAttachment = func(id string) ([]byte, error) {
			if id != file.ID {
				return nil, problem(404, "Вложение не найдено.")
			}
			return data, nil
		}
	}
	return current, nil
}

func readDemoMultipart(w http.ResponseWriter, r *http.Request, input *demoInput) (attachment, []byte, error) {
	r.Body = http.MaxBytesReader(w, r.Body, maxFileBytes+(64<<10))
	reader, err := r.MultipartReader()
	if err != nil {
		return attachment{}, nil, problem(422, "Проверьте вложение демо.")
	}
	meta, err := reader.NextPart()
	if err != nil || meta.FormName() != "input" || meta.FileName() != "" {
		return attachment{}, nil, problem(422, "Ожидаются параметры демо в поле input.")
	}
	decoder := json.NewDecoder(io.LimitReader(meta, 64<<10))
	decoder.DisallowUnknownFields()
	if decoder.Decode(input) != nil || decoder.Decode(new(any)) != io.EOF {
		return attachment{}, nil, problem(422, "Некорректные параметры демо.")
	}
	part, err := reader.NextPart()
	if err != nil {
		return attachment{}, nil, problem(422, "Ожидается файл в поле file.")
	}
	file, data, err := readAttachmentPart(part)
	if err != nil {
		return attachment{}, nil, err
	}
	if _, err := reader.NextPart(); !errors.Is(err, io.EOF) {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			return attachment{}, nil, err
		}
		return attachment{}, nil, problem(422, "Можно передать одно вложение.")
	}
	if !supportedMedia(file.MIME) {
		return attachment{}, nil, problem(415, "Формат вложения не поддерживается.")
	}
	return file, data, nil
}

type demoMediaKey struct{}

func (agent legacyAgent) trace(ctx context.Context, input agentRequest, emit func(demoEvent) error) error {
	if agent.token == "" {
		return problem(503, "Задайте токен подключения к агенту для демо.")
	}
	start := time.Now()
	hasMedia := len(input.Message.Attachments) != 0
	if hasMedia {
		if err := emit(demoEvent{Type: "span_start", ID: "media", Parent: "agent", Service: "backend", Operation: "prepare_media"}); err != nil {
			return err
		}
		ctx = context.WithValue(ctx, demoMediaKey{}, emit)
	}
	prepared, err := agent.prepare(ctx, input, func(activity) {})
	if hasMedia {
		duration := int(time.Since(start).Milliseconds())
		outcome := "ok"
		if err != nil {
			outcome = "error"
		}
		if flushErr := emit(demoEvent{Type: "span_end", ID: "media", Duration: &duration, Outcome: outcome}); flushErr != nil {
			return flushErr
		}
	}
	if err != nil {
		return err
	}
	if len(prepared.payload) == 0 {
		return emit(demoEvent{Type: "answer", Answer: &prepared.local})
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, agent.endpoint+"/trace", bytes.NewReader(prepared.payload))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer "+agent.token)
	propagate(ctx, request)
	response, err := agent.client.Do(request)
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return problem(502, "Не удалось подключиться к агенту.")
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return upstreamFailure(response.StatusCode, "Агент")
	}
	reader := bufio.NewScanner(io.LimitReader(response.Body, 2<<20))
	reader.Buffer(make([]byte, 4096), 1<<20)
	for reader.Scan() {
		var event demoEvent
		if err := json.Unmarshal(reader.Bytes(), &event); err != nil {
			return problem(502, "Агент вернул некорректное событие.")
		}
		switch event.Type {
		case "span_start", "span_end", "heartbeat":
			if err := emit(event); err != nil {
				return err
			}
		case "answer":
			if event.Answer == nil {
				return problem(502, "В трассировке отсутствует итоговый ответ.")
			}
			answer, err := prepared.finalize(ctx, *event.Answer)
			if err != nil {
				return err
			}
			event.Answer = &answer
			return emit(event)
		case "error":
			return upstreamFailure(event.Status, "Агент")
		}
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	return problem(502, "Поток событий завершился без ответа.")
}
