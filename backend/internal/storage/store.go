package storage

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sync"
	"time"

	_ "github.com/lib/pq"

	"github.com/qubeyond/neti-next/backend/internal/account"
	"github.com/qubeyond/neti-next/backend/internal/chat"
	"github.com/qubeyond/neti-next/backend/internal/identity"
)

type workspace struct {
	Profile  account.Profile             `json:"profile"`
	Password []byte                      `json:"password"`
	Chats    map[string]*chat.StoredChat `json:"chats"`
	Bytes    int                         `json:"bytes"`
}

func (state workspace) hasWorkingTurn() bool {
	for _, chat := range state.Chats {
		for _, message := range chat.Messages {
			if message.State == "working" {
				return true
			}
		}
	}
	return false
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
	db     *sql.DB
	mu     sync.Mutex
	guests map[string]*guestSession
}

func Open(dsn, legacyPath string) (*Store, error) {
	db, err := sql.Open("postgres", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(12)
	db.SetMaxIdleConns(4)
	db.SetConnMaxIdleTime(5 * time.Minute)
	s := NewGuestStore()
	s.db = db
	if err := s.initialize(legacyPath); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) transaction(limit time.Duration, write bool, operation func(*sql.Tx) error) error {
	ctx, cancel := context.WithTimeout(context.Background(), limit)
	defer cancel()
	options := sql.TxOptions{ReadOnly: !write}
	if !write {
		options.Isolation = sql.LevelRepeatableRead
	}
	tx, err := s.db.BeginTx(ctx, &options)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := operation(tx); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) update(operation func(*sql.Tx) error) error {
	return s.transaction(10*time.Second, true, operation)
}

func tokenKey(value string) string {
	digest := sha256.Sum256([]byte(value))
	return hex.EncodeToString(digest[:])
}

func readWorkspace(row *sql.Row) (workspace, error) {
	var state workspace
	var raw []byte
	if err := row.Scan(&raw); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return state, identity.ErrSessionEnded
		}
		return state, err
	}
	return state, json.Unmarshal(raw, &state)
}

func saveWorkspace(tx *sql.Tx, owner string, state workspace) error {
	data, err := json.Marshal(state)
	if err != nil {
		return err
	}
	_, err = tx.Exec("UPDATE backend.workspaces SET state = $1, has_working_turn = $2 WHERE id = $3", data, state.hasWorkingTurn(), owner)
	return err
}

func (s *Store) Close() error { return s.db.Close() }

func NewGuestStore() *Store {
	return &Store{guests: make(map[string]*guestSession)}
}
