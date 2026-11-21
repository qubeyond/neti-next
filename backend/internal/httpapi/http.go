package httpapi

import (
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"

	"github.com/qubeyond/neti-next/backend/internal/apperr"
)

func Protect(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		if !SameOrigin(r) {
			WriteError(w, apperr.New(apperr.Forbidden, "Запрос с другого сайта отклонён."))
			return
		}
		next.ServeHTTP(w, r)
	})
}

func SameOrigin(r *http.Request) bool {
	origin := r.Header.Get("Origin")
	if origin == "" {
		return true
	}
	parsed, err := url.Parse(origin)
	return err == nil && (parsed.Scheme == "http" || parsed.Scheme == "https") &&
		parsed.User == nil && parsed.Path == "" && parsed.RawQuery == "" && parsed.Fragment == "" && strings.EqualFold(parsed.Host, r.Host)
}

func Handle(action func(http.ResponseWriter, *http.Request) error) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if err := action(w, r); err != nil {
			WriteError(w, err)
		}
	}
}

func WriteError(w http.ResponseWriter, err error) {
	var reply apperr.Error
	var tooLarge *http.MaxBytesError
	var status int
	switch {
	case errors.As(err, &reply):
		status = statusCodes[reply.Kind]
		if status == 0 {
			status = http.StatusInternalServerError
		}
	case errors.As(err, &tooLarge):
		status = http.StatusRequestEntityTooLarge
		reply.Message = "Размер запроса превышает лимит."
	default:
		log.Printf("backend: %T", err)
		status = http.StatusInternalServerError
		reply.Message = "Не удалось выполнить запрос. Попробуйте ещё раз."
	}
	WriteJSON(w, status, map[string]string{"detail": reply.Message})
}

var statusCodes = map[apperr.Kind]int{
	apperr.Unauthorized:  http.StatusUnauthorized,
	apperr.Forbidden:     http.StatusForbidden,
	apperr.NotFound:      http.StatusNotFound,
	apperr.Conflict:      http.StatusConflict,
	apperr.TooLarge:      http.StatusRequestEntityTooLarge,
	apperr.InvalidInput:  http.StatusUnprocessableEntity,
	apperr.LimitExceeded: http.StatusTooManyRequests,
}

func WriteJSON(w http.ResponseWriter, code int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(value)
}

func ReadJSON(w http.ResponseWriter, r *http.Request, value any) error {
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(value); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			return err
		}
		return apperr.New(apperr.InvalidInput, "Проверьте поля запроса.")
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return apperr.New(apperr.InvalidInput, "Проверьте поля запроса.")
	}
	return nil
}
