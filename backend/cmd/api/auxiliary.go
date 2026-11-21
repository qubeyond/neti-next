package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/qubeyond/neti-next/backend/internal/account"
	"github.com/qubeyond/neti-next/backend/internal/agentclient"
	"github.com/qubeyond/neti-next/backend/internal/apperr"
	"github.com/qubeyond/neti-next/backend/internal/chat"
	"github.com/qubeyond/neti-next/backend/internal/httpapi"
	"github.com/qubeyond/neti-next/backend/internal/identity"
	"github.com/qubeyond/neti-next/backend/internal/limits"
)

const demoStreamType = "application/x-ndjson"

type demoAgent interface {
	Trace(context.Context, chat.AgentRequest, func(agentclient.TraceEvent) error) error
}

func (s *auxiliary) demoOwner(request *http.Request) (identity.Actor, error) {
	who, err := s.accounts.Account(request)
	if err != nil {
		return who, err
	}
	if id := os.Getenv("BACKEND_DEMO_ACCOUNT_ID"); id == "" || who.Owner != id {
		return who, apperr.HTTP(403, "Режим демонстрации недоступен для этого аккаунта.")
	}
	return who, nil
}

func (s *auxiliary) demoAccess(w http.ResponseWriter, r *http.Request) error {
	if _, err := s.demoOwner(r); err != nil {
		return err
	}
	httpapi.WriteJSON(w, 200, map[string]bool{"enabled": true})
	return nil
}

