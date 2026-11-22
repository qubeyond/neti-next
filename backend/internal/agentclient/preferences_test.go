package agentclient

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/qubeyond/neti-next/backend/internal/chat"
)

func TestPreparePreservesProfileInstructions(t *testing.T) {
	agent := New("http://agent")
	input := chat.AgentRequest{Message: chat.Message{Text: "Привет"}, Instructions: "Отвечай кратко"}
	prepared, err := agent.prepare(context.Background(), input, func(chat.Activity) {})
	if err != nil {
		t.Fatal(err)
	}
	var body map[string]any
	if err := json.Unmarshal(prepared.payload, &body); err != nil {
		t.Fatal(err)
	}
	if body["user_instructions"] != input.Instructions {
		t.Fatal("Предпочтения профиля не переданы агенту")
	}
}
