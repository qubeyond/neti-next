package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"errors"
	"net/http"
	"net/mail"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/lib/pq"
	"golang.org/x/crypto/bcrypt"
)

type credentials struct {
	Name     string `json:"name"`
	Email    string `json:"email"`
	Password string `json:"password"`
}

func goodPassword(password string) bool {
	return len(password) <= 72 && utf8.RuneCountInString(password) >= 8
}

func goodName(name string) bool {
	return name != "" && utf8.RuneCountInString(name) <= 60
}

func (s *server) register(w http.ResponseWriter, r *http.Request) error {
	var input credentials
	if err := readJSON(w, r, &input); err != nil {
		return err
	}
	input.Email = strings.ToLower(strings.TrimSpace(input.Email))
	input.Name = strings.TrimSpace(input.Name)
	address, err := mail.ParseAddress(input.Email)
	if err != nil || address.Address != input.Email || len(input.Email) > 254 {
		return problem(422, "Введите корректный адрес почты.")
	}
	if !goodName(input.Name) {
		return problem(422, "Введите имя длиной до 60 символов.")
	}
	if !goodPassword(input.Password) {
		return problem(422, "Пароль должен содержать от 8 символов и занимать до 72 байт.")
	}
	hashed, err := bcrypt.GenerateFromPassword([]byte(input.Password), bcrypt.DefaultCost)
	if err != nil {
		return err
	}
	account := profile{ID: randomID(), Email: input.Email, Name: input.Name, Theme: "light"}
	token := randomID()
	err = s.store.update(func(tx *sql.Tx) error {
		state := workspace{Profile: account, Password: hashed, Chats: make(map[string]*storedChat)}
		data, err := json.Marshal(state)
		if err != nil {
			return err
		}
		_, err = tx.Exec("INSERT INTO backend.workspaces (id, email, state) VALUES ($1, $2, $3)", account.ID, account.Email, data)
		var pgError *pq.Error
		if errors.As(err, &pgError) && pgError.Code == "23505" && pgError.Constraint == "workspaces_email_key" {
			return problem(409, "Аккаунт с такой почтой уже есть.")
		}
		if err != nil {
			return err
		}
		return addSession(tx, token, account.ID)
	})
	if err != nil {
		return err
	}
	s.cookie(w, r, token, int(sessionLifetime.Seconds()))
	writeJSON(w, 201, account)
	return nil
}

func (s *server) login(w http.ResponseWriter, r *http.Request) error {
	var input credentials
	if err := readJSON(w, r, &input); err != nil {
		return err
	}
	invalid := problem(401, "Неверная почта или пароль.")
	if !goodPassword(input.Password) {
		return invalid
	}
	email := strings.ToLower(strings.TrimSpace(input.Email))
	var snapshot workspace
	if err := s.store.transaction(5*time.Second, false, func(tx *sql.Tx) error {
		var err error
		snapshot, err = readWorkspace(tx.QueryRow("SELECT state FROM backend.workspaces WHERE email = $1", email))
		if errors.Is(err, errGone) {
			return invalid
		}
		return err
	}); err != nil {
		return err
	}
	if bcrypt.CompareHashAndPassword(snapshot.Password, []byte(input.Password)) != nil {
		return invalid
	}
	token := randomID()
	var account profile
	err := s.store.update(func(tx *sql.Tx) error {
		current, err := readWorkspace(tx.QueryRow("SELECT state FROM backend.workspaces WHERE id = $1 FOR UPDATE", snapshot.Profile.ID))
		if errors.Is(err, errGone) {
			return invalid
		}
		if err != nil {
			return err
		}
		if !bytes.Equal(current.Password, snapshot.Password) {
			return invalid
		}
		account = current.Profile
		return addSession(tx, token, account.ID)
	})
	if err != nil {
		return err
	}
	s.cookie(w, r, token, int(sessionLifetime.Seconds()))
	writeJSON(w, 200, account)
	return nil
}

func addSession(tx *sql.Tx, token, owner string) error {
	if _, err := tx.Exec("DELETE FROM backend.sessions WHERE expires_at <= now()"); err != nil {
		return err
	}
	_, err := tx.Exec("INSERT INTO backend.sessions (token, owner, expires_at) VALUES ($1, $2, $3)", tokenKey(token), owner, now().Add(sessionLifetime))
	return err
}

func (s *server) logout(w http.ResponseWriter, r *http.Request) error {
	if cookie, err := r.Cookie(sessionCookie); err == nil {
		key := tokenKey(cookie.Value)
		if err := s.store.update(func(tx *sql.Tx) error {
			_, err := tx.Exec("DELETE FROM backend.sessions WHERE token = $1", key)
			return err
		}); err != nil {
			return err
		}
		s.events.disconnect(func(subscriber identity) bool { return subscriber.token == key })
	}
	s.cookie(w, r, "", -1)
	w.WriteHeader(204)
	return nil
}

