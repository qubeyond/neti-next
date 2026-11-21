package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"regexp"
	"strings"
)

// Данные текущего хода передаются из рабочего пространства адаптеру агента.
type agentRequest struct {
	ChatID         string
	History        []message
	Message        message
	DisplayName    string
	Instructions   string
	ReadAttachment func(string) ([]byte, error)
}

type agentAnswer struct {
	Status      string          `json:"status"`
	Answer      string          `json:"answer"`
	Citations   []citation      `json:"citations"`
	Blocks      []chatBlock     `json:"blocks,omitempty"`
	Suggestions []string        `json:"suggestions,omitempty"`
	Events      []calendarEvent `json:"events"`
	Artifacts   []artifact      `json:"artifacts"`
}

type agent interface {
	answer(context.Context, agentRequest, func(activity)) (agentAnswer, error)
}

type legacyAgent struct {
	client    *http.Client
	endpoint  string
	inference *inferenceClient
	token     string
}

func newLegacyAgent(baseURL string) legacyAgent {
	return legacyAgent{
		client:   &http.Client{CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }},
		endpoint: strings.TrimRight(baseURL, "/") + "/agent/v1/answers",
	}
}

var imageQuestion = regexp.MustCompile(`(?i)фото|снимк|скрин|изображ|картинк|что (?:здесь|тут|написано|видно)|здесь (?:написано|указано|видно)`)

type preparedTurn struct {
	payload []byte
	local   agentAnswer
	image   *recognizedImage
}

func (agent legacyAgent) prepare(ctx context.Context, input agentRequest, report func(activity)) (preparedTurn, error) {
	var result agentAnswer
	question := input.Message.Text
	contextText := ""
	var image *recognizedImage
	if imageQuestion.MatchString(question) {
		photos := 0
		for _, item := range input.Message.Attachments {
			if strings.HasPrefix(item.MIME, "image/") {
				photos++
			}
		}
		if photos > 1 {
			return preparedTurn{local: agentAnswer{Status: "clarification", Answer: "Пришлите одну фотографию, к которой относится вопрос."}}, nil
		}
	}
	if len(input.Message.Attachments) > 0 && agent.inference != nil && input.ReadAttachment != nil {
		recognized, files, mediaImage, err := agent.inference.prepare(ctx, input, report)
		if err != nil {
			return preparedTurn{}, err
		}
		result.Artifacts = files
		if question == "" {
			hasVoice := false
			for _, item := range files {
				hasVoice = hasVoice || strings.HasPrefix(item.Title, "Расшифровка · ")
			}
			if !hasVoice && recognized != "" {
				result.Status, result.Answer = "answered", "Распознанный текст доступен во вложениях ответа."
				return preparedTurn{local: result}, nil
			}
			question = recognized
		} else {
			contextText = recognized
			image = mediaImage
		}
	}
	if question == "" {
		return preparedTurn{}, problem(422, "Добавьте текст, аудиозапись, PDF или изображение для распознавания.")
	}
	runes := []rune(question)
	question = string(runes[:min(len(runes), 2000)])
	type historyTurn struct {
		Role string `json:"role"`
		Text string `json:"text"`
	}
	history := make([]historyTurn, 0, 6)
	for _, message := range input.History {
		if message.Text == "" || message.Role == "assistant" && message.State != "complete" {
			continue
		}
		runes := []rune(message.Text)
		history = append(history, historyTurn{message.Role, string(runes[:min(800, len(runes))])})
	}
	payload := map[string]any{"question": question}
	if len(history) > 0 {
		payload["history"] = history[max(0, len(history)-6):]
	}
	if input.Message.Quote != "" {
		payload["quoted_text"] = input.Message.Quote
	}
	if image != nil {
		payload["image_source"] = image
	} else if contextText != "" {
		contextRunes := []rune(contextText)
		payload["attachment_text"] = string(contextRunes[:min(len(contextRunes), 6000)])
	}
	data, err := json.Marshal(payload)
	if err != nil {
		return preparedTurn{}, problem(500, "Не удалось подготовить запрос.")
	}
	return preparedTurn{payload: data, local: result, image: image}, nil
}

func (agent legacyAgent) answer(ctx context.Context, input agentRequest, report func(activity)) (agentAnswer, error) {
	prepared, err := agent.prepare(ctx, input, report)
	if err != nil || len(prepared.payload) == 0 {
		return prepared.local, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, agent.endpoint, bytes.NewReader(prepared.payload))
	if err != nil {
		return prepared.local, problem(503, "Адрес агента настроен неверно.")
	}
	request.Header.Set("Content-Type", "application/json")
	propagate(ctx, request)
	if agent.token != "" {
		request.Header.Set("Authorization", "Bearer "+agent.token)
	}
	response, err := agent.client.Do(request)
	if err != nil {
		if ctx.Err() != nil {
			return prepared.local, ctx.Err()
		}
		return prepared.local, problem(502, "Не удалось подключиться к агенту. Попробуйте ещё раз.")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return prepared.local, upstreamFailure(response.StatusCode, "Агент")
	}
	var answer agentAnswer
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&answer); err != nil {
		return prepared.local, problem(502, "Агент вернул ответ в неизвестном формате.")
	}
	return prepared.finalize(ctx, answer)
}

func (prepared preparedTurn) finalize(ctx context.Context, answer agentAnswer) (agentAnswer, error) {
	answer.Artifacts = append(prepared.local.Artifacts, answer.Artifacts...)
	addImageRegions(ctx, prepared.image, answer.Citations)
	switch answer.Status {
	case "answered", "partial", "unverified", "clarification":
		return answer, nil
	default:
		return agentAnswer{}, problem(502, "Агент вернул неизвестный статус ответа.")
	}
}
