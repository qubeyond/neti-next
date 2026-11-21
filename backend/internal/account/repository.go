package account

import (
	"errors"
	"time"

	"github.com/qubeyond/neti-next/backend/internal/identity"
)

const SessionLifetime = 30 * 24 * time.Hour

var ErrInvalidCredentials = errors.New("invalid credentials")

type Profile struct {
	ID           string `json:"id"`
	Email        string `json:"email"`
	Name         string `json:"name"`
	Theme        string `json:"theme"`
	Instructions string `json:"instructions"`
}

type Credentials struct {
	Profile      Profile
	PasswordHash []byte
}

type Repository interface {
	Register(profile Profile, hash []byte, token string) error
	FindCredentials(email string) (Credentials, error)
	StartSession(owner string, expectedHash []byte, token string) (Profile, error)
	EndSession(token string) (string, error)
	Authenticate(token string, guest bool) (identity.Actor, error)
	NewGuest() (string, error)
	EndGuest(who identity.Actor)
	GuestAlive(who identity.Actor) bool
	GetCredentials(who identity.Actor) (Credentials, error)

	// ChangeProfile saves the profile and revokes other sessions atomically if the hash changes.
	ChangeProfile(who identity.Actor, change func(*Profile, []byte) ([]byte, error)) (Profile, error)
	DeleteProfile(who identity.Actor) error
}
