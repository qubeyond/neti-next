package main

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"time"

	bolt "go.etcd.io/bbolt"
)

const (
	guestLifetime   = time.Hour
	sessionLifetime = 30 * 24 * time.Hour
	maxChats        = 60
	maxMessages     = 100
	maxFilesPerChat = 60
	maxFileBytes    = 16 << 20
	maxStoredBytes  = 64 << 20
)

var (
	owners   = []byte("workspaces")
	emails   = []byte("emails")
	sessions = []byte("sessions")
	stateKey = []byte("state")
	errGone  = errors.New("Сессия завершена.")
)

type profile struct {
	ID           string `json:"id"`
	Email        string `json:"email"`
	Name         string `json:"name"`
	Theme        string `json:"theme"`
	Instructions string `json:"instructions"`
}

type attachment struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	MIME string `json:"mime"`
	Size int    `json:"size"`
}

type citation struct {
	URL   string `json:"url"`
	Quote string `json:"quote"`
}

type artifact struct {
	Kind  string `json:"kind"`
	Title string `json:"title"`
	URL   string `json:"url,omitempty"`
	Text  string `json:"text,omitempty"`
}

type activity struct {
	Kind    string `json:"kind"`
	Label   string `json:"label"`
	Elapsed int64  `json:"elapsedMs"`
}

type message struct {
	ID          string       `json:"id"`
	Role        string       `json:"role"`
	Text        string       `json:"text"`
	Quote       string       `json:"quote,omitempty"`
	Attachments []attachment `json:"attachments,omitempty"`
	State       string       `json:"state,omitempty"`
	Status      string       `json:"status,omitempty"`
	Error       string       `json:"error,omitempty"`
	Citations   []citation   `json:"citations,omitempty"`
	Artifacts   []artifact   `json:"artifacts,omitempty"`
	Activity    []activity   `json:"activity,omitempty"`
	CreatedAt   time.Time    `json:"createdAt"`
	Duration    int64        `json:"durationMs,omitempty"`
}

type chatSummary struct {
	ID        string    `json:"id"`
	Title     string    `json:"title"`
	UpdatedAt time.Time `json:"updatedAt"`
}

type chat struct {
	chatSummary
	Messages []message `json:"messages"`
}

type storedChat struct {
	chat
	Files map[string]attachment `json:"files"`
}

type workspace struct {
	Profile  profile                `json:"profile"`
	Password []byte                 `json:"password"`
	Chats    map[string]*storedChat `json:"chats"`
	Bytes    int                    `json:"bytes"`
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

type identity struct {
	owner string
	token string
	guest bool
}

type store struct {
	db     *bolt.DB
	mu     sync.Mutex
	guests map[string]*guestSession
}

func newStore(path string) (*store, error) {
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
	return &store{db: db, guests: make(map[string]*guestSession)}, nil
}

func randomID() string {
	value := make([]byte, 24)
	rand.Read(value)
	return hex.EncodeToString(value)
}

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
		return state, errGone
	}
	err := json.Unmarshal(bucket.Get(stateKey), &state)
	return state, err
}

type blobs struct {
	bucket *bolt.Bucket
	files  map[string][]byte
}

func (storage blobs) get(id string) []byte {
	if storage.bucket != nil {
		return storage.bucket.Get([]byte(id))
	}
	return storage.files[id]
}

func (storage blobs) put(id string, data []byte) error {
	if storage.bucket != nil {
		return storage.bucket.Put([]byte(id), data)
	}
	storage.files[id] = data
	return nil
}

func (storage blobs) delete(id string) error {
	if storage.bucket != nil {
		return storage.bucket.Delete([]byte(id))
	}
	delete(storage.files, id)
	return nil
}

func (s *store) access(who identity, write bool, operation func(*workspace, blobs) error) error {
	if who.guest {
		s.mu.Lock()
		defer s.mu.Unlock()
		guest := s.guests[who.token]
		if guest == nil || time.Now().After(guest.expires) {
			return errGone
		}
		guest.expires = time.Now().Add(guestLifetime)
		return operation(&guest.state, blobs{files: guest.files})
	}
	run := s.db.View
	if write {
		run = s.db.Update
	}
	return run(func(tx *bolt.Tx) error {
		bucket := tx.Bucket(owners).Bucket([]byte(who.owner))
		state, err := readWorkspace(bucket)
		if err != nil {
			return err
		}
		if err := operation(&state, blobs{bucket: bucket}); err != nil {
			return err
		}
		if write {
			return putJSON(bucket, stateKey, state)
		}
		return nil
	})
}

func (s *store) authenticate(value string, guest bool) (identity, error) {
	if len(value) != 48 {
		return identity{}, errGone
	}
	key := tokenKey(value)
	if guest {
		who := identity{owner: key, token: key, guest: true}
		return who, s.access(who, false, func(*workspace, blobs) error { return nil })
	}
	var account session
	err := s.db.View(func(tx *bolt.Tx) error {
		raw := tx.Bucket(sessions).Get([]byte(key))
		if raw == nil || json.Unmarshal(raw, &account) != nil || time.Now().After(account.Expires) ||
			tx.Bucket(owners).Bucket([]byte(account.Owner)) == nil {
			return errGone
		}
		return nil
	})
	return identity{owner: account.Owner, token: key}, err
}

func (s *store) newGuest() (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.purgeGuests(time.Now())
	if len(s.guests) >= 128 {
		return "", problem(429, "Слишком много гостевых сессий. Попробуйте позже.")
	}
	value := randomID()
	s.guests[tokenKey(value)] = &guestSession{
		state: workspace{Chats: make(map[string]*storedChat)},
		files: make(map[string][]byte), expires: time.Now().Add(guestLifetime),
	}
	return value, nil
}

func (s *store) purgeGuests(now time.Time) {
	for key, guest := range s.guests {
		if now.After(guest.expires) {
			delete(s.guests, key)
		}
	}
}

func (s *store) expireGuests() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.purgeGuests(time.Now())
}

func (s *store) endGuest(who identity) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.guests, who.token)
}
