package chat

import "context"

// Данные текущего хода передаются из рабочего пространства адаптеру агента.
type AgentRequest struct {
	ChatID         string
	History        []Message
	Message        Message
	DisplayName    string
	Instructions   string
	ReadAttachment func(string) ([]byte, error)
}

type AgentAnswer struct {
	Status      string          `json:"status"`
	Answer      string          `json:"answer"`
	Citations   []Citation      `json:"citations"`
	Blocks      []Block         `json:"blocks,omitempty"`
	Suggestions []string        `json:"suggestions,omitempty"`
	Events      []CalendarEvent `json:"events"`
	Artifacts   []Artifact      `json:"artifacts"`
}

type Agent interface {
	Answer(context.Context, AgentRequest, func(Activity)) (AgentAnswer, error)
}
