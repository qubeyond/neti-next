package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/qubeyond/neti-next/backend/internal/account"
	"github.com/qubeyond/neti-next/backend/internal/agentclient"
	"github.com/qubeyond/neti-next/backend/internal/chat"
	"github.com/qubeyond/neti-next/backend/internal/events"
	"github.com/qubeyond/neti-next/backend/internal/identity"
	"github.com/qubeyond/neti-next/backend/internal/storage"
)

type recordingAgent struct {
	requests chan chat.AgentRequest
}

func (runner recordingAgent) Answer(_ context.Context, input chat.AgentRequest, report func(chat.Activity)) (chat.AgentAnswer, error) {
	if len(input.Message.Attachments) > 0 {
		data, err := input.ReadAttachment(input.Message.Attachments[0].ID)
		if err != nil || string(data) != "Тестовое вложение" {
			return chat.AgentAnswer{}, fmt.Errorf("Не удалось прочитать вложение: %v", err)
		}
	}
	runner.requests <- input
	report(chat.Activity{Kind: "tool", Label: "Поиск по материалам"})
	return chat.AgentAnswer{Status: "answered", Answer: "**Ответ**", Citations: []chat.Citation{{URL: "https://example.org", Quote: "Цитата"}},
		Artifacts: []chat.Artifact{{Kind: "link", Title: "Материал", URL: "https://example.org"}}}, nil
}

type testBrowser struct {
	*testing.T
	base  string
	http  *http.Client
	token string
}

func newBrowser(t *testing.T, base string) testBrowser {
	t.Helper()
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	return testBrowser{T: t, base: base, http: &http.Client{Jar: jar, Timeout: 3 * time.Second}}
}

func (b testBrowser) call(method, route string, status int, content any, target any) {
	b.Helper()
	var body io.Reader
	if content != nil {
		data, err := json.Marshal(content)
		if err != nil {
			b.Fatal(err)
		}
		body = bytes.NewReader(data)
	}
	request, err := http.NewRequest(method, b.base+route, body)
	if err != nil {
		b.Fatal(err)
	}
	request.Header.Set("Content-Type", "application/json")
	b.headers(request)
	response, err := b.http.Do(request)
	if err != nil {
		b.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != status {
		data, _ := io.ReadAll(response.Body)
		b.Fatalf("%s %s: got %d, want %d: %s", method, route, response.StatusCode, status, data)
	}
	if target != nil {
		if err := json.NewDecoder(response.Body).Decode(target); err != nil {
			b.Fatal(err)
		}
	}
}

func (b testBrowser) headers(request *http.Request) {
	request.Header.Set("Origin", strings.TrimSuffix(b.base, "/api/v1"))
	if b.token != "" {
		request.Header.Set("Authorization", "Bearer "+b.token)
	}
}

func (b testBrowser) create() string {
	b.Helper()
	var item chat.Summary
	b.call("POST", "/chats", 201, nil, &item)
	return "/chats/" + item.ID
}

func (b testBrowser) completed(route string) chat.Chat {
	b.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		var item chat.Chat
		b.call("GET", route, 200, nil, &item)
		if len(item.Messages) > 0 && item.Messages[len(item.Messages)-1].State != "working" {
			return item
		}
		time.Sleep(time.Millisecond * 10)
	}
	b.Fatal("Ассистент не завершил запрос.")
	return chat.Chat{}
}

