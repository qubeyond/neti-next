package main

import (
	"net/http"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

type event struct {
	Type   string `json:"type"`
	ChatID string `json:"chatId,omitempty"`
}

type subscriber struct {
	who    identity
	conn   *websocket.Conn
	events chan event
}

type eventHub struct {
	mu      sync.Mutex
	clients map[*subscriber]struct{}
}

func newEventHub() *eventHub {
	return &eventHub{clients: make(map[*subscriber]struct{})}
}

func (hub *eventHub) publish(who identity, change event) {
	hub.mu.Lock()
	defer hub.mu.Unlock()
	for client := range hub.clients {
		if client.who.owner != who.owner || client.who.guest != who.guest {
			continue
		}
		select {
		case client.events <- change:
		default:
			client.conn.Close()
		}
	}
}

func (hub *eventHub) disconnect(match func(identity) bool) {
	hub.mu.Lock()
	defer hub.mu.Unlock()
	for client := range hub.clients {
		if match(client.who) {
			client.conn.Close()
		}
	}
}

var upgrader = websocket.Upgrader{Subprotocols: []string{"neti.v1"}, CheckOrigin: sameOrigin}

func (s *server) connectEvents(w http.ResponseWriter, r *http.Request) error {
	var who identity
	var err error
	var value string
	for _, protocol := range websocket.Subprotocols(r) {
		if len(protocol) == 54 && protocol[:6] == "guest." {
			value = protocol[6:]
			break
		}
	}
	if value != "" {
		who, err = s.store.authenticate(value, true)
	} else {
		if cookie, lookup := r.Cookie(sessionCookie); lookup == nil {
			value = cookie.Value
		}
		who, err = s.store.authenticate(value, false)
	}
	if err != nil {
		return err
	}
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return nil
	}
	client := &subscriber{who: who, conn: conn, events: make(chan event, 16)}
	s.events.mu.Lock()
	s.events.clients[client] = struct{}{}
	_, err = s.store.authenticate(value, who.guest)
	s.events.mu.Unlock()
	defer func() {
		s.events.mu.Lock()
		delete(s.events.clients, client)
		s.events.mu.Unlock()
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
			alive := true
			if who.guest {
				s.store.mu.Lock()
				entry := s.store.guests[who.token]
				alive = entry != nil && time.Now().Before(entry.expires)
				s.store.mu.Unlock()
			} else {
				_, err := s.store.authenticate(value, false)
				alive = err == nil
			}
			if !alive || conn.WriteControl(websocket.PingMessage, nil, time.Now().Add(5*time.Second)) != nil {
				return nil
			}
		}
	}
}
