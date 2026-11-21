package chat

import "context"

// Объявляет данные, доступные следующему адаптеру. Текущий HTTP-метод поддерживает только вопрос.
type AgentRequest struct {
	ChatID         string
	History        []Message
	Message        Message
	DisplayName    string
	Instructions   string
	ReadAttachment func(string) ([]byte, error)
}

type AgentAnswer struct {
	Status    string     `json:"status"`
	Answer    string     `json:"answer"`
	Citations []Citation `json:"citations"`
	Artifacts []Artifact `json:"artifacts"`
}

type Agent interface {
	Answer(context.Context, AgentRequest, func(Activity)) (AgentAnswer, error)
}
