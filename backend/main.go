package main

import (
	"log"
	"net/http"
	"os"
	"time"
)

func setting(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func main() {
	pdf, err := loadPDFLimits()
	if err != nil {
		log.Fatal(err)
	}
	dsn := os.Getenv("BACKEND_DATABASE_DSN")
	if dsn == "" {
		log.Fatal("BACKEND_DATABASE_DSN is required")
	}
	db, err := newStore(dsn, setting("BACKEND_DATA_FILE", "neti-data/workspace.db"))
	if err != nil {
		log.Fatal(err)
	}
	defer db.db.Close()
	agentClient := newLegacyAgent(setting("BACKEND_AGENT_URL", "http://127.0.0.1:8001"))
	agentClient.token = os.Getenv("AGENT_TOKEN")
	agentClient.inference = &inferenceClient{
		base: setting("INFERENCE_URL", "http://127.0.0.1:8002"), token: os.Getenv("INFERENCE_TOKEN"), client: agentClient.client, pdf: pdf, audioLimit: time.Duration(positiveSetting("BACKEND_AUDIO_MAX_SECONDS", "180")) * time.Second,
	}
	backend := newServer(db, agentClient, os.Getenv("BACKEND_SECURE_COOKIES") == "true")
	go func() {
		ticker := time.NewTicker(time.Minute)
		defer ticker.Stop()
		for range ticker.C {
			db.expireGuests()
		}
	}()
	server := &http.Server{
		Addr: ":" + setting("BACKEND_PORT", "8000"), Handler: backend.routes(),
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 45 * time.Second, IdleTimeout: 60 * time.Second,
	}
	log.Fatal(server.ListenAndServe())
}
