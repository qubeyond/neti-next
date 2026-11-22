package agentclient

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"time"

	"github.com/qubeyond/neti-next/backend/internal/apperr"
	"github.com/qubeyond/neti-next/backend/internal/chat"
	"github.com/qubeyond/neti-next/backend/internal/httpapi"
)

type TraceUsage struct {
	Input  *int     `json:"prompt_tokens,omitempty"`
	Output *int     `json:"completion_tokens,omitempty"`
	Total  *int     `json:"total_tokens,omitempty"`
	Cost   *float64 `json:"cost,omitempty"`
}

type TraceEvent struct {
	Type         string                `json:"type"`
	RequestID    string                `json:"request_id,omitempty"`
	ID           string                `json:"id,omitempty"`
	Parent       string                `json:"parent,omitempty"`
	Service      string                `json:"service,omitempty"`
	Operation    string                `json:"operation,omitempty"`
	At           *int64                `json:"at_ms,omitempty"`
	Duration     *int                  `json:"duration_ms,omitempty"`
	Upstream     *int                  `json:"upstream_ms,omitempty"`
	Status       int                   `json:"http_status,omitempty"`
	Count        *int                  `json:"count,omitempty"`
	CacheHit     *bool                 `json:"cache_hit,omitempty"`
	Outcome      string                `json:"outcome,omitempty"`
	Decision     string                `json:"decision,omitempty"`
	Recovery     string                `json:"recovery,omitempty"`
	Model        string                `json:"model,omitempty"`
	Domains      []string              `json:"domains,omitempty"`
	ActiveSkills []string              `json:"active_skills,omitempty"`
	Sources      []chat.ActivitySource `json:"sources,omitempty"`
	Provider     string                `json:"provider,omitempty"`
	Usage        *TraceUsage           `json:"usage,omitempty"`
	Answer       *chat.AgentAnswer     `json:"answer,omitempty"`
	Error        string                `json:"detail,omitempty"`
}

type demoMediaKey struct{}

func (agent Client) Trace(ctx context.Context, input chat.AgentRequest, emit func(TraceEvent) error) error {
	if agent.token == "" {
		return apperr.HTTP(503, "Задайте токен подключения к агенту для демо.")
	}
	start := time.Now()
	hasMedia := len(input.Message.Attachments) != 0
	if hasMedia {
		if err := emit(TraceEvent{Type: "span_start", ID: "media", Parent: "agent", Service: "backend", Operation: "prepare_media"}); err != nil {
			return err
		}
		ctx = context.WithValue(ctx, demoMediaKey{}, emit)
	}
	prepared, err := agent.prepare(ctx, input, func(chat.Activity) {})
	if hasMedia {
		duration := int(time.Since(start).Milliseconds())
		outcome := "ok"
		if err != nil {
			outcome = "error"
		}
		if flushErr := emit(TraceEvent{Type: "span_end", ID: "media", Duration: &duration, Outcome: outcome}); flushErr != nil {
			return flushErr
		}
	}
	if err != nil {
		return err
	}
	if len(prepared.payload) == 0 {
		return emit(TraceEvent{Type: "answer", Answer: &prepared.local})
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, agent.endpoint+"/trace", bytes.NewReader(prepared.payload))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer "+agent.token)
	httpapi.Propagate(ctx, request)
	response, err := agent.client.Do(request)
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return apperr.HTTP(502, "Не удалось подключиться к агенту.")
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return httpapi.UpstreamFailure(response.StatusCode, "Агент")
	}
	return readTrace(ctx, response.Body, prepared, emit)
}

func readTrace(ctx context.Context, body io.Reader, prepared preparedTurn, emit func(TraceEvent) error) error {
	reader := bufio.NewScanner(io.LimitReader(body, 2<<20))
	reader.Buffer(make([]byte, 4096), 1<<20)
	for reader.Scan() {
		var event TraceEvent
		if err := json.Unmarshal(reader.Bytes(), &event); err != nil {
			return apperr.HTTP(502, "Агент вернул некорректное событие.")
		}
		switch event.Type {
		case "span_start", "span_end", "heartbeat":
			if err := emit(event); err != nil {
				return err
			}
		case "answer":
			if event.Answer == nil {
				return apperr.HTTP(502, "В трассировке отсутствует итоговый ответ.")
			}
			answer, err := prepared.finalize(ctx, *event.Answer)
			if err != nil {
				return err
			}
			event.Answer = &answer
			return emit(event)
		case "error":
			return httpapi.UpstreamFailure(event.Status, "Агент")
		}
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	return apperr.HTTP(502, "Поток событий завершился без ответа.")
}

var activityLabels = map[string]string{
	"route_turn":      "Определяю, нужны ли источники",
	"resolve_context": "Уточняю контекст вопроса",
	"search":          "Ищу материалы по вопросу",
	"backup_search":   "Ищу дополнительные источники",
	"read":            "Читаю найденный документ",
	"select_skills":   "Выбираю навыки для ответа",
	"generation":      "Сопоставляю факты и источники",
	"regeneration":    "Дополняю ответ новыми источниками",
	"verify":          "Сверяю утверждения с цитатами",
}

func chatTrace(ctx context.Context, body io.Reader, prepared preparedTurn, report func(chat.Activity)) (chat.AgentAnswer, error) {
	answer := prepared.local
	active := make(map[string]chat.Activity)
	err := readTrace(ctx, body, prepared, func(event TraceEvent) error {
		switch event.Type {
		case "span_start":
			label, visible := activityLabels[event.Operation]
			if !visible {
				return nil
			}
			step := chat.Activity{ID: event.ID, Kind: event.Operation, Label: label, State: "running"}
			active[event.ID] = step
			if event.Operation != "select_skills" && event.Operation != "resolve_context" {
				report(step)
			}
		case "span_end":
			step, visible := active[event.ID]
			if !visible {
				return nil
			}
			delete(active, event.ID)
			step.State, step.Duration = event.Outcome, event.Duration
			step.Provider, step.Domains, step.Count = event.Provider, event.Domains, event.Count
			step.Skills, step.Sources = event.ActiveSkills, event.Sources
			if (step.Kind != "select_skills" || len(step.Skills) > 0) &&
				(step.Kind != "resolve_context" || (step.Duration != nil && *step.Duration >= 50)) {
				report(step)
			}
		case "answer":
			answer = *event.Answer
		}
		return nil
	})
	return answer, err
}
