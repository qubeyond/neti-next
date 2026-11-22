package main

import (
	"io"
	"net/http"

	"github.com/qubeyond/neti-next/backend/internal/account"
	"github.com/qubeyond/neti-next/backend/internal/apperr"
	"github.com/qubeyond/neti-next/backend/internal/chat"
	"github.com/qubeyond/neti-next/backend/internal/events"
	"github.com/qubeyond/neti-next/backend/internal/httpapi"
	"github.com/qubeyond/neti-next/backend/internal/limits"
	"github.com/qubeyond/neti-next/backend/internal/storage"
)

func routes(db *storage.Store, agent chat.Agent, secureCookies bool) http.Handler {
	mux := http.NewServeMux()
	hub := events.New()
	turns := limits.New()
	accounts := account.NewService(db, hub, func(owner string) { turns.Cancel(owner, "") })
	accountHTTP := account.NewHandler(accounts, secureCookies)
	chats := chat.NewService(db, agent, hub, turns)
	chatHTTP := chat.NewHandler(chats, accountHTTP.Actor)

	mux.HandleFunc("GET /api/health", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"status":"ok"}`)
	})
	accountHTTP.RegisterRoutes(mux)
	chatHTTP.RegisterRoutes(mux)
	mux.HandleFunc("GET /api/v1/events", hub.Handler(accounts))
	extra := &auxiliary{accounts: accountHTTP, profiles: accounts, agent: agent, turns: turns}
	mux.HandleFunc("GET /api/v1/demo", httpapi.Handle(extra.demoAccess))
	mux.HandleFunc("POST /api/v1/demo/runs", httpapi.Handle(extra.demoRun))
	mux.HandleFunc("POST /api/v1/drafts/transcribe", httpapi.Handle(extra.transcribeDraft))
	return httpapi.Protect(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if handler, pattern := mux.Handler(r); pattern == "" {
			handler.ServeHTTP(muxProblemWriter{w}, r)
			return
		}
		mux.ServeHTTP(w, r)
	}))
}

type muxProblemWriter struct{ http.ResponseWriter }

func (w muxProblemWriter) WriteHeader(code int) {
	if code == http.StatusMethodNotAllowed {
		httpapi.WriteError(w.ResponseWriter, apperr.HTTP(code, "Метод запроса не поддерживается."))
		return
	}
	httpapi.WriteError(w.ResponseWriter, apperr.HTTP(code, "Маршрут не найден."))
}

func (w muxProblemWriter) Write(data []byte) (int, error) { return len(data), nil }
