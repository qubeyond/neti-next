package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	bolt "go.etcd.io/bbolt"

	accounts "github.com/qubeyond/neti-next/backend/internal/account"
	"github.com/qubeyond/neti-next/backend/internal/agentclient"
	"github.com/qubeyond/neti-next/backend/internal/chat"
	"github.com/qubeyond/neti-next/backend/internal/events"
	"github.com/qubeyond/neti-next/backend/internal/httpapi"
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

func (runner recordingAgent) Trace(_ context.Context, _ chat.AgentRequest, emit func(agentclient.TraceEvent) error) error {
	if err := emit(agentclient.TraceEvent{Type: "span_start", ID: "fixture", Parent: "agent", Service: "web", Operation: "search"}); err != nil {
		return err
	}
	return emit(agentclient.TraceEvent{Type: "answer", Answer: &chat.AgentAnswer{Status: "answered", Answer: "Демо"}})
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

func legacyWorkspace(t *testing.T, filename string) (string, string, string) {
	t.Helper()
	accountID, token, fileID := identity.NewID(), identity.NewID(), identity.NewID()
	db, err := bolt.Open(filename, 0600, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	err = db.Update(func(tx *bolt.Tx) error {
		owners, err := tx.CreateBucket([]byte("workspaces"))
		if err != nil {
			return err
		}
		account, err := owners.CreateBucket([]byte(accountID))
		if err != nil {
			return err
		}
		state := struct {
			Profile accounts.Profile            `json:"profile"`
			Chats   map[string]*chat.StoredChat `json:"chats"`
		}{
			Profile: accounts.Profile{ID: accountID, Email: "migrated@example.org", Name: "Импорт", Instructions: "текст\u0000из BoltDB"},
			Chats:   map[string]*chat.StoredChat{"chat": {Chat: chat.Chat{Messages: []chat.Message{{State: "working"}}}}},
		}
		raw, err := json.Marshal(state)
		if err != nil {
			return err
		}
		if err := account.Put([]byte("state"), raw); err != nil {
			return err
		}
		if err := account.Put([]byte(fileID), []byte("Старое вложение")); err != nil {
			return err
		}
		sessions, err := tx.CreateBucket([]byte("sessions"))
		if err != nil {
			return err
		}
		raw, err = json.Marshal(session{Owner: accountID, Expires: time.Now().Add(time.Hour)})
		if err != nil {
			return err
		}
		return sessions.Put([]byte(tokenKey(token)), raw)
	})
	if err != nil {
		t.Fatal(err)
	}
	return accountID, token, fileID
}

func setLegacyOrphan(t *testing.T, filename string, create bool) {
	t.Helper()
	db, err := bolt.Open(filename, 0600, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if err := db.Update(func(tx *bolt.Tx) error {
		bucket := tx.Bucket([]byte("sessions"))
		key := []byte("orphan-session")
		if !create {
			return bucket.Delete(key)
		}
		raw, err := json.Marshal(session{Owner: "missing-owner", Expires: time.Now().Add(time.Hour)})
		if err != nil {
			return err
		}
		return bucket.Put(key, raw)
	}); err != nil {
		t.Fatal(err)
	}
}

func TestWorkspaceIsolationAndEvents(t *testing.T) {
	dsn := os.Getenv("BACKEND_TEST_DATABASE_DSN")
	if dsn == "" {
		t.Skip("requires an empty PostgreSQL database; covered by Compose smoke")
	}
	filename := filepath.Join(t.TempDir(), "workspace.db")
	migratedID, migratedToken, migratedFile := legacyWorkspace(t, filename)
	setLegacyOrphan(t, filename, true)
	if unsuccessful, err := storage.Open(dsn, filename); err == nil {
		unsuccessful.Close()
		t.Fatal("Импорт с отсутствующим владельцем должен отмениться полностью")
	}
	setLegacyOrphan(t, filename, false)
	db, err := storage.Open(dsn, filename)
	if err != nil {
		t.Fatal(err)
	}
	migrated, err := db.Authenticate(migratedToken, false)
	if err != nil || migrated.Owner != migratedID {
		t.Fatalf("Сессия импорта: %+v, %v", migrated, err)
	}
	err = db.ViewChats(migrated, func(state *chat.State, files chat.Blobs) error {
		data, err := files.Get(migratedFile)
		if err != nil || string(data) != "Старое вложение" || state.Instructions != "текст\u0000из BoltDB" ||
			state.Chats["chat"].Messages[0].State != "failed" {
			t.Fatalf("Импорт профиля, чата или вложения: %v", err)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	runner := recordingAgent{requests: make(chan chat.AgentRequest, 4)}
	service := httptest.NewServer(routes(db, runner, false))
	defer service.Close()
	defer db.Close()
	base := service.URL + "/api/v1"
	account := newBrowser(t, base)
	other := newBrowser(t, base)
	var demo accounts.Profile
	account.call("POST", "/auth/register", 201, credentials{Name: "Лена", Email: "lena@example.org", Password: "password123"}, &demo)
	other.call("POST", "/auth/register", 201, credentials{Name: "Илья", Email: "ilya@example.org", Password: "password123"}, nil)
	t.Setenv("BACKEND_DEMO_ACCOUNT_ID", demo.ID)
	account.call("GET", "/demo", 200, nil, nil)
	other.call("POST", "/demo/runs", 403, map[string]string{"question": "Вопрос"}, nil)
	newBrowser(t, base).call("GET", "/demo", 401, nil, nil)
	response, err := account.http.Post(base+"/demo/runs", "application/json", strings.NewReader(`{"question":"Вопрос"}`))
	if err != nil {
		t.Fatal(err)
	}
	stream := json.NewDecoder(response.Body)
	for _, eventType := range []string{"span_start", "span_start", "span_end", "answer"} {
		var event agentclient.TraceEvent
		if err := stream.Decode(&event); err != nil || event.Type != eventType || event.RequestID == "" {
			response.Body.Close()
			t.Fatalf("Событие %s: %+v (%v)", eventType, event, err)
		}
	}
	response.Body.Close()
	if response.StatusCode != 200 || response.Header.Get("Content-Type") != demoStreamType {
		t.Fatalf("Некорректный поток: %d, %s", response.StatusCode, response.Header.Get("Content-Type"))
	}

	account.call("PATCH", "/profile", 200, map[string]string{"name": "Елена", "theme": "dark", "instructions": "Отвечай кратко."}, nil)
	first := account.create()
	second := account.create()
	file := account.upload(first)
	other.call("GET", first, 404, nil, nil)
	other.call("GET", first+"/files/"+file.ID, 404, nil, nil)
	account.call("GET", first+"/files/"+file.ID, 200, nil, nil)
	account.call("POST", second+"/messages", 422, chat.SendInput{RequestID: identity.NewID(), Text: "Другой диалог", FileIDs: []string{file.ID}}, nil)

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
	firstRequest := chat.SendInput{RequestID: identity.NewID(), Text: "Как поступить?", FileIDs: []string{file.ID}}
	account.call("POST", first+"/messages", 202, firstRequest, nil)
	firstReply := account.completed(first)
	account.call("POST", first+"/messages", 202, firstRequest, nil)
	account.call("POST", first+"/messages", 202, chat.SendInput{RequestID: identity.NewID(), Text: "А в магистратуру?"}, nil)
	account.completed(first)
	account.call("POST", second+"/messages", 202, chat.SendInput{RequestID: identity.NewID(), Text: "Вопрос в другом диалоге."}, nil)
	account.completed(second)
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
	if len(answer.Activity) != 1 || len(answer.Citations) != 1 || len(answer.Artifacts) != 1 {
		t.Fatalf("Потеря данных ответа: %+v", answer)
	}
	service.Close()
	db.Close()
	db, err = storage.Open(dsn, filename)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if account, err := db.Authenticate(migratedToken, false); err != nil || account.Owner != migratedID {
		t.Fatalf("Импортированная сессия после перезапуска: %+v, %v", account, err)
	}
	reopened := httptest.NewServer(routes(db, runner, false))
	defer reopened.Close()
	account.base = reopened.URL + "/api/v1"
	guest.base = account.base
	var settings accounts.Profile
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
		var input struct {
			Question string `json:"question"`
			Quoted   string `json:"quoted_text"`
			History  []struct {
				Role string
				Text string
			} `json:"history"`
		}
		json.NewDecoder(r.Body).Decode(&input)
		if r.Method != "POST" || r.URL.Path != "/agent/v1/answers" ||
			input.Question != "Уточните" || input.Quoted != "Цитируемый фрагмент" ||
			len(input.History) != 1 || input.History[0].Text != "Сохранённый диалог" {
			t.Errorf("Неверный HTTP-запрос: %s %s %+v", r.Method, r.URL.Path, input)
		}
		fmt.Fprint(w, `{"status":"partial","answer":"Ответ","citations":[]}`)
	}))
	defer service.Close()
	result, err := agentclient.New(service.URL).Answer(context.Background(), chat.AgentRequest{
		Message: chat.Message{Text: "Уточните", Quote: "Цитируемый фрагмент"},
		History: []chat.Message{{Role: "user", Text: "Сохранённый диалог"}}, Instructions: "Настройка профиля",
	}, func(chat.Activity) {})
	if err != nil || result.Status != "partial" {
		t.Fatalf("Ответ: %+v, ошибка: %v", result, err)
	}
}

func TestAttachmentOnlyOCRUsesInferenceWithoutAgent(t *testing.T) {
	inference := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]string
		json.NewDecoder(r.Body).Decode(&body)
		if r.URL.Path != "/inference/v1/ocr" || r.Header.Get("Authorization") != "Bearer test-token" ||
			body["mime"] != "image/png" || body["data"] == "" {
			t.Errorf("Неверный запрос распознавания: %s %+v", r.URL.Path, body)
		}
		fmt.Fprint(w, `{"text":"Расписание 2036","truncated":false}`)
	}))
	defer inference.Close()

	client := agentclient.New("http://127.0.0.1:1")
	if err := client.Configure("", inference.URL, "test-token", 3*time.Minute); err != nil {
		t.Fatal(err)
	}
	reply, err := client.Answer(context.Background(), chat.AgentRequest{
		Message: chat.Message{Attachments: []chat.Attachment{{ID: "fixture", Name: "test.png", MIME: "image/png"}}},
		ReadAttachment: func(id string) ([]byte, error) {
			if id != "fixture" {
				t.Errorf("Неожиданный идентификатор %s", id)
			}
			return []byte("png"), nil
		},
	}, func(chat.Activity) {})
	if err != nil || reply.Status != "answered" || len(reply.Artifacts) != 1 || reply.Artifacts[0].Text != "Расписание 2036" {
		t.Fatalf("Ответ: %+v, ошибка: %v", reply, err)
	}
}

