package events

import (
	"net/http"
	"sync"
	"time"

	"github.com/qubeyond/neti-next/backend/internal/account"
	"github.com/qubeyond/neti-next/backend/internal/httpapi"
	"github.com/qubeyond/neti-next/backend/internal/identity"

	"github.com/gorilla/websocket"
)

type Event struct {
	Type   string `json:"type"`
	ChatID string `json:"chatId,omitempty"`
}

type subscriber struct {
	who    identity.Actor
	conn   *websocket.Conn
	events chan Event
}

type Hub struct {
	mu      sync.Mutex
	clients map[*subscriber]struct{}
}

func New() *Hub {
	return &Hub{clients: make(map[*subscriber]struct{})}
}

func (hub *Hub) Publish(who identity.Actor, kind, chatID string) {
	hub.mu.Lock()
	defer hub.mu.Unlock()
	for client := range hub.clients {
		if client.who.Owner != who.Owner || client.who.Guest != who.Guest {
			continue
		}
		select {
		case client.events <- Event{Type: kind, ChatID: chatID}:
		default:
			client.conn.Close()
		}
	}
}

func (hub *Hub) Disconnect(match func(identity.Actor) bool) {
	hub.mu.Lock()
	defer hub.mu.Unlock()
	for client := range hub.clients {
		if match(client.who) {
			client.conn.Close()
		}
	}
}

var upgrader = websocket.Upgrader{Subprotocols: []string{"neti.v1"}, CheckOrigin: httpapi.SameOrigin}

type Sessions interface {
	Authenticate(token string, guest bool) (identity.Actor, error)
	SessionAlive(token string, who identity.Actor) bool
}

func (hub *Hub) Handler(sessions Sessions) http.HandlerFunc {
	return httpapi.Handle(func(w http.ResponseWriter, r *http.Request) error {
		return hub.connect(sessions, w, r)
	})
}

func (hub *Hub) connect(sessions Sessions, w http.ResponseWriter, r *http.Request) error {
	var who identity.Actor
	var err error
	var value string
	for _, protocol := range websocket.Subprotocols(r) {
		if len(protocol) == 54 && protocol[:6] == "guest." {
			value = protocol[6:]
			break
		}
	}
	if value != "" {
		who, err = sessions.Authenticate(value, true)
	} else {
		if cookie, lookup := r.Cookie(account.SessionCookie); lookup == nil {
			value = cookie.Value
		}
		who, err = sessions.Authenticate(value, false)
	}
	if err != nil {
		return err
	}
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return nil
	}
	client := &subscriber{who: who, conn: conn, events: make(chan Event, 16)}
	hub.mu.Lock()
	hub.clients[client] = struct{}{}
	_, err = sessions.Authenticate(value, who.Guest)
	hub.mu.Unlock()
	defer func() {
		hub.mu.Lock()
		delete(hub.clients, client)
		hub.mu.Unlock()
		conn.Close()
	}()
	if err != nil {
		return nil
	}
	done := make(chan struct{})
	conn.SetReadLimit(1024)
	conn.SetReadDeadline(time.Now().Add(45 * time.Second))
	conn.SetPongHandler(func(string) error { return conn.SetReadDeadline(time.Now().Add(45 * time.Second)) })
	go func() {
		defer close(done)
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				return
			}
		}
	}()
	ticker := time.NewTicker(20 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-done:
			return nil
		case change := <-client.events:
			conn.SetWriteDeadline(time.Now().Add(5 * time.Second))
			if err := conn.WriteJSON(change); err != nil {
				return nil
			}
		case <-ticker.C:
			// Проверка сессии не продлевает срок хранения гостевого диалога.
			if !sessions.SessionAlive(value, who) || conn.WriteControl(websocket.PingMessage, nil, time.Now().Add(5*time.Second)) != nil {
				return nil
			}
		}
	}
}
