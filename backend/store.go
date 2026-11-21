package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sync"
	"time"

	_ "github.com/lib/pq"
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

var errGone = errors.New("Сессия завершена.")

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
	URL          string       `json:"url"`
	Quote        string       `json:"quote"`
	Ref          int          `json:"ref,omitempty"`
	AttachmentID string       `json:"attachment_id,omitempty"`
	Region       *imageRegion `json:"region,omitempty"`
}

type calendarEvent struct {
	Date     string `json:"date"`
	Title    string `json:"title"`
	Citation int    `json:"citation"`
}

type blockCell struct {
	Value string `json:"value"`
	Ref   int    `json:"ref"`
}

type chatBlock struct {
	Kind     string         `json:"kind"`
	Title    string         `json:"title"`
	Columns  []string       `json:"columns,omitempty"`
	Rows     [][]*blockCell `json:"rows,omitempty"`
	Operator string         `json:"operator,omitempty"`
	Operands []blockCell    `json:"operands,omitempty"`
	Result   string         `json:"result,omitempty"`
	Note     string         `json:"note,omitempty"`
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
	ID          string          `json:"id"`
	Role        string          `json:"role"`
	Text        string          `json:"text"`
	Quote       string          `json:"quote,omitempty"`
	Attachments []attachment    `json:"attachments,omitempty"`
	State       string          `json:"state,omitempty"`
	Status      string          `json:"status,omitempty"`
	Error       string          `json:"error,omitempty"`
	Problem     *problemDetails `json:"problem,omitempty"`
	Citations   []citation      `json:"citations,omitempty"`
	Blocks      []chatBlock     `json:"blocks,omitempty"`
	Suggestions []string        `json:"suggestions,omitempty"`
	Events      []calendarEvent `json:"events,omitempty"`
	Artifacts   []artifact      `json:"artifacts,omitempty"`
	Activity    []activity      `json:"activity,omitempty"`
	CreatedAt   time.Time       `json:"createdAt"`
	Duration    int64           `json:"durationMs,omitempty"`
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

type identity struct {
	owner string
	token string
	guest bool
}

type store struct {
	db     *sql.DB
	mu     sync.Mutex
	guests map[string]*guestSession
}

func newStore(dsn, legacyPath string) (*store, error) {
	db, err := sql.Open("postgres", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(12)
	db.SetMaxIdleConns(4)
	db.SetConnMaxIdleTime(5 * time.Minute)
	s := &store{db: db, guests: make(map[string]*guestSession)}
	if err := s.initialize(legacyPath); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *store) transaction(limit time.Duration, write bool, operation func(*sql.Tx) error) error {
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

func (s *store) update(operation func(*sql.Tx) error) error {
	return s.transaction(10*time.Second, true, operation)
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

func readWorkspace(row *sql.Row) (workspace, error) {
	var state workspace
	var raw []byte
	if err := row.Scan(&raw); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return state, errGone
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

type blobs struct {
	tx    *sql.Tx
	owner string
	files map[string][]byte
}

func (storage blobs) get(id string) ([]byte, error) {
	if storage.tx == nil {
		return storage.files[id], nil
	}
	var data []byte
	err := storage.tx.QueryRow("SELECT contents FROM backend.attachments WHERE owner = $1 AND id = $2", storage.owner, id).Scan(&data)
	return data, err
}

func (storage blobs) put(id string, data []byte) error {
	if storage.tx != nil {
		_, err := storage.tx.Exec("INSERT INTO backend.attachments (owner, id, contents) VALUES ($1, $2, $3)", storage.owner, id, data)
		return err
	}
	storage.files[id] = data
	return nil
}

func (storage blobs) delete(id string) error {
	if storage.tx != nil {
		_, err := storage.tx.Exec("DELETE FROM backend.attachments WHERE owner = $1 AND id = $2", storage.owner, id)
		return err
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
	return s.transaction(10*time.Second, write, func(tx *sql.Tx) error {
		query := "SELECT state FROM backend.workspaces WHERE id = $1"
		if write {
			query += " FOR UPDATE"
		}
		state, err := readWorkspace(tx.QueryRow(query, who.owner))
		if err != nil {
			return err
		}
		if err := operation(&state, blobs{tx: tx, owner: who.owner}); err != nil {
			return err
		}
		if write {
			return saveWorkspace(tx, who.owner, state)
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
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	var owner string
	err := s.db.QueryRowContext(ctx, "SELECT owner FROM backend.sessions WHERE token = $1 AND expires_at > now()", key).Scan(&owner)
	if errors.Is(err, sql.ErrNoRows) {
		err = errGone
	}
	return identity{owner: owner, token: key}, err
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
