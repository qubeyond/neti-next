package identity

import (
	"crypto/rand"
	"encoding/hex"

	"github.com/qubeyond/neti-next/backend/internal/apperr"
)

type Actor struct {
	Owner string
	Token string
	Guest bool
}

var ErrSessionEnded = apperr.New(apperr.Unauthorized, "Сессия завершена. Войдите или начните новый гостевой сеанс.")

func NewID() string {
	value := make([]byte, 24)
	rand.Read(value)
	return hex.EncodeToString(value)
}
