package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/mail"
	"strings"
	"time"
	"unicode/utf8"

	bolt "go.etcd.io/bbolt"
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
	err = s.store.db.Update(func(tx *bolt.Tx) error {
		index := tx.Bucket(emails)
		if index.Get([]byte(input.Email)) != nil {
			return problem(409, "Аккаунт с такой почтой уже есть.")
		}
		bucket, err := tx.Bucket(owners).CreateBucket([]byte(account.ID))
		if err != nil {
			return err
		}
		state := workspace{Profile: account, Password: hashed, Chats: make(map[string]*storedChat)}
		if err := putJSON(bucket, stateKey, state); err != nil {
			return err
		}
		if err := index.Put([]byte(account.Email), []byte(account.ID)); err != nil {
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
	if err := s.store.db.View(func(tx *bolt.Tx) error {
		owner := tx.Bucket(emails).Get([]byte(email))
		if owner == nil {
			return invalid
		}
		var err error
		snapshot, err = readWorkspace(tx.Bucket(owners).Bucket(owner))
		return err
	}); err != nil {
		return err
	}
	if bcrypt.CompareHashAndPassword(snapshot.Password, []byte(input.Password)) != nil {
		return invalid
	}
	token := randomID()
	var account profile
	err := s.store.db.Update(func(tx *bolt.Tx) error {
		current, err := readWorkspace(tx.Bucket(owners).Bucket([]byte(snapshot.Profile.ID)))
		if err != nil || !bytes.Equal(current.Password, snapshot.Password) {
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

func addSession(tx *bolt.Tx, token, owner string) error {
	bucket := tx.Bucket(sessions)
	if err := removeSessions(bucket, "", ""); err != nil {
		return err
	}
	return putJSON(bucket, []byte(tokenKey(token)), session{Owner: owner, Expires: now().Add(sessionLifetime)})
}

func removeSessions(bucket *bolt.Bucket, owner, keep string) error {
	var remove [][]byte
	err := bucket.ForEach(func(key, value []byte) error {
		var item session
		if json.Unmarshal(value, &item) != nil || time.Now().After(item.Expires) ||
			(owner != "" && item.Owner == owner && string(key) != keep) {
			remove = append(remove, bytes.Clone(key))
		}
		return nil
	})
	if err != nil {
		return err
	}
	for _, key := range remove {
		if err := bucket.Delete(key); err != nil {
			return err
		}
	}
	return nil
}

func (s *server) logout(w http.ResponseWriter, r *http.Request) error {
	if cookie, err := r.Cookie(sessionCookie); err == nil {
		key := tokenKey(cookie.Value)
		if err := s.store.db.Update(func(tx *bolt.Tx) error { return tx.Bucket(sessions).Delete([]byte(key)) }); err != nil {
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
	err = s.store.db.Update(func(tx *bolt.Tx) error {
		bucket := tx.Bucket(owners).Bucket([]byte(who.owner))
		state, err := readWorkspace(bucket)
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
			if err := removeSessions(tx.Bucket(sessions), who.owner, who.token); err != nil {
				return err
			}
		}
		account = state.Profile
		return putJSON(bucket, stateKey, state)
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
	err = s.store.db.Update(func(tx *bolt.Tx) error {
		workspaces := tx.Bucket(owners)
		state, err := readWorkspace(workspaces.Bucket([]byte(who.owner)))
		if err != nil {
			return err
		}
		if err := tx.Bucket(emails).Delete([]byte(state.Profile.Email)); err != nil {
			return err
		}
		if err := removeSessions(tx.Bucket(sessions), who.owner, ""); err != nil {
			return err
		}
		return workspaces.DeleteBucket([]byte(who.owner))
	})
	if err != nil {
		return err
	}
	s.events.disconnect(func(subscriber identity) bool { return subscriber.owner == who.owner })
	s.cookie(w, r, "", -1)
	w.WriteHeader(204)
	return nil
}
