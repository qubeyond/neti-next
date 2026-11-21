package storage

import (
	"bytes"
	"encoding/json"
	"time"

	"github.com/qubeyond/neti-next/backend/internal/account"
	"github.com/qubeyond/neti-next/backend/internal/apperr"
	"github.com/qubeyond/neti-next/backend/internal/chat"
	"github.com/qubeyond/neti-next/backend/internal/identity"
	bolt "go.etcd.io/bbolt"
)

const guestLifetime = time.Hour

func (s *Store) Register(profile account.Profile, hash []byte, token string) error {
	return s.db.Update(func(tx *bolt.Tx) error {
		index := tx.Bucket(emails)
		if index.Get([]byte(profile.Email)) != nil {
			return apperr.New(apperr.Conflict, "Аккаунт с такой почтой уже есть.")
		}
		bucket, err := tx.Bucket(owners).CreateBucket([]byte(profile.ID))
		if err != nil {
			return err
		}
		state := workspace{Profile: profile, Password: hash, Chats: make(map[string]*chat.StoredChat)}
		if err := putJSON(bucket, stateKey, state); err != nil {
			return err
		}
		if err := index.Put([]byte(profile.Email), []byte(profile.ID)); err != nil {
			return err
		}
		return addSession(tx, token, profile.ID)
	})
}

func (s *Store) FindCredentials(email string) (account.Credentials, error) {
	var snapshot workspace
	err := s.db.View(func(tx *bolt.Tx) error {
		owner := tx.Bucket(emails).Get([]byte(email))
		if owner == nil {
			return account.ErrInvalidCredentials
		}
		var err error
		snapshot, err = readWorkspace(tx.Bucket(owners).Bucket(owner))
		return err
	})
	return account.Credentials{Profile: snapshot.Profile, PasswordHash: snapshot.Password}, err
}

func (s *Store) StartSession(owner string, expectedHash []byte, token string) (account.Profile, error) {
	var profile account.Profile
	err := s.db.Update(func(tx *bolt.Tx) error {
		current, err := readWorkspace(tx.Bucket(owners).Bucket([]byte(owner)))
		if err != nil || !bytes.Equal(current.Password, expectedHash) {
			return account.ErrInvalidCredentials
		}
		profile = current.Profile
		return addSession(tx, token, owner)
	})
	return profile, err
}

func (s *Store) EndSession(token string) (string, error) {
	key := tokenKey(token)
	err := s.db.Update(func(tx *bolt.Tx) error { return tx.Bucket(sessions).Delete([]byte(key)) })
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
	var record session
	err := s.db.View(func(tx *bolt.Tx) error {
		raw := tx.Bucket(sessions).Get([]byte(key))
		if raw == nil || json.Unmarshal(raw, &record) != nil || time.Now().After(record.Expires) ||
			tx.Bucket(owners).Bucket([]byte(record.Owner)) == nil {
			return identity.ErrSessionEnded
		}
		return nil
	})
	return identity.Actor{Owner: record.Owner, Token: key}, err
}

func (s *Store) GuestAlive(who identity.Actor) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	entry := s.guests[who.Token]
	return entry != nil && time.Now().Before(entry.expires)
}

func (s *Store) GetCredentials(who identity.Actor) (account.Credentials, error) {
	var result account.Credentials
	err := s.db.View(func(tx *bolt.Tx) error {
		state, err := readWorkspace(tx.Bucket(owners).Bucket([]byte(who.Owner)))
		if err != nil {
			return err
		}
		result = account.Credentials{Profile: state.Profile, PasswordHash: state.Password}
		return nil
	})
	return result, err
}

func (s *Store) ChangeProfile(who identity.Actor, change func(*account.Profile, []byte) ([]byte, error)) (account.Profile, error) {
	var profile account.Profile
	err := s.db.Update(func(tx *bolt.Tx) error {
		bucket := tx.Bucket(owners).Bucket([]byte(who.Owner))
		state, err := readWorkspace(bucket)
		if err != nil {
			return err
		}
		newHash, err := change(&state.Profile, state.Password)
		if err != nil {
			return err
		}
		if newHash != nil {
			state.Password = newHash
			if err := removeSessions(tx.Bucket(sessions), who.Owner, who.Token); err != nil {
				return err
			}
		}
		profile = state.Profile
		return putJSON(bucket, stateKey, state)
	})
	return profile, err
}

func (s *Store) DeleteProfile(who identity.Actor) error {
	return s.db.Update(func(tx *bolt.Tx) error {
		workspaces := tx.Bucket(owners)
		state, err := readWorkspace(workspaces.Bucket([]byte(who.Owner)))
		if err != nil {
			return err
		}
		if err := tx.Bucket(emails).Delete([]byte(state.Profile.Email)); err != nil {
			return err
		}
		if err := removeSessions(tx.Bucket(sessions), who.Owner, ""); err != nil {
			return err
		}
		return workspaces.DeleteBucket([]byte(who.Owner))
	})
}

func addSession(tx *bolt.Tx, token, owner string) error {
	bucket := tx.Bucket(sessions)
	if err := removeSessions(bucket, "", ""); err != nil {
		return err
	}
	return putJSON(bucket, []byte(tokenKey(token)), session{Owner: owner, Expires: time.Now().UTC().Add(account.SessionLifetime)})
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
