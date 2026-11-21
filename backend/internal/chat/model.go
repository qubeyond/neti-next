package chat

import "time"

type Attachment struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	MIME string `json:"mime"`
	Size int    `json:"size"`
}

type Citation struct {
	URL   string `json:"url"`
	Quote string `json:"quote"`
}

type Artifact struct {
	Kind  string `json:"kind"`
	Title string `json:"title"`
	URL   string `json:"url,omitempty"`
	Text  string `json:"text,omitempty"`
}

type Activity struct {
	Kind    string `json:"kind"`
	Label   string `json:"label"`
	Elapsed int64  `json:"elapsedMs"`
}

type Message struct {
	ID          string       `json:"id"`
	Role        string       `json:"role"`
	Text        string       `json:"text"`
	Quote       string       `json:"quote,omitempty"`
	Attachments []Attachment `json:"attachments,omitempty"`
	State       string       `json:"state,omitempty"`
	Status      string       `json:"status,omitempty"`
	Error       string       `json:"error,omitempty"`
	Citations   []Citation   `json:"citations,omitempty"`
	Artifacts   []Artifact   `json:"artifacts,omitempty"`
	Activity    []Activity   `json:"activity,omitempty"`
	CreatedAt   time.Time    `json:"createdAt"`
	Duration    int64        `json:"durationMs,omitempty"`
}

type Summary struct {
	ID        string    `json:"id"`
	Title     string    `json:"title"`
	UpdatedAt time.Time `json:"updatedAt"`
}

type Chat struct {
	Summary
	Messages []Message `json:"messages"`
}

type StoredChat struct {
	Chat
	Files map[string]Attachment `json:"files"`
}
