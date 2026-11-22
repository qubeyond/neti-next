package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"testing"
	"time"

	"github.com/qubeyond/neti-next/backend/internal/agentclient"
	"github.com/qubeyond/neti-next/backend/internal/chat"
	"github.com/qubeyond/neti-next/backend/internal/identity"
	"github.com/qubeyond/neti-next/backend/internal/storage"
)

func TestProfileInstructionsAndChatContextReachAgent(t *testing.T) {
	dsn := os.Getenv("BACKEND_TEST_DATABASE_DSN")
	if dsn == "" {
		t.Skip("requires PostgreSQL; covered by Compose smoke")
	}
	db, err := storage.Open(dsn, "")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	type historyTurn struct {
		Role string `json:"role"`
		Text string `json:"text"`
	}
	type agentPayload struct {
		Question     string        `json:"question"`
		Instructions string        `json:"user_instructions"`
		History      []historyTurn `json:"history"`
	}
	requests := make(chan agentPayload, 8)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var input agentPayload
		if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
			http.Error(w, "invalid request", http.StatusBadRequest)
			return
		}
		requests <- input
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(chat.AgentAnswer{Status: "answered", Answer: "Ответ: " + input.Question})
	}))
	defer upstream.Close()
	service := httptest.NewServer(routes(db, agentclient.New(upstream.URL), false))
	defer service.Close()
	account := newBrowser(t, service.URL+"/api/v1")
	other := newBrowser(t, account.base)
	account.call("POST", "/auth/register", 201, credentials{Name: "Первый", Email: identity.NewID() + "@example.org", Password: "password123"}, nil)
	other.call("POST", "/auth/register", 201, credentials{Name: "Второй", Email: identity.NewID() + "@example.org", Password: "password123"}, nil)
	defer account.call("DELETE", "/profile", 204, nil, nil)
	defer other.call("DELETE", "/profile", 204, nil, nil)
	account.call("PATCH", "/profile", 200, map[string]string{"instructions": "Пиши кратко"}, nil)
	other.call("PATCH", "/profile", 200, map[string]string{"instructions": "Пиши подробно"}, nil)
	first, second, otherChat := account.create(), account.create(), other.create()
	guest := newBrowser(t, account.base)
	var session struct{ Token string }
	guest.call("POST", "/auth/guest", 201, nil, &session)
	guest.token = session.Token
	guestChat := guest.create()

	check := func(browser testBrowser, route, question, instructions string, history []historyTurn) {
		t.Helper()
		browser.call("POST", route+"/messages", 202, chat.SendInput{RequestID: identity.NewID(), Text: question}, nil)
		reply := browser.completed(route)
		if last := reply.Messages[len(reply.Messages)-1]; last.State != "complete" || last.Text != "Ответ: "+question {
			t.Fatalf("Ответ агента не сохранён: %+v", last)
		}
		select {
		case input := <-requests:
			if input.Question != question || input.Instructions != instructions || !reflect.DeepEqual(input.History, history) {
				t.Fatalf("Контекст %s: %+v; ожидались инструкция %q, история %+v", route, input, instructions, history)
			}
		case <-time.After(time.Second):
			t.Fatal("HTTP-запрос агенту не получен")
		}
	}
	check(account, first, "Код первого чата: кедр", "Пиши кратко", nil)
	check(account, second, "Код второго чата: сосна", "Пиши кратко", nil)
	account.call("PATCH", "/profile", 200, map[string]string{"instructions": "Пиши списком"}, nil)
	check(account, first, "Продолжи первый", "Пиши списком", []historyTurn{
		{Role: "user", Text: "Код первого чата: кедр"},
		{Role: "assistant", Text: "Ответ: Код первого чата: кедр"},
	})
	check(account, second, "Продолжи второй", "Пиши списком", []historyTurn{
		{Role: "user", Text: "Код второго чата: сосна"},
		{Role: "assistant", Text: "Ответ: Код второго чата: сосна"},
	})
	check(other, otherChat, "Вопрос другого профиля", "Пиши подробно", nil)
	check(guest, guestChat, "Вопрос гостя", "", nil)
	account.call("PATCH", "/profile", 200, map[string]string{"instructions": ""}, nil)
	check(account, account.create(), "Новый чат без инструкции", "", nil)
}
