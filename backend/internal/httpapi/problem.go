package httpapi

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"net/http"
	"strings"

	"github.com/qubeyond/neti-next/backend/internal/apperr"
)

type requestIDKey struct{}

type RetryLater struct {
	error
	Seconds int
}

func Retry(err error, seconds int) error { return RetryLater{err, seconds} }

func (e RetryLater) Unwrap() error { return e.error }

type Problem struct {
	Type      string `json:"type"`
	Title     string `json:"title"`
	Status    int    `json:"status"`
	Detail    string `json:"detail"`
	RequestID string `json:"request_id"`
}

func NormalizeRequestID(value string) string {
	if len(value) == 32 && value == strings.ToLower(value) {
		if _, err := hex.DecodeString(value); err == nil {
			return value
		}
	}

	var id [16]byte
	rand.Read(id[:])
	return hex.EncodeToString(id[:])
}

func Propagate(ctx context.Context, request *http.Request) {
	if id, ok := ctx.Value(requestIDKey{}).(string); ok {
		request.Header.Set("X-Request-ID", id)
	}
	if deadline, ok := ctx.Deadline(); ok {
		request.Header.Set("X-Deadline-Ms", fmt.Sprint(deadline.UnixMilli()))
	}
}

func Describe(code int, detail, id string) Problem {
	title := http.StatusText(code)
	return Problem{
		Type:  "urn:neti:problem:" + strings.ReplaceAll(strings.ToLower(title), " ", "-"),
		Title: title, Status: code, Detail: detail, RequestID: id,
	}
}

func SafeFailure(err error, id string) apperr.Failure {
	var reply apperr.Failure
	var domain apperr.Error
	var tooLarge *http.MaxBytesError
	switch {
	case errors.As(err, &reply):
	case errors.As(err, &domain):
		code := statusCodes[domain.Kind]
		if code == 0 {
			code = http.StatusInternalServerError
		}
		reply = apperr.Failure{Code: code, Detail: domain.Message}
	case errors.As(err, &tooLarge):
		reply = apperr.Failure{Code: 413, Detail: "Размер запроса превышает лимит."}
	case errors.Is(err, context.Canceled):
		reply = apperr.Failure{Code: 409, Detail: "Запрос остановлен."}
	case errors.Is(err, context.DeadlineExceeded):
		reply = apperr.Failure{Code: 504, Detail: "Бюджет времени исчерпан. Попробуйте ещё раз."}
	default:
		log.Printf("backend: request_id=%s error_type=%T", id, err)
		reply = apperr.Failure{Code: 500, Detail: "Не удалось выполнить запрос. Попробуйте ещё раз."}
	}
	return reply
}

func UpstreamFailure(code int, service string) error {
	switch code {
	case 429:
		return apperr.HTTP(code, service+" занят. Попробуйте ещё раз.")
	case 503:
		return apperr.HTTP(code, service+" временно недоступен.")
	case 504:
		return apperr.HTTP(code, service+" не успел ответить.")
	case 413:
		return apperr.HTTP(code, "Вложение превышает допустимый размер.")
	case 415:
		return apperr.HTTP(code, "Формат вложения не поддерживается.")
	default:
		return apperr.HTTP(502, service+" не смог обработать запрос.")
	}
}

func WithRequestID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, requestIDKey{}, id)
}

func RequestID(ctx context.Context) string {
	id, _ := ctx.Value(requestIDKey{}).(string)
	return id
}
