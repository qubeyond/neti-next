package main

import (
	"io"
	"net/http"

	"github.com/qubeyond/neti-next/backend/internal/account"
	"github.com/qubeyond/neti-next/backend/internal/chat"
	"github.com/qubeyond/neti-next/backend/internal/events"
	"github.com/qubeyond/neti-next/backend/internal/httpapi"
	"github.com/qubeyond/neti-next/backend/internal/storage"
)

func routes(db *storage.Store, agent chat.Agent, secureCookies bool) http.Handler {
	mux := http.NewServeMux()
	hub := events.New()
	accounts := account.NewService(db, hub)
	accountHTTP := account.NewHandler(accounts, secureCookies)
	chats := chat.NewService(db, agent, hub)
	chatHTTP := chat.NewHandler(chats, accountHTTP.Actor)

	mux.HandleFunc("GET /api/health", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"status":"ok"}`)
	})
	accountHTTP.RegisterRoutes(mux)
	chatHTTP.RegisterRoutes(mux)
	mux.HandleFunc("GET /api/v1/events", hub.Handler(accounts))
	return httpapi.Protect(mux)
}