func (b testBrowser) upload(route string) chat.Attachment {
	b.Helper()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	part, err := writer.CreateFormFile("file", "sample.txt")
	if err != nil {
		b.Fatal(err)
	}
	io.WriteString(part, "Тестовое вложение")
	writer.Close()
	request, err := http.NewRequest("POST", b.base+route+"/files", &body)
	if err != nil {
		b.Fatal(err)
	}
	request.Header.Set("Content-Type", writer.FormDataContentType())
	b.headers(request)
	response, err := b.http.Do(request)
	if err != nil {
		b.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 201 {
		b.Fatal(response.StatusCode)
	}
	var item chat.Attachment
	if err := json.NewDecoder(response.Body).Decode(&item); err != nil {
		b.Fatal(err)
	}
	return item
}

func TestWorkspaceIsolationAndEvents(t *testing.T) {
	filename := filepath.Join(t.TempDir(), "workspace.db")
	db, err := storage.Open(filename)
	if err != nil {
		t.Fatal(err)
	}
	runner := recordingAgent{requests: make(chan chat.AgentRequest, 4)}
	service := httptest.NewServer(routes(db, runner, false))
	defer service.Close()
	defer db.Close()
	base := service.URL + "/api/v1"
	browser := newBrowser(t, base)
	other := newBrowser(t, base)
	browser.call("POST", "/auth/register", 201, map[string]string{"name": "Лена", "email": "lena@example.org", "password": "password123"}, nil)
	other.call("POST", "/auth/register", 201, map[string]string{"name": "Илья", "email": "ilya@example.org", "password": "password123"}, nil)
	browser.call("PATCH", "/profile", 200, map[string]string{"name": "Елена", "theme": "dark", "instructions": "Отвечай кратко."}, nil)
	first := browser.create()
	second := browser.create()
	file := browser.upload(first)
	other.call("GET", first, 404, nil, nil)
	other.call("GET", first+"/files/"+file.ID, 404, nil, nil)
	browser.call("GET", first+"/files/"+file.ID, 200, nil, nil)
	browser.call("POST", second+"/messages", 422, chat.SendInput{RequestID: identity.NewID(), Text: "Другой диалог", FileIDs: []string{file.ID}}, nil)

	guest := newBrowser(t, base)
	var guestSession struct{ Token string }
	guest.call("POST", "/auth/guest", 201, nil, &guestSession)
	guest.token = guestSession.Token
	guestChat := guest.create()
	guest.call("GET", first, 404, nil, nil)
	browser.call("GET", guestChat, 404, nil, nil)
	dialer := websocket.Dialer{Subprotocols: []string{"neti.v1", "guest." + guest.token}}
	websocketURL := "ws" + strings.TrimPrefix(base, "http") + "/events"
	socket, response, err := dialer.Dial(websocketURL, http.Header{"Origin": []string{service.URL}})
	if err != nil {
		t.Fatalf("WebSocket: %v (%v)", err, response)
	}
	defer socket.Close()
	firstRequest := chat.SendInput{RequestID: identity.NewID(), Text: "Как поступить?", FileIDs: []string{file.ID}}
	browser.call("POST", first+"/messages", 202, firstRequest, nil)
	firstReply := browser.completed(first)
	browser.call("POST", first+"/messages", 202, firstRequest, nil)
	browser.call("POST", first+"/messages", 202, chat.SendInput{RequestID: identity.NewID(), Text: "А в магистратуру?"}, nil)
	browser.completed(first)
	browser.call("POST", second+"/messages", 202, chat.SendInput{RequestID: identity.NewID(), Text: "Вопрос в другом диалоге."}, nil)
	browser.completed(second)
	guest.call("POST", guestChat+"/messages", 202, chat.SendInput{RequestID: identity.NewID(), Text: "Гостевой вопрос."}, nil)
	socket.SetReadDeadline(time.Now().Add(3 * time.Second))
	var change events.Event
	if err := socket.ReadJSON(&change); err != nil || change.ChatID != strings.TrimPrefix(guestChat, "/chats/") {
		t.Fatalf("Неверное событие гостя: %+v, %v", change, err)
	}
	guest.completed(guestChat)
	for index, expected := range []int{0, 2, 0, 0} {
		select {
		case input := <-runner.requests:
			if len(input.History) != expected {
				t.Fatalf("Запрос %d: история = %d", index, len(input.History))
			}
			if index < 3 && input.Instructions != "Отвечай кратко." {
				t.Fatal("Инструкция профиля не передана адаптеру")
			}
			if index == 3 && input.Instructions != "" {
				t.Fatal("Гость получил настройки профиля")
			}
		default:
			t.Fatal("Запрос агента не получен.")
		}
	}
	answer := firstReply.Messages[1]
	if len(answer.Activity) != 3 || len(answer.Citations) != 1 || len(answer.Artifacts) != 1 {
		t.Fatalf("Потеря данных ответа: %+v", answer)
	}
	service.Close()
	db.Close()
	db, err = storage.Open(filename)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	reopened := httptest.NewServer(routes(db, runner, false))
	defer reopened.Close()
	browser.base = reopened.URL + "/api/v1"
	guest.base = browser.base
	var settings account.Profile
	browser.call("GET", "/profile", 200, nil, &settings)
	if settings.Theme != "dark" || settings.Name != "Елена" {
		t.Fatalf("Профиль потерян после перезапуска: %+v", settings)
	}
	browser.call("GET", first+"/files/"+file.ID, 200, nil, nil)
	guest.call("GET", guestChat, 401, nil, nil)
	browser.call("DELETE", "/chats", 204, nil, nil)
	browser.call("GET", first, 404, nil, nil)
	browser.call("DELETE", "/profile", 204, nil, nil)
	browser.call("POST", "/auth/login", 401, map[string]string{"email": "lena@example.org", "password": "password123"}, nil)
}

func TestLegacyAgentQuestionContract(t *testing.T) {
	service := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var input map[string]string
		json.NewDecoder(r.Body).Decode(&input)
		if r.Method != "POST" || r.URL.Path != "/agent/v1/answers" || len(input) != 1 || !strings.Contains(input["question"], "Цитируемый фрагмент") {
			t.Errorf("Неверный HTTP-запрос: %s %s %+v", r.Method, r.URL.Path, input)
		}
		fmt.Fprint(w, `{"status":"partial","answer":"Ответ","citations":[]}`)
	}))
	defer service.Close()
	result, err := agentclient.New(service.URL).Answer(context.Background(), chat.AgentRequest{
		Message: chat.Message{Text: "Уточните", Quote: "Цитируемый фрагмент"},
		History: []chat.Message{{Text: "Сохранённый диалог"}}, Instructions: "Настройка профиля",
	}, func(chat.Activity) {})
	if err != nil || result.Status != "partial" {
		t.Fatalf("Ответ: %+v, ошибка: %v", result, err)
	}
}
