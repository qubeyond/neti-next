package httpapi

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/qubeyond/neti-next/backend/internal/apperr"
)

func Protect(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := NormalizeRequestID(r.Header.Get("X-Request-ID"))
		r = r.WithContext(WithRequestID(r.Context(), id))
		w.Header().Set("X-Request-ID", id)
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
	id := w.Header().Get("X-Request-ID")
	reply := SafeFailure(err, id)
	if reply.Code == http.StatusTooManyRequests {
		delay := 1
		var limited RetryLater
		if errors.As(err, &limited) {
			delay = limited.Seconds
		}
		w.Header().Set("Retry-After", strconv.Itoa(delay))
	}
	w.Header().Set("Content-Type", "application/problem+json")
	w.WriteHeader(reply.Code)
	json.NewEncoder(w).Encode(Describe(reply.Code, reply.Detail, id))
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
