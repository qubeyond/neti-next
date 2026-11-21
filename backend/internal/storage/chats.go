package storage

import (
	"time"

	"github.com/qubeyond/neti-next/backend/internal/apperr"
	"github.com/qubeyond/neti-next/backend/internal/chat"
	"github.com/qubeyond/neti-next/backend/internal/identity"
	bolt "go.etcd.io/bbolt"
)

type blobs struct {
	bucket *bolt.Bucket
	guest  *guestSession
	guests map[string]*guestSession
}

func (storage blobs) Get(id string) []byte {
	if storage.bucket != nil {
		return storage.bucket.Get([]byte(id))
	}
	return storage.guest.files[id]
}

func (storage blobs) Put(id string, data []byte) error {
	if storage.bucket != nil {
		return storage.bucket.Put([]byte(id), data)
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
	if storage.bucket != nil {
		return storage.bucket.Delete([]byte(id))
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
	run := s.db.View
	if write {
		run = s.db.Update
	}
	return run(func(tx *bolt.Tx) error {
		bucket := tx.Bucket(owners).Bucket([]byte(who.Owner))
		state, err := readWorkspace(bucket)
		if err != nil {
			return err
		}
		chats := chatState(&state)
		if err := operation(&chats, blobs{bucket: bucket}); err != nil {
			return err
		}
		if write {
			state.Chats = chats.Chats
			state.Bytes = chats.Bytes
			return putJSON(bucket, stateKey, state)
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
