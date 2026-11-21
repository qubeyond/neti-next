package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"net/http"
	"strings"
)

type requestIDKey struct{}

type retryLater struct {
	error
	seconds int
}

func (e retryLater) Unwrap() error { return e.error }

type problemDetails struct {
	Type      string `json:"type"`
	Title     string `json:"title"`
	Status    int    `json:"status"`
	Detail    string `json:"detail"`
	RequestID string `json:"request_id"`
}

func requestID(value string) string {
	if len(value) == 32 && value == strings.ToLower(value) {
		if _, err := hex.DecodeString(value); err == nil {
			return value
		}
	}

	var id [16]byte
	rand.Read(id[:])
	return hex.EncodeToString(id[:])
}

func propagate(ctx context.Context, request *http.Request) {
	if id, ok := ctx.Value(requestIDKey{}).(string); ok {
		request.Header.Set("X-Request-ID", id)
	}
	if deadline, ok := ctx.Deadline(); ok {
		request.Header.Set("X-Deadline-Ms", fmt.Sprint(deadline.UnixMilli()))
	}
}

func problemResponse(code int, detail, requestID string) problemDetails {
	title := http.StatusText(code)
	return problemDetails{
		Type:  "urn:neti:problem:" + strings.ReplaceAll(strings.ToLower(title), " ", "-"),
		Title: title, Status: code, Detail: detail, RequestID: requestID,
	}
}

func safeFailure(err error, id string) failure {
	var reply failure
	var tooLarge *http.MaxBytesError
	switch {
	case errors.As(err, &reply):
	case errors.Is(err, errGone):
		reply = failure{401, "Сессия завершена. Войдите или начните новый гостевой сеанс."}
	case errors.As(err, &tooLarge):
		reply = failure{413, "Размер запроса превышает лимит."}
	case errors.Is(err, context.Canceled):
		reply = failure{409, "Запрос остановлен."}
	case errors.Is(err, context.DeadlineExceeded):
		reply = failure{504, "Бюджет времени исчерпан. Попробуйте ещё раз."}
	default:
		log.Printf("backend: request_id=%s error_type=%T", id, err)
		reply = failure{500, "Не удалось выполнить запрос. Попробуйте ещё раз."}
	}
	return reply
}

func upstreamFailure(code int, service string) error {
	switch code {
	case 429:
		return problem(code, service+" занят. Попробуйте ещё раз.")
	case 503:
		return problem(code, service+" временно недоступен.")
	case 504:
		return problem(code, service+" не успел ответить.")
	case 413:
		return problem(code, "Вложение превышает допустимый размер.")
	case 415:
		return problem(code, "Формат вложения не поддерживается.")
	default:
		return problem(502, service+" не смог обработать запрос.")
	}
}

type muxProblemWriter struct{ http.ResponseWriter }

func (w muxProblemWriter) WriteHeader(code int) {
	if code == 405 {
		writeError(w.ResponseWriter, problem(code, "Метод запроса не поддерживается."))
	} else {
		writeError(w.ResponseWriter, problem(code, "Маршрут не найден."))
	}
}

func (w muxProblemWriter) Write(data []byte) (int, error) { return len(data), nil }
