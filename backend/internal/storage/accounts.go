package storage

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"errors"
	"time"

	"github.com/lib/pq"

	"github.com/qubeyond/neti-next/backend/internal/account"
	"github.com/qubeyond/neti-next/backend/internal/apperr"
	"github.com/qubeyond/neti-next/backend/internal/chat"
	"github.com/qubeyond/neti-next/backend/internal/identity"
)

const guestLifetime = time.Hour

func (s *Store) Register(profile account.Profile, hash []byte, token string) error {
	return s.update(func(tx *sql.Tx) error {
		state := workspace{Profile: profile, Password: hash, Chats: make(map[string]*chat.StoredChat)}
		data, err := json.Marshal(state)
		if err != nil {
			return err
		}
		_, err = tx.Exec("INSERT INTO backend.workspaces (id, email, state) VALUES ($1, $2, $3)", profile.ID, profile.Email, data)
		var pgError *pq.Error
		if errors.As(err, &pgError) && pgError.Code == "23505" && pgError.Constraint == "workspaces_email_key" {
			return apperr.New(apperr.Conflict, "Аккаунт с такой почтой уже есть.")
		}
		if err != nil {
			return err
		}
		return addSession(tx, token, profile.ID)
	})
}

func (s *Store) FindCredentials(email string) (account.Credentials, error) {
	var snapshot workspace
	err := s.transaction(5*time.Second, false, func(tx *sql.Tx) error {
		var err error
		snapshot, err = readWorkspace(tx.QueryRow("SELECT state FROM backend.workspaces WHERE email = $1", email))
		if errors.Is(err, identity.ErrSessionEnded) {
			return account.ErrInvalidCredentials
		}
		return err
	})
	return account.Credentials{Profile: snapshot.Profile, PasswordHash: snapshot.Password}, err
}

func (s *Store) StartSession(owner string, expectedHash []byte, token string) (account.Profile, error) {
	var profile account.Profile
	err := s.update(func(tx *sql.Tx) error {
		current, err := readWorkspace(tx.QueryRow("SELECT state FROM backend.workspaces WHERE id = $1 FOR UPDATE", owner))
		if errors.Is(err, identity.ErrSessionEnded) {
			return account.ErrInvalidCredentials
		}
		if err != nil {
			return err
		}
		if !bytes.Equal(current.Password, expectedHash) {
			return account.ErrInvalidCredentials
		}
		profile = current.Profile
		return addSession(tx, token, owner)
	})
	return profile, err
}

func (s *Store) EndSession(token string) (string, error) {
	key := tokenKey(token)
	err := s.update(func(tx *sql.Tx) error {
		_, err := tx.Exec("DELETE FROM backend.sessions WHERE token = $1", key)
		return err
	})
	return key, err
}

func (s *Store) Authenticate(value string, guest bool) (identity.Actor, error) {
	if len(value) != 48 {
		return identity.Actor{}, identity.ErrSessionEnded
	}
	key := tokenKey(value)
	if guest {
		who := identity.Actor{Owner: key, Token: key, Guest: true}
		s.mu.Lock()
		defer s.mu.Unlock()
		_, err := s.touchGuest(who)
		return who, err
	}
	who := identity.Actor{Token: key}
	err := s.transaction(5*time.Second, false, func(tx *sql.Tx) error {
		return tx.QueryRow("SELECT owner FROM backend.sessions WHERE token = $1 AND expires_at > now()", key).Scan(&who.Owner)
	})
	if errors.Is(err, sql.ErrNoRows) {
		err = identity.ErrSessionEnded
	}
	return who, err
}

func (s *Store) GuestAlive(who identity.Actor) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	entry := s.guests[who.Token]
	return entry != nil && time.Now().Before(entry.expires)
}

func (s *Store) GetCredentials(who identity.Actor) (account.Credentials, error) {
	var result account.Credentials
	err := s.transaction(5*time.Second, false, func(tx *sql.Tx) error {
		state, err := readWorkspace(tx.QueryRow("SELECT state FROM backend.workspaces WHERE id = $1", who.Owner))
		if err == nil {
			result = account.Credentials{Profile: state.Profile, PasswordHash: state.Password}
		}
		return err
	})
	return result, err
}

func (s *Store) ChangeProfile(who identity.Actor, change func(*account.Profile, []byte) ([]byte, error)) (account.Profile, error) {
	var profile account.Profile
	err := s.update(func(tx *sql.Tx) error {
		state, err := readWorkspace(tx.QueryRow("SELECT state FROM backend.workspaces WHERE id = $1 FOR UPDATE", who.Owner))
		if err != nil {
			return err
		}
		newHash, err := change(&state.Profile, state.Password)
		if err != nil {
			return err
		}
		if newHash != nil {
			state.Password = newHash
			if _, err := tx.Exec("DELETE FROM backend.sessions WHERE owner = $1 AND token <> $2", who.Owner, who.Token); err != nil {
				return err
			}
		}
		profile = state.Profile
		return saveWorkspace(tx, who.Owner, state)
	})
	return profile, err
}

func (s *Store) DeleteProfile(who identity.Actor) error {
	return s.update(func(tx *sql.Tx) error {
		_, err := tx.Exec("DELETE FROM backend.workspaces WHERE id = $1", who.Owner)
		return err
	})
}

func addSession(tx *sql.Tx, token, owner string) error {
	if _, err := tx.Exec("DELETE FROM backend.sessions WHERE expires_at <= now()"); err != nil {
		return err
	}
	_, err := tx.Exec("INSERT INTO backend.sessions (token, owner, expires_at) VALUES ($1, $2, $3)", tokenKey(token), owner, time.Now().UTC().Add(account.SessionLifetime))
	return err
}

func (s *Store) NewGuest() (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.purgeGuests(time.Now())
	if len(s.guests) >= 128 {
		return "", apperr.New(apperr.LimitExceeded, "Слишком много гостевых сессий. Попробуйте позже.")
	}
	value := identity.NewID()
	s.guests[tokenKey(value)] = &guestSession{
		state: workspace{Chats: make(map[string]*chat.StoredChat)},
		files: make(map[string][]byte), expires: time.Now().Add(guestLifetime),
	}
	return value, nil
}

func (s *Store) purgeGuests(now time.Time) {
	for key, guest := range s.guests {
		if now.After(guest.expires) {
			delete(s.guests, key)
		}
	}
}

func (s *Store) ExpireGuests() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.purgeGuests(time.Now())
}

func (s *Store) EndGuest(who identity.Actor) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.guests, who.Token)
}
