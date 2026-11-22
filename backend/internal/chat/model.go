package chat

import (
	"time"

	"github.com/qubeyond/neti-next/backend/internal/httpapi"
)

type Attachment struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	MIME string `json:"mime"`
	Size int    `json:"size"`
}

type Citation struct {
	URL          string       `json:"url"`
	Quote        string       `json:"quote"`
	Ref          int          `json:"ref,omitempty"`
	AttachmentID string       `json:"attachment_id,omitempty"`
	Region       *ImageRegion `json:"region,omitempty"`
}

type CalendarEvent struct {
	Date     string `json:"date"`
	Title    string `json:"title"`
	Citation int    `json:"citation"`
}

type BlockCell struct {
	Value string `json:"value"`
	Ref   int    `json:"ref"`
}

type Block struct {
	Kind     string         `json:"kind"`
	Title    string         `json:"title"`
	Columns  []string       `json:"columns,omitempty"`
	Rows     [][]*BlockCell `json:"rows,omitempty"`
	Operator string         `json:"operator,omitempty"`
	Operands []BlockCell    `json:"operands,omitempty"`
	Result   string         `json:"result,omitempty"`
	Note     string         `json:"note,omitempty"`
}

type Artifact struct {
	Kind  string `json:"kind"`
	Title string `json:"title"`
	URL   string `json:"url,omitempty"`
	Text  string `json:"text,omitempty"`
}

type ActivitySource struct {
	URL     string `json:"url"`
	Title   string `json:"title"`
	Excerpt string `json:"excerpt"`
}

type Activity struct {
	ID       string           `json:"id,omitempty"`
	Kind     string           `json:"kind"`
	Label    string           `json:"label"`
	Elapsed  int64            `json:"elapsedMs"`
	Duration *int             `json:"durationMs,omitempty"`
	State    string           `json:"state,omitempty"`
	Count    *int             `json:"count,omitempty"`
	Provider string           `json:"provider,omitempty"`
	Domains  []string         `json:"domains,omitempty"`
	Skills   []string         `json:"skills,omitempty"`
	Sources  []ActivitySource `json:"sources,omitempty"`
}

type Message struct {
	ID          string           `json:"id"`
	Role        string           `json:"role"`
	Text        string           `json:"text"`
	Quote       string           `json:"quote,omitempty"`
	Attachments []Attachment     `json:"attachments,omitempty"`
	State       string           `json:"state,omitempty"`
	Status      string           `json:"status,omitempty"`
	Error       string           `json:"error,omitempty"`
	Problem     *httpapi.Problem `json:"problem,omitempty"`
	Citations   []Citation       `json:"citations,omitempty"`
	Blocks      []Block          `json:"blocks,omitempty"`
	Suggestions []string         `json:"suggestions,omitempty"`
	Events      []CalendarEvent  `json:"events,omitempty"`
	Artifacts   []Artifact       `json:"artifacts,omitempty"`
	Activity    []Activity       `json:"activity,omitempty"`
	CreatedAt   time.Time        `json:"createdAt"`
	Duration    int64            `json:"durationMs,omitempty"`
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

type ImageRegion struct {
	X      float64 `json:"x"`
	Y      float64 `json:"y"`
	Width  float64 `json:"width"`
	Height float64 `json:"height"`
}
