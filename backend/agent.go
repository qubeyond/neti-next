package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
)

// Объявляет данные, доступные следующему адаптеру. Текущий HTTP-метод поддерживает только вопрос.
type agentRequest struct {
	ChatID         string
	History        []message
	Message        message
	DisplayName    string
	Instructions   string
	ReadAttachment func(string) ([]byte, error)
}

type agentAnswer struct {
	Status    string     `json:"status"`
	Answer    string     `json:"answer"`
	Citations []citation `json:"citations"`
	Artifacts []artifact `json:"artifacts"`
}

type agent interface {
	answer(context.Context, agentRequest, func(activity)) (agentAnswer, error)
}

type legacyAgent struct {
	client   *http.Client
	endpoint string
}

func newLegacyAgent(baseURL string) legacyAgent {
	return legacyAgent{
		client:   &http.Client{CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }},
		endpoint: strings.TrimRight(baseURL, "/") + "/agent/v1/answers",
	}
}

func (agent legacyAgent) answer(ctx context.Context, input agentRequest, _ func(activity)) (agentAnswer, error) {
	var result agentAnswer
	if input.Message.Text == "" {
		return result, errors.New("Агент пока не читает вложения. Отправьте текстовый вопрос.")
	}
	question := input.Message.Text
	if input.Message.Quote != "" {
		label := "\n\nЦитата из предыдущего ответа:\n"
		free := 2000 - len([]rune(question)) - len([]rune(label))
		if free > 0 {
			quote := []rune(input.Message.Quote)
			question += label + string(quote[:min(free, len(quote))])
		}
	}
	data, err := json.Marshal(map[string]string{"question": question})
	if err != nil {
		return result, errors.New("Не удалось подготовить запрос.")
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, agent.endpoint, bytes.NewReader(data))
	if err != nil {
		return result, errors.New("Адрес агента настроен неверно.")
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := agent.client.Do(request)
	if err != nil {
		if ctx.Err() != nil {
			return result, errors.New("Агент не успел ответить. Попробуйте ещё раз.")
		}
		return result, errors.New("Не удалось подключиться к агенту. Попробуйте ещё раз.")
	}
	defer response.Body.Close()
	switch response.StatusCode {
	case 200:
	case 503:
		return result, errors.New("Ответы агента отключены в этом окружении.")
	case 429, 502, 504:
		return result, errors.New("Сервис агента временно недоступен. Попробуйте ещё раз.")
	default:
		return result, fmt.Errorf("Сервис агента отклонил запрос (HTTP %d).", response.StatusCode)
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&result); err != nil {
		return result, errors.New("Агент вернул ответ в неизвестном формате.")
	}
	switch result.Status {
	case "answered", "partial", "unverified":
		return result, nil
	default:
		return agentAnswer{}, errors.New("Агент вернул неизвестный статус ответа.")
	}
}