type cancellableAgent struct{ started chan int }

func (runner cancellableAgent) Answer(ctx context.Context, _ chat.AgentRequest, _ func(chat.Activity)) (chat.AgentAnswer, error) {
	runner.started <- 1
	<-ctx.Done()
	return chat.AgentAnswer{}, ctx.Err()
}

func TestTurnQuotaCancellationAndProblemContract(t *testing.T) {
	db := storage.NewGuestStore()
	t.Setenv("BACKEND_MAX_ACTIVE_TURNS", "1")
	runner := cancellableAgent{started: make(chan int, 1)}
	server := httptest.NewServer(routes(db, runner, false))
	defer server.Close()
	client := newBrowser(t, server.URL+"/api/v1")
	var guest map[string]string
	client.call("POST", "/auth/guest", http.StatusCreated, nil, &guest)
	client.token = guest["token"]
	first, second := client.create(), client.create()
	var accepted map[string]string
	client.call("POST", first+"/messages", 202, chat.SendInput{RequestID: identity.NewID(), Text: "Когда занятия?"}, &accepted)
	select {
	case <-runner.started:
	case <-time.After(time.Second):
		t.Fatal("Агент не получил ход")
	}
	var busy httpapi.Problem
	client.call("POST", second+"/messages", 429, chat.SendInput{RequestID: identity.NewID(), Text: "Второй вопрос"}, &busy)
	var untouched chat.Chat
	client.call("GET", second, 200, nil, &untouched)
	if len(untouched.Messages) != 0 {
		t.Fatalf("Отклонённый запрос изменил гостевой чат: %+v", untouched.Messages)
	}
	if busy.RequestID == "" || busy.Type != "urn:neti:problem:too-many-requests" {
		t.Fatalf("Неверный формат ошибки: %+v", busy)
	}
	client.call("POST", first+"/messages/"+accepted["messageId"]+"/cancel", 202, nil, nil)
	item := client.completed(first)
	last := item.Messages[len(item.Messages)-1]
	if last.Problem == nil || last.Problem.Status != 409 || last.State != "failed" {
		t.Fatalf("Запрос не отменён: %+v", last)
	}
}

type session struct {
	Owner   string    `json:"owner"`
	Expires time.Time `json:"expires"`
}

func tokenKey(value string) string {
	digest := sha256.Sum256([]byte(value))
	return hex.EncodeToString(digest[:])
}

type credentials struct {
	Name     string `json:"name"`
	Email    string `json:"email"`
	Password string `json:"password"`
}
