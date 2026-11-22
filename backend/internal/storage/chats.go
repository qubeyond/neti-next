package storage

import (
	"database/sql"
	"errors"
	"time"

	"github.com/qubeyond/neti-next/backend/internal/apperr"
	"github.com/qubeyond/neti-next/backend/internal/chat"
	"github.com/qubeyond/neti-next/backend/internal/identity"
)

type blobs struct {
	tx     *sql.Tx
	owner  string
	guest  *guestSession
	guests map[string]*guestSession
}

func (storage blobs) Get(id string) ([]byte, error) {
	if storage.tx == nil {
		return storage.guest.files[id], nil
	}
	var data []byte
	err := storage.tx.QueryRow("SELECT contents FROM backend.attachments WHERE owner = $1 AND id = $2", storage.owner, id).Scan(&data)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, apperr.New(apperr.NotFound, "Вложение не найдено.")
	}
	return data, err
}

func (storage blobs) Put(id string, data []byte) error {
	if storage.tx != nil {
		_, err := storage.tx.Exec("INSERT INTO backend.attachments (owner, id, contents) VALUES ($1, $2, $3)", storage.owner, id, data)
		return err
	}
	total := 0
	for _, entry := range storage.guests {
		total += entry.state.Bytes
	}
	if storage.guest.state.Bytes+len(data) > 32<<20 || total+len(data) > 256<<20 {
		return apperr.New(apperr.TooLarge, "Достигнут лимит гостевого хранилища.")
	}
	storage.guest.files[id] = data
	return nil
}

func (storage blobs) Delete(id string) error {
	if storage.tx != nil {
		_, err := storage.tx.Exec("DELETE FROM backend.attachments WHERE owner = $1 AND id = $2", storage.owner, id)
		return err
	}
	delete(storage.guest.files, id)
	return nil
}

func (s *Store) ViewChats(who identity.Actor, view func(*chat.State, chat.Blobs) error) error {
	return s.accessChats(who, false, view)
}

func (s *Store) UpdateChats(who identity.Actor, update func(*chat.State, chat.Blobs) error) error {
	return s.accessChats(who, true, update)
}

func chatState(state *workspace) chat.State {
	return chat.State{
		Chats: state.Chats, Bytes: state.Bytes,
		DisplayName: state.Profile.Name, Instructions: state.Profile.Instructions,
	}
}

func (s *Store) accessChats(who identity.Actor, write bool, operation func(*chat.State, chat.Blobs) error) error {
	if who.Guest {
		s.mu.Lock()
		defer s.mu.Unlock()
		guest, err := s.touchGuest(who)
		if err != nil {
			return err
		}
		state := chatState(&guest.state)
		if err := operation(&state, blobs{guest: guest, guests: s.guests}); err != nil {
			return err
		}
		if write {
			guest.state.Chats = state.Chats
			guest.state.Bytes = state.Bytes
		}
		return nil
	}
	return s.transaction(10*time.Second, write, func(tx *sql.Tx) error {
		query := "SELECT state FROM backend.workspaces WHERE id = $1"
		if write {
			query += " FOR UPDATE"
		}
		state, err := readWorkspace(tx.QueryRow(query, who.Owner))
		if err != nil {
			return err
		}
		chats := chatState(&state)
		if err := operation(&chats, blobs{tx: tx, owner: who.Owner}); err != nil {
			return err
		}
		if write {
			state.Chats, state.Bytes = chats.Chats, chats.Bytes
			return saveWorkspace(tx, who.Owner, state)
		}
		return nil
	})
}

func (s *Store) touchGuest(who identity.Actor) (*guestSession, error) {
	guest := s.guests[who.Token]
	if guest == nil || time.Now().After(guest.expires) {
		return nil, identity.ErrSessionEnded
	}
	guest.expires = time.Now().Add(guestLifetime)
	return guest, nil
}
