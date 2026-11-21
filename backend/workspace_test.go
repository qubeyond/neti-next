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
)

type recordingAgent struct {
	requests chan agentRequest
}

func (runner recordingAgent) answer(_ context.Context, input agentRequest, report func(activity)) (agentAnswer, error) {
	if len(input.Message.Attachments) > 0 {
		data, err := input.ReadAttachment(input.Message.Attachments[0].ID)
		if err != nil || string(data) != "Тестовое вложение" {
			return agentAnswer{}, fmt.Errorf("Не удалось прочитать вложение: %v", err)
		}
	}
	runner.requests <- input
	report(activity{Kind: "tool", Label: "Поиск по материалам"})
	return agentAnswer{Status: "answered", Answer: "**Ответ**", Citations: []citation{{URL: "https://example.org", Quote: "Цитата"}},
		Artifacts: []artifact{{Kind: "link", Title: "Материал", URL: "https://example.org"}}}, nil
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
	var item chatSummary
	b.call("POST", "/chats", 201, nil, &item)
	return "/chats/" + item.ID
}

func (b testBrowser) completed(route string) chat {
	b.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		var item chat
		b.call("GET", route, 200, nil, &item)
		if len(item.Messages) > 0 && item.Messages[len(item.Messages)-1].State != "working" {
			return item
		}
		time.Sleep(time.Millisecond * 10)
	}
	b.Fatal("Ассистент не завершил запрос.")
	return chat{}
}

func (b testBrowser) upload(route string) attachment {
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
	var item attachment
	if err := json.NewDecoder(response.Body).Decode(&item); err != nil {
		b.Fatal(err)
	}
	return item
}

func TestWorkspaceIsolationAndEvents(t *testing.T) {
	filename := filepath.Join(t.TempDir(), "workspace.db")
	db, err := newStore(filename)
	if err != nil {
		t.Fatal(err)
	}
	runner := recordingAgent{requests: make(chan agentRequest, 4)}
	service := httptest.NewServer(newServer(db, runner, false).routes())
	defer service.Close()
	defer db.db.Close()
	base := service.URL + "/api/v1"
	account := newBrowser(t, base)
	other := newBrowser(t, base)
	account.call("POST", "/auth/register", 201, credentials{Name: "Лена", Email: "lena@example.org", Password: "password123"}, nil)
	other.call("POST", "/auth/register", 201, credentials{Name: "Илья", Email: "ilya@example.org", Password: "password123"}, nil)
	account.call("PATCH", "/profile", 200, map[string]string{"name": "Елена", "theme": "dark", "instructions": "Отвечай кратко."}, nil)
	first := account.create()
	second := account.create()
	file := account.upload(first)
	other.call("GET", first, 404, nil, nil)
	other.call("GET", first+"/files/"+file.ID, 404, nil, nil)
	account.call("GET", first+"/files/"+file.ID, 200, nil, nil)
	account.call("POST", second+"/messages", 422, sendInput{RequestID: randomID(), Text: "Другой диалог", FileIDs: []string{file.ID}}, nil)

	guest := newBrowser(t, base)
	var guestSession struct{ Token string }
	guest.call("POST", "/auth/guest", 201, nil, &guestSession)
	guest.token = guestSession.Token
	guestChat := guest.create()
	guest.call("GET", first, 404, nil, nil)
	account.call("GET", guestChat, 404, nil, nil)
	dialer := websocket.Dialer{Subprotocols: []string{"neti.v1", "guest." + guest.token}}
	websocketURL := "ws" + strings.TrimPrefix(base, "http") + "/events"
	socket, response, err := dialer.Dial(websocketURL, http.Header{"Origin": []string{service.URL}})
	if err != nil {
		t.Fatalf("WebSocket: %v (%v)", err, response)
	}
	defer socket.Close()
	firstRequest := sendInput{RequestID: randomID(), Text: "Как поступить?", FileIDs: []string{file.ID}}
	account.call("POST", first+"/messages", 202, firstRequest, nil)
	firstReply := account.completed(first)
	account.call("POST", first+"/messages", 202, firstRequest, nil)
	account.call("POST", first+"/messages", 202, sendInput{RequestID: randomID(), Text: "А в магистратуру?"}, nil)
	account.completed(first)
	account.call("POST", second+"/messages", 202, sendInput{RequestID: randomID(), Text: "Вопрос в другом диалоге."}, nil)
	account.completed(second)
	guest.call("POST", guestChat+"/messages", 202, sendInput{RequestID: randomID(), Text: "Гостевой вопрос."}, nil)
	socket.SetReadDeadline(time.Now().Add(3 * time.Second))
	var change event
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
	db.db.Close()
	db, err = newStore(filename)
	if err != nil {
		t.Fatal(err)
	}
	defer db.db.Close()
	reopened := httptest.NewServer(newServer(db, runner, false).routes())
	defer reopened.Close()
	account.base = reopened.URL + "/api/v1"
	guest.base = account.base
	var settings profile
	account.call("GET", "/profile", 200, nil, &settings)
	if settings.Theme != "dark" || settings.Name != "Елена" {
		t.Fatalf("Профиль потерян после перезапуска: %+v", settings)
	}
	account.call("GET", first+"/files/"+file.ID, 200, nil, nil)
	guest.call("GET", guestChat, 401, nil, nil)
	account.call("DELETE", "/chats", 204, nil, nil)
	account.call("GET", first, 404, nil, nil)
	account.call("DELETE", "/profile", 204, nil, nil)
	account.call("POST", "/auth/login", 401, credentials{Email: "lena@example.org", Password: "password123"}, nil)
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
	result, err := newLegacyAgent(service.URL).answer(context.Background(), agentRequest{
		Message: message{Text: "Уточните", Quote: "Цитируемый фрагмент"},
		History: []message{{Text: "Сохранённый диалог"}}, Instructions: "Настройка профиля",
	}, func(activity) {})
	if err != nil || result.Status != "partial" {
		t.Fatalf("Ответ: %+v, ошибка: %v", result, err)
	}
}
