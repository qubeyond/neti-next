package chat

import "github.com/qubeyond/neti-next/backend/internal/identity"

type State struct {
	Chats        map[string]*StoredChat
	Bytes        int
	DisplayName  string
	Instructions string
}

type Blobs interface {
	Get(id string) ([]byte, error)
	Put(id string, payload []byte) error
	Delete(id string) error
}

type Repository interface {
	ViewChats(who identity.Actor, view func(*State, Blobs) error) error

	// UpdateChats keeps changes to chat state and attachments in the same transaction.
	UpdateChats(who identity.Actor, update func(*State, Blobs) error) error
}
