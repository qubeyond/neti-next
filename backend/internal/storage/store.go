package storage

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/qubeyond/neti-next/backend/internal/account"
	"github.com/qubeyond/neti-next/backend/internal/chat"
	"github.com/qubeyond/neti-next/backend/internal/identity"
	bolt "go.etcd.io/bbolt"
)

var (
	owners   = []byte("workspaces")
	emails   = []byte("emails")
	sessions = []byte("sessions")
	stateKey = []byte("state")
)

type workspace struct {
	Profile  account.Profile             `json:"profile"`
	Password []byte                      `json:"password"`
	Chats    map[string]*chat.StoredChat `json:"chats"`
	Bytes    int                         `json:"bytes"`
}

type guestSession struct {
	state   workspace
	files   map[string][]byte
	expires time.Time
}

type session struct {
	Owner   string    `json:"owner"`
	Expires time.Time `json:"expires"`
}

type Store struct {
	db     *bolt.DB
	mu     sync.Mutex
	guests map[string]*guestSession
}

func Open(path string) (*Store, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return nil, err
	}
	db, err := bolt.Open(path, 0600, &bolt.Options{Timeout: time.Second})
	if err != nil {
		return nil, err
	}
	err = db.Update(func(tx *bolt.Tx) error {
		for _, key := range [][]byte{owners, emails, sessions} {
			if _, err := tx.CreateBucketIfNotExists(key); err != nil {
				return err
			}
		}
		return tx.Bucket(owners).ForEachBucket(func(key []byte) error {
			bucket := tx.Bucket(owners).Bucket(key)
			state, err := readWorkspace(bucket)
			if err != nil {
				return err
			}
			changed := false
			for _, chat := range state.Chats {
				for index := range chat.Messages {
					item := &chat.Messages[index]
					if item.State == "working" {
						item.State = "failed"
						item.Error = "Сервис перезапустился. Повторите запрос."
						changed = true
					}
				}
			}
			if changed {
				return putJSON(bucket, stateKey, state)
			}
			return nil
		})
	})
	if err != nil {
		db.Close()
		return nil, err
	}
	return &Store{db: db, guests: make(map[string]*guestSession)}, nil
}

func (s *Store) Close() error { return s.db.Close() }

func tokenKey(value string) string {
	digest := sha256.Sum256([]byte(value))
	return hex.EncodeToString(digest[:])
}

func putJSON(bucket *bolt.Bucket, key []byte, value any) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	return bucket.Put(key, data)
}

func readWorkspace(bucket *bolt.Bucket) (workspace, error) {
	var state workspace
	if bucket == nil {
		return state, identity.ErrSessionEnded
	}
	err := json.Unmarshal(bucket.Get(stateKey), &state)
	return state, err
}