func (s *auxiliary) demoRun(w http.ResponseWriter, r *http.Request) error {
	who, err := s.demoOwner(r)
	if err != nil {
		return err
	}
	runner, ok := s.agent.(demoAgent)
	if !ok {
		return apperr.HTTP(503, "Трассировка агента недоступна.")
	}

	input, err := readDemoInput(w, r)
	if err != nil {
		return err
	}

	id := httpapi.RequestID(r.Context())
	permit, err := s.turns.Admit(who.Owner, "", id)
	if err != nil {
		return err
	}
	defer s.turns.Release(permit)
	ctx, cancel := context.WithCancel(permit.Context)
	defer cancel()
	stop := context.AfterFunc(r.Context(), cancel)
	defer stop()

	w.Header().Set("Content-Type", demoStreamType)
	w.Header().Set("X-Accel-Buffering", "no")
	controller := http.NewResponseController(w)
	writer := json.NewEncoder(w)
	start := time.Now()
	emit := func(event agentclient.TraceEvent) error {
		ms := time.Since(start).Milliseconds()
		event.At, event.RequestID = &ms, id
		if err := writer.Encode(event); err != nil {
			return err
		}
		return controller.Flush()
	}

	if err := emit(agentclient.TraceEvent{Type: "span_start", ID: "agent", Service: "backend", Operation: "agent_request"}); err != nil {
		return nil
	}
	var final agentclient.TraceEvent
	err = runner.Trace(ctx, input, func(event agentclient.TraceEvent) error {
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
		failure := httpapi.SafeFailure(err, id)
		final = agentclient.TraceEvent{Type: "error", Status: failure.Code, Error: failure.Detail}
	}
	if final.Type == "error" {
		outcome = "error"
	} else if final.Type == "" {
		outcome = "error"
		final = agentclient.TraceEvent{Type: "error", Status: 502, Error: "Поток завершился без ответа."}
	}
	if err := emit(agentclient.TraceEvent{Type: "span_end", ID: "agent", Duration: &duration, Outcome: outcome, Status: final.Status}); err == nil {
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

func readDemoInput(w http.ResponseWriter, r *http.Request) (chat.AgentRequest, error) {
	var input demoInput
	var file chat.Attachment
	var data []byte
	var err error
	if strings.HasPrefix(r.Header.Get("Content-Type"), "multipart/form-data") {
		file, data, err = readDemoMultipart(w, r, &input)
	} else {
		err = httpapi.ReadJSON(w, r, &input)
	}
	if err != nil {
		return chat.AgentRequest{}, err
	}
	input.Question = strings.TrimSpace(input.Question)
	if (input.Question == "" && len(data) == 0) ||
		utf8.RuneCountInString(input.Question) > 2000 || utf8.RuneCountInString(input.Quote) > 1200 ||
		len(input.History) > 6 {
		return chat.AgentRequest{}, apperr.HTTP(422, "Проверьте вопрос, цитату и историю демо.")
	}
	current := chat.AgentRequest{Message: chat.Message{Text: input.Question, Quote: input.Quote}}
	for _, turn := range input.History {
		if turn.Role != "user" && turn.Role != "assistant" || utf8.RuneCountInString(turn.Text) > 800 {
			return chat.AgentRequest{}, apperr.HTTP(422, "Некорректная реплика истории демо.")
		}
		current.History = append(current.History, chat.Message{Role: turn.Role, Text: turn.Text, State: "complete"})
	}
	if len(data) != 0 {
		current.Message.Attachments = []chat.Attachment{file}
		current.ReadAttachment = func(id string) ([]byte, error) {
			if id != file.ID {
				return nil, apperr.HTTP(404, "Вложение не найдено.")
			}
			return data, nil
		}
	}
	return current, nil
}

func readDemoMultipart(w http.ResponseWriter, r *http.Request, input *demoInput) (chat.Attachment, []byte, error) {
	r.Body = http.MaxBytesReader(w, r.Body, chat.MaxFileBytes+(64<<10))
	reader, err := r.MultipartReader()
	if err != nil {
		return chat.Attachment{}, nil, apperr.HTTP(422, "Проверьте вложение демо.")
	}
	meta, err := reader.NextPart()
	if err != nil || meta.FormName() != "input" || meta.FileName() != "" {
		return chat.Attachment{}, nil, apperr.HTTP(422, "Ожидаются параметры демо в поле input.")
	}
	decoder := json.NewDecoder(io.LimitReader(meta, 64<<10))
	decoder.DisallowUnknownFields()
	if decoder.Decode(input) != nil || decoder.Decode(new(any)) != io.EOF {
		return chat.Attachment{}, nil, apperr.HTTP(422, "Некорректные параметры демо.")
	}
	part, err := reader.NextPart()
	if err != nil {
		return chat.Attachment{}, nil, apperr.HTTP(422, "Ожидается файл в поле file.")
	}
	file, data, err := chat.ReadAttachmentPart(part)
	if err != nil {
		return chat.Attachment{}, nil, err
	}
	if _, err := reader.NextPart(); !errors.Is(err, io.EOF) {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			return chat.Attachment{}, nil, err
		}
		return chat.Attachment{}, nil, apperr.HTTP(422, "Можно передать одно вложение.")
	}
	if !agentclient.SupportedMedia(file.MIME) {
		return chat.Attachment{}, nil, apperr.HTTP(415, "Формат вложения не поддерживается.")
	}
	return file, data, nil
}

type auxiliary struct {
	accounts *account.Handler
	agent    chat.Agent
	turns    *limits.Turns
}

func (s *auxiliary) transcribeDraft(w http.ResponseWriter, r *http.Request) error {
	who, err := s.accounts.Actor(r)
	if err != nil {
		return err
	}
	provider, ok := s.agent.(agentclient.Client)
	if !ok || !provider.TranscriptionAvailable() {
		return apperr.HTTP(503, "Распознавание речи недоступно.")
	}
	mime, _, _ := strings.Cut(r.Header.Get("Content-Type"), ";")
	if mime == "audio/wave" || mime == "audio/x-wav" {
		mime = "audio/wav"
	}
	if !strings.HasPrefix(mime, "audio/") || !agentclient.SupportedMedia(mime) {
		return apperr.HTTP(415, "Нужна поддерживаемая аудиозапись.")
	}
	data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, chat.MaxFileBytes))
	if err != nil {
		return err
	}
	if len(data) == 0 {
		return apperr.HTTP(422, "Аудиозапись пуста.")
	}

	permit, err := s.turns.Admit(who.Owner, "", httpapi.RequestID(r.Context()))
	if err != nil {
		return err
	}
	defer s.turns.Release(permit)
	ctx, cancel := context.WithCancel(permit.Context)
	defer cancel()
	stop := context.AfterFunc(r.Context(), cancel)
	defer stop()
	result, err := provider.Transcribe(ctx, mime, data)

	if err != nil {
		return err
	}
	httpapi.WriteJSON(w, 200, map[string]string{"text": result})
	return nil
}
