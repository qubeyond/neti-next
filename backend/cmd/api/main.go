package main

import (
	"log"
	"net/http"
	"os"
	"time"

	"github.com/qubeyond/neti-next/backend/internal/agentclient"
	"github.com/qubeyond/neti-next/backend/internal/storage"
)

func setting(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func main() {
	db, err := storage.Open(setting("BACKEND_DATA_FILE", "neti-data/workspace.db"))
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()
	handler := routes(db, agentclient.New(setting("BACKEND_AGENT_URL", "http://127.0.0.1:8001")), os.Getenv("BACKEND_SECURE_COOKIES") == "true")
	go func() {
		ticker := time.NewTicker(time.Minute)
		defer ticker.Stop()
		for range ticker.C {
			db.ExpireGuests()
		}
	}()
	server := &http.Server{
		Addr: ":" + setting("BACKEND_PORT", "8000"), Handler: handler,
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 45 * time.Second, IdleTimeout: 60 * time.Second,
	}
	log.Fatal(server.ListenAndServe())
}
