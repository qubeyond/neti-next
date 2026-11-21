package main

import (
	"bytes"
	"context"
	"encoding/json"
	"image"
	"image/png"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
)

func TestDemoTraceSharesChatHistoryAndMedia(t *testing.T) {
	var picture bytes.Buffer
	if err := png.Encode(&picture, image.NewRGBA(image.Rect(0, 0, 24, 24))); err != nil {
		t.Fatal(err)
	}
	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)
	err := writer.WriteField("input", `{"question":"Что на фото?","quoted_text":"Фрагмент","history":[{"role":"user","text":"Где в НГУ берут справку?"},{"role":"assistant","text":"Какой документ нужен?"}]}`)
	if err != nil {
		t.Fatal(err)
	}
	part, err := writer.CreateFormFile("file", "fixture.png")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := part.Write(picture.Bytes()); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/api/v1/demo/runs", body)
	request.Header.Set("Content-Type", writer.FormDataContentType())
	input, err := readDemoInput(httptest.NewRecorder(), request)
	if err != nil {
		t.Fatal(err)
	}
	requests := make(chan map[string]any, 2)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/inference/v1/ocr" {
			w.Write([]byte(`{"text":"Справку выдают в кабинете 204.","model":"test-ocr","latency_ms":4,"usage":{"total_tokens":13}}`))
			return
		}
		var input map[string]any
		if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
			t.Error(err)
			w.WriteHeader(422)
			return
		}
		requests <- input
		if r.URL.Path == "/agent/v1/answers/trace" {
			w.Write([]byte("{\"type\":\"answer\",\"answer\":{\"status\":\"answered\",\"answer\":\"Ответ\"}}\n"))
			return
		}
		w.Write([]byte(`{"status":"answered","answer":"Ответ"}`))
	}))
	defer server.Close()
	adapter := newLegacyAgent(server.URL)
	adapter.token = "test-token"
	adapter.inference = &inferenceClient{base: server.URL, client: adapter.client}
	ctx := context.Background()

	answer, err := adapter.answer(ctx, input, func(activity) {})
	if err != nil {
		t.Fatal(err)
	}
	var events []demoEvent
	if err := adapter.trace(ctx, input, func(event demoEvent) error {
		events = append(events, event)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	primary, traced := <-requests, <-requests
	if !reflect.DeepEqual(primary, traced) || primary["history"] == nil || primary["image_source"] == nil {
		t.Fatalf("Нормальный запрос и поток получили разные данные: %v, %v", primary, traced)
	}
	if len(events) != 5 || events[1].Operation != "ocr" || events[2].Usage == nil || !reflect.DeepEqual(events[4].Answer, &answer) {
		t.Fatalf("Трассировка не содержит фактический OCR и итог: %+v", events)
	}
}

func TestDemoRejectsInvalidHistoryAndUnexpectedFields(t *testing.T) {
	for _, fixture := range []io.Reader{
		strings.NewReader(`{"question":"Вопрос","history":[{"role":"system","text":"Тестовый текст"}]}`),
		strings.NewReader(`{"question":"Вопрос","file_id":"чужое вложение"}`),
	} {
		request := httptest.NewRequest(http.MethodPost, "/api/v1/demo/runs", fixture)
		if _, err := readDemoInput(httptest.NewRecorder(), request); err == nil {
			t.Fatal("Некорректные данные дошли до агента")
		}
	}
}
