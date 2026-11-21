package main

import (
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type failure struct {
	code int
	text string
}

func (error failure) Error() string       { return error.text }
func problem(code int, text string) error { return failure{code, text} }

type server struct {
	store         *store
	agent         agent
	events        *eventHub
	secureCookies bool
}

func newServer(store *store, agent agent, secureCookies bool) *server {
	return &server{store: store, agent: agent, events: newEventHub(), secureCookies: secureCookies}
}

func (s *server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/health", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"status":"ok"}`)
	})
	mux.HandleFunc("POST /api/v1/auth/register", handle(s.register))
	mux.HandleFunc("POST /api/v1/auth/login", handle(s.login))
	mux.HandleFunc("POST /api/v1/auth/logout", handle(s.logout))
	mux.HandleFunc("POST /api/v1/auth/guest", handle(s.createGuest))
	mux.HandleFunc("DELETE /api/v1/auth/guest", handle(s.deleteGuest))
	mux.HandleFunc("GET /api/v1/profile", handle(s.getProfile))
	mux.HandleFunc("PATCH /api/v1/profile", handle(s.updateProfile))
	mux.HandleFunc("DELETE /api/v1/profile", handle(s.deleteProfile))
	mux.HandleFunc("GET /api/v1/chats", handle(s.listChats))
	mux.HandleFunc("POST /api/v1/chats", handle(s.createChat))
	mux.HandleFunc("DELETE /api/v1/chats", handle(s.deleteChats))
	mux.HandleFunc("GET /api/v1/chats/{chat}", handle(s.getChat))
	mux.HandleFunc("DELETE /api/v1/chats/{chat}", handle(s.deleteChat))
	mux.HandleFunc("POST /api/v1/chats/{chat}/files", handle(s.upload))
	mux.HandleFunc("GET /api/v1/chats/{chat}/files/{file}", handle(s.download))
	mux.HandleFunc("DELETE /api/v1/chats/{chat}/files/{file}", handle(s.deleteFile))
	mux.HandleFunc("POST /api/v1/chats/{chat}/messages", handle(s.send))
	mux.HandleFunc("POST /api/v1/chats/{chat}/messages/{message}/retry", handle(s.retry))
	mux.HandleFunc("GET /api/v1/events", handle(s.connectEvents))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		if !sameOrigin(r) {
			writeError(w, problem(http.StatusForbidden, "Запрос с другого сайта отклонён."))
			return
		}
		mux.ServeHTTP(w, r)
	})
}

func sameOrigin(r *http.Request) bool {
	origin := r.Header.Get("Origin")
	if origin == "" {
		return true
	}
	parsed, err := url.Parse(origin)
	return err == nil && (parsed.Scheme == "http" || parsed.Scheme == "https") &&
		parsed.User == nil && parsed.Path == "" && parsed.RawQuery == "" && parsed.Fragment == "" && strings.EqualFold(parsed.Host, r.Host)
}

func handle(action func(http.ResponseWriter, *http.Request) error) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if err := action(w, r); err != nil {
			writeError(w, err)
		}
	}
}

func writeError(w http.ResponseWriter, err error) {
	var reply failure
	var tooLarge *http.MaxBytesError
	switch {
	case errors.As(err, &reply):
	case errors.Is(err, errGone):
		reply = failure{401, "Сессия завершена. Войдите или начните новый гостевой сеанс."}
	case errors.As(err, &tooLarge):
		reply = failure{413, "Размер запроса превышает лимит."}
	default:
		log.Printf("backend: %T", err)
		reply = failure{500, "Не удалось выполнить запрос. Попробуйте ещё раз."}
	}
	writeJSON(w, reply.code, map[string]string{"detail": reply.text})
}

func writeJSON(w http.ResponseWriter, code int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(value)
}

func readJSON(w http.ResponseWriter, r *http.Request, value any) error {
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(value); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			return err
		}
		return problem(422, "Проверьте поля запроса.")
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return problem(422, "Проверьте поля запроса.")
	}
	return nil
}

const sessionCookie = "neti_session"

func (s *server) cookie(w http.ResponseWriter, r *http.Request, value string, maxAge int) {
	http.SetCookie(w, &http.Cookie{Name: sessionCookie, Value: value, Path: "/api/v1",
		HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies || r.TLS != nil, MaxAge: maxAge})
}

func (s *server) account(r *http.Request) (identity, error) {
	cookie, err := r.Cookie(sessionCookie)
	if err != nil {
		return identity{}, errGone
	}
	return s.store.authenticate(cookie.Value, false)
}

func (s *server) actor(r *http.Request) (identity, error) {
	if header := r.Header.Get("Authorization"); header != "" {
		value, found := strings.CutPrefix(header, "Bearer ")
		if !found {
			return identity{}, errGone
		}
		return s.store.authenticate(value, true)
	}
	return s.account(r)
}

func now() time.Time { return time.Now().UTC() }