func (s *server) createGuest(w http.ResponseWriter, _ *http.Request) error {
	token, err := s.store.newGuest()
	if err == nil {
		writeJSON(w, 201, map[string]string{"token": token})
	}
	return err
}

func (s *server) deleteGuest(w http.ResponseWriter, r *http.Request) error {
	who, err := s.actor(r)
	if err != nil {
		return err
	}
	if !who.guest {
		return problem(403, "Требуется гостевая сессия.")
	}
	s.store.endGuest(who)
	s.turns.cancel(who.owner, "")
	s.events.disconnect(func(subscriber identity) bool { return subscriber == who })
	w.WriteHeader(204)
	return nil
}

func (s *server) getProfile(w http.ResponseWriter, r *http.Request) error {
	who, err := s.account(r)
	if err != nil {
		return err
	}
	var account profile
	err = s.store.access(who, false, func(state *workspace, _ blobs) error {
		account = state.Profile
		return nil
	})
	if err == nil {
		writeJSON(w, 200, account)
	}
	return err
}

func (s *server) updateProfile(w http.ResponseWriter, r *http.Request) error {
	who, err := s.account(r)
	if err != nil {
		return err
	}
	var input struct {
		Name            *string `json:"name"`
		Theme           *string `json:"theme"`
		Instructions    *string `json:"instructions"`
		CurrentPassword string  `json:"currentPassword"`
		Password        string  `json:"password"`
	}
	if err := readJSON(w, r, &input); err != nil {
		return err
	}
	if input.Name != nil {
		*input.Name = strings.TrimSpace(*input.Name)
		if !goodName(*input.Name) {
			return problem(422, "Введите имя длиной до 60 символов.")
		}
	}
	if input.Theme != nil && *input.Theme != "light" && *input.Theme != "dark" {
		return problem(422, "Выберите светлую или тёмную тему.")
	}
	if input.Instructions != nil && len([]rune(*input.Instructions)) > 2000 {
		return problem(422, "Инструкция должна быть не длиннее 2000 символов.")
	}
	var oldHash, newHash []byte
	if input.Password != "" {
		if !goodPassword(input.Password) {
			return problem(422, "Пароль должен содержать от 8 символов и занимать до 72 байт.")
		}
		if err := s.store.access(who, false, func(state *workspace, _ blobs) error {
			oldHash = state.Password
			return nil
		}); err != nil {
			return err
		}
		if bcrypt.CompareHashAndPassword(oldHash, []byte(input.CurrentPassword)) != nil {
			return problem(403, "Текущий пароль введён неверно.")
		}
		newHash, err = bcrypt.GenerateFromPassword([]byte(input.Password), bcrypt.DefaultCost)
		if err != nil {
			return err
		}
	}
	var account profile
	err = s.store.update(func(tx *sql.Tx) error {
		state, err := readWorkspace(tx.QueryRow("SELECT state FROM backend.workspaces WHERE id = $1 FOR UPDATE", who.owner))
		if err != nil {
			return err
		}
		if input.Name != nil {
			state.Profile.Name = *input.Name
		}
		if input.Theme != nil {
			state.Profile.Theme = *input.Theme
		}
		if input.Instructions != nil {
			state.Profile.Instructions = *input.Instructions
		}
		if newHash != nil {
			if !bytes.Equal(oldHash, state.Password) {
				return problem(409, "Пароль уже изменился. Попробуйте ещё раз.")
			}
			state.Password = newHash
			if _, err := tx.Exec("DELETE FROM backend.sessions WHERE owner = $1 AND token <> $2", who.owner, who.token); err != nil {
				return err
			}
		}
		account = state.Profile
		return saveWorkspace(tx, who.owner, state)
	})
	if err != nil {
		return err
	}
	if newHash != nil {
		s.events.disconnect(func(subscriber identity) bool { return subscriber.owner == who.owner && subscriber.token != who.token })
	}
	writeJSON(w, 200, account)
	return nil
}

func (s *server) deleteProfile(w http.ResponseWriter, r *http.Request) error {
	who, err := s.account(r)
	if err != nil {
		return err
	}
	err = s.store.update(func(tx *sql.Tx) error {
		_, err := tx.Exec("DELETE FROM backend.workspaces WHERE id = $1", who.owner)
		return err
	})
	if err != nil {
		return err
	}
	s.turns.cancel(who.owner, "")
	s.events.disconnect(func(subscriber identity) bool { return subscriber.owner == who.owner })
	s.cookie(w, r, "", -1)
	w.WriteHeader(204)
	return nil
}
