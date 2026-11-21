package main

import (
	"database/sql"
	"encoding/json"
	"errors"
	"os"
	"time"

	bolt "go.etcd.io/bbolt"
)

const initialSchema = `
CREATE TABLE IF NOT EXISTS backend.workspaces (
	id text PRIMARY KEY,
	email text UNIQUE NOT NULL,
	state json NOT NULL,
	has_working_turn boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS backend.attachments (
	owner text NOT NULL REFERENCES backend.workspaces ON DELETE CASCADE,
	id text NOT NULL,
	contents bytea NOT NULL,
	PRIMARY KEY (owner, id)
);
CREATE TABLE IF NOT EXISTS backend.sessions (
	token text PRIMARY KEY,
	owner text NOT NULL REFERENCES backend.workspaces ON DELETE CASCADE,
	expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_owner ON backend.sessions (owner);
CREATE INDEX IF NOT EXISTS sessions_expiration ON backend.sessions (expires_at);
CREATE TABLE IF NOT EXISTS backend.migrations (name text PRIMARY KEY);
`

func (s *store) initialize(legacyPath string) error {
	return s.transaction(60*time.Second, true, func(tx *sql.Tx) error {
		if _, err := tx.Exec("SELECT pg_advisory_xact_lock(191947566, 1)"); err != nil {
			return err
		}
		if _, err := tx.Exec(initialSchema); err != nil {
			return err
		}
		if legacyPath != "" {
			if err := importBolt(tx, legacyPath); err != nil {
				return err
			}
		}
		return recoverInterrupted(tx)
	})
}

func importBolt(tx *sql.Tx, path string) error {
	var imported bool
	if err := tx.QueryRow("SELECT EXISTS (SELECT FROM backend.migrations WHERE name = 'bolt-v1')").Scan(&imported); err != nil || imported {
		return err
	}
	if _, err := os.Stat(path); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return err
	}
	legacy, err := bolt.Open(path, 0600, &bolt.Options{ReadOnly: true, Timeout: 2 * time.Second})
	if err != nil {
		return err
	}
	defer legacy.Close()

	var occupied bool
	if err := tx.QueryRow("SELECT EXISTS (SELECT FROM backend.workspaces)").Scan(&occupied); err != nil {
		return err
	}
	if occupied {
		return errors.New("cannot import BoltDB into a PostgreSQL schema with existing accounts")
	}
	if err := legacy.View(func(source *bolt.Tx) error {
		if err := importWorkspaces(tx, source.Bucket([]byte("workspaces"))); err != nil {
			return err
		}
		sessions := source.Bucket([]byte("sessions"))
		if sessions == nil {
			return nil
		}
		return sessions.ForEach(func(key, value []byte) error {
			var item session
			if json.Unmarshal(value, &item) != nil || !item.Expires.After(time.Now()) {
				return nil
			}
			_, err := tx.Exec("INSERT INTO backend.sessions (token, owner, expires_at) VALUES ($1, $2, $3)", string(key), item.Owner, item.Expires)
			return err
		})
	}); err != nil {
		return err
	}
	_, err = tx.Exec("INSERT INTO backend.migrations (name) VALUES ('bolt-v1')")
	return err
}

func importWorkspaces(tx *sql.Tx, owners *bolt.Bucket) error {
	if owners == nil {
		return nil
	}
	return owners.ForEachBucket(func(key []byte) error {
		account := owners.Bucket(key)
		raw := account.Get([]byte("state"))
		var state workspace
		if err := json.Unmarshal(raw, &state); err != nil {
			return err
		}
		owner := string(key)
		if _, err := tx.Exec("INSERT INTO backend.workspaces (id, email, state, has_working_turn) VALUES ($1, $2, $3, $4)", owner, state.Profile.Email, raw, state.hasWorkingTurn()); err != nil {
			return err
		}
		storage := blobs{tx: tx, owner: owner}
		return account.ForEach(func(key, value []byte) error {
			if string(key) == "state" || value == nil {
				return nil
			}
			return storage.put(string(key), value)
		})
	})
}

func recoverInterrupted(tx *sql.Tx) error {
	rows, err := tx.Query(`
		SELECT id, state FROM backend.workspaces
		WHERE has_working_turn
		FOR UPDATE
	`)
	if err != nil {
		return err
	}
	defer rows.Close()
	pending := make(map[string]workspace)
	for rows.Next() {
		var id string
		var raw []byte
		var state workspace
		if err := rows.Scan(&id, &raw); err != nil {
			return err
		}
		if err := json.Unmarshal(raw, &state); err != nil {
			return err
		}
		pending[id] = state
	}
	if err := rows.Err(); err != nil {
		return err
	}
	for id, state := range pending {
		for _, chat := range state.Chats {
			for index := range chat.Messages {
				item := &chat.Messages[index]
				if item.State == "working" {
					item.State = "failed"
					item.Error = "Сервис перезапустился. Повторите запрос."
					reply := problemResponse(503, item.Error, requestID(""))
					item.Problem = &reply
				}
			}
		}
		if err := saveWorkspace(tx, id, state); err != nil {
			return err
		}
	}
	return nil
}
