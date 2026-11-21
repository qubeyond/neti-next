package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"
)

type inferenceClient struct {
	base       string
	token      string
	client     *http.Client
	pdf        pdfLimits
	audioLimit time.Duration
}

type recognition struct {
	Text      string     `json:"text"`
	Truncated bool       `json:"truncated"`
	Model     string     `json:"model"`
	Latency   *int       `json:"latency_ms"`
	Usage     *demoUsage `json:"usage"`
}

func (service inferenceClient) recognize(ctx context.Context, media attachment, data []byte) (output recognition, failure error) {
	action := "ocr"
	if strings.HasPrefix(media.MIME, "audio/") {
		action = "transcribe"
	}
	if emit, ok := ctx.Value(demoMediaKey{}).(func(demoEvent) error); ok {
		id, start := randomID(), time.Now()
		_ = emit(demoEvent{Type: "span_start", ID: id, Parent: "media", Service: "inference", Operation: action})
		defer func() {
			duration := int(time.Since(start).Milliseconds())
			outcome, status := "ok", 0
			if failure != nil {
				outcome = "error"
				status = safeFailure(failure, "").code
			}
			_ = emit(demoEvent{Type: "span_end", ID: id, Duration: &duration, Outcome: outcome, Status: status,
				Model: output.Model, Upstream: output.Latency, Usage: output.Usage})
		}()
	}
	body, err := json.Marshal(struct {
		MIME string `json:"mime"`
		Data []byte `json:"data"`
	}{MIME: media.MIME, Data: data})
	if err != nil {
		return output, err
	}
	address := strings.TrimRight(service.base, "/") + "/inference/v1/" + action
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, address, bytes.NewReader(body))
	if err != nil {
		return output, problem(503, "Адрес инференса настроен неверно.")
	}
	request.Header.Set("Content-Type", "application/json")
	propagate(ctx, request)
	if service.token != "" {
		request.Header.Set("Authorization", "Bearer "+service.token)
	}
	response, err := service.client.Do(request)
	if err != nil {
		if ctx.Err() != nil {
			return output, ctx.Err()
		}
		return output, problem(502, "Не удалось подключиться к сервису распознавания.")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return output, upstreamFailure(response.StatusCode, "Сервис распознавания")
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&output); err != nil {
		return recognition{}, problem(502, "Сервис распознавания вернул неверный результат.")
	}
	output.Text = strings.TrimSpace(output.Text)
	if output.Text == "" {
		return output, problem(422, "Во вложении не удалось разобрать речь или текст.")
	}
	return output, nil
}

func supportedMedia(mime string) bool {
	switch mime {
	case "audio/webm", "audio/ogg", "audio/wav", "audio/wave", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/flac",
		"image/jpeg", "image/png", "image/webp", "application/pdf":
		return true
	}
	return false
}

type recognizedImage struct {
	ID   string `json:"id"`
	Text string `json:"text"`
	data []byte
}

func (service inferenceClient) prepare(ctx context.Context, input agentRequest, report func(activity)) (string, []artifact, *recognizedImage, error) {
	var voice, images []string
	var files []artifact
	var image *recognizedImage
	for _, file := range input.Message.Attachments {
		if !supportedMedia(file.MIME) {
			files = append(files, artifact{Kind: "quote", Title: file.Name, Text: "Этот формат пока недоступен для распознавания."})
			continue
		}
		if file.MIME == "audio/wave" || file.MIME == "audio/x-wav" {
			file.MIME = "audio/wav"
		}
		data, err := input.ReadAttachment(file.ID)
		if err != nil {
			return "", nil, nil, problem(404, "Не удалось прочитать вложение диалога.")
		}
		report(activity{Kind: "inference", Label: "Распознаём вложение"})
		recognize := service.recognize
		if strings.HasPrefix(file.MIME, "audio/") && service.audioLimit > 0 {
			if err := probeAudio(ctx, data, service.audioLimit); err != nil {
				return "", nil, nil, err
			}
		}
		if file.MIME == "application/pdf" {
			recognize = service.preparePDF
		}
		result, err := recognize(ctx, file, data)
		if err != nil {
			return "", nil, nil, err
		}
		title := "Текст · " + file.Name
		if strings.HasPrefix(file.MIME, "audio/") {
			voice = append(voice, result.Text)
			title = "Расшифровка · " + file.Name
		} else {
			images = append(images, result.Text)
			if strings.HasPrefix(file.MIME, "image/") && imageQuestion.MatchString(input.Message.Text) {
				text := []rune(result.Text)
				image = &recognizedImage{ID: file.ID, Text: string(text[:min(len(text), 6000)]), data: data}
			}
		}
		if result.Truncated {
			title += " (первые страницы)"
		}
		files = append(files, artifact{Kind: "quote", Title: title, Text: string([]rune(result.Text)[:min(len([]rune(result.Text)), 6000)])})
	}
	if input.Message.Text == "" && len(voice) > 0 {
		return strings.Join(voice, "\n"), files, nil, nil
	}
	return strings.Join(append(voice, images...), "\n\n"), files, image, nil
}
