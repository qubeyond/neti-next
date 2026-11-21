package main

import (
	"context"
	"encoding/json"
	"net/http"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

func (s *server) listChats(w http.ResponseWriter, r *http.Request) error {
	who, err := s.actor(r)
	if err != nil {
		return err
	}
	list := make([]chatSummary, 0)
	err = s.store.access(who, false, func(state *workspace, _ blobs) error {
		for _, item := range state.Chats {
			list = append(list, item.chatSummary)
		}
		return nil
	})
	if err != nil {
		return err
	}
	sort.Slice(list, func(i, j int) bool { return list[i].UpdatedAt.After(list[j].UpdatedAt) })
	writeJSON(w, 200, list)
	return nil
}

func (s *server) createChat(w http.ResponseWriter, r *http.Request) error {
	who, err := s.actor(r)
	if err != nil {
		return err
	}
	item := storedChat{
		chat:  chat{chatSummary: chatSummary{ID: randomID(), Title: "Новый диалог", UpdatedAt: now()}, Messages: []message{}},
		Files: make(map[string]attachment),
	}
	err = s.store.access(who, true, func(state *workspace, _ blobs) error {
		if len(state.Chats) >= maxChats {
			return problem(409, "Достигнут лимит диалогов. Удалите один из старых.")
		}
		state.Chats[item.ID] = &item
		return nil
	})
	if err == nil {
		s.events.publish(who, event{Type: "chats.updated", ChatID: item.ID})
		writeJSON(w, 201, item.chatSummary)
	}
	return err
}

func chatByID(state *workspace, id string) (*storedChat, error) {
	item := state.Chats[id]
	if item == nil {
		return nil, problem(404, "Диалог не найден.")
	}
	return item, nil
}

func (s *server) getChat(w http.ResponseWriter, r *http.Request) error {
	who, err := s.actor(r)
	if err != nil {
		return err
	}
	var payload json.RawMessage
	err = s.store.access(who, false, func(state *workspace, _ blobs) error {
		item, err := chatByID(state, r.PathValue("chat"))
		if err == nil {
			payload, err = json.Marshal(item.chat)
		}
		return err
	})
	if err == nil {
		writeJSON(w, 200, payload)
	}
	return err
}

func removeChat(state *workspace, storage blobs, item *storedChat) error {
	for key, file := range item.Files {
		if err := storage.delete(key); err != nil {
			return err
		}
		state.Bytes -= file.Size
	}
	delete(state.Chats, item.ID)
	return nil
}

func (s *server) deleteChat(w http.ResponseWriter, r *http.Request) error {
	who, err := s.actor(r)
	if err != nil {
		return err
	}
	id := r.PathValue("chat")
	err = s.store.access(who, true, func(state *workspace, storage blobs) error {
		item, err := chatByID(state, id)
		if err != nil {
			return err
		}
		return removeChat(state, storage, item)
	})
	if err == nil {
		s.events.publish(who, event{Type: "chats.updated", ChatID: id})
		w.WriteHeader(204)
	}
	return err
}

func (s *server) deleteChats(w http.ResponseWriter, r *http.Request) error {
	who, err := s.actor(r)
	if err != nil {
		return err
	}
	err = s.store.access(who, true, func(state *workspace, storage blobs) error {
		for _, item := range state.Chats {
			if err := removeChat(state, storage, item); err != nil {
				return err
			}
		}
		return nil
	})
	if err == nil {
		s.events.publish(who, event{Type: "chats.updated"})
		w.WriteHeader(204)
	}
	return err
}

type sendInput struct {
	RequestID string   `json:"requestId"`
	Text      string   `json:"text"`
	Quote     string   `json:"quote"`
	FileIDs   []string `json:"fileIds"`
}

type runJob struct {
	who   identity
	chat  string
	id    string
	start time.Time
	input agentRequest
}

func captureRun(who identity, item *storedChat, account profile, userIndex int) *runJob {
	answer := &item.Messages[userIndex+1]
	return &runJob{who: who, chat: item.ID, id: answer.ID, start: answer.CreatedAt, input: agentRequest{
		ChatID: item.ID, History: append([]message(nil), item.Messages[:userIndex]...),
		Message: item.Messages[userIndex], DisplayName: account.Name, Instructions: account.Instructions,
	}}
}

func titled(text string) string {
	line := strings.Join(strings.Fields(text), " ")
	runes := []rune(line)
	if len(runes) > 42 {
		return string(runes[:41]) + "…"
	}
	return line
}

func workingMessage() message {
	return message{ID: randomID(), Role: "assistant", State: "working", CreatedAt: now(),
		Activity: []activity{{Kind: "queued", Label: "Запрос принят"}}}
}

func (s *server) send(w http.ResponseWriter, r *http.Request) error {
	who, err := s.actor(r)
	if err != nil {
		return err
	}
	var input sendInput
	if err := readJSON(w, r, &input); err != nil {
		return err
	}
	input.Text = strings.TrimSpace(input.Text)
	input.Quote = strings.TrimSpace(input.Quote)
	if len(input.RequestID) < 20 || len(input.RequestID) > 64 ||
		strings.ContainsFunc(input.RequestID, func(c rune) bool {
			return c != '-' && !(c >= 'a' && c <= 'z') && !(c >= '0' && c <= '9')
		}) {
		return problem(422, "Некорректный идентификатор сообщения.")
	}
	if input.Text == "" && len(input.FileIDs) == 0 {
		return problem(422, "Добавьте вопрос или вложение.")
	}
	if utf8.RuneCountInString(input.Text) > 2000 || utf8.RuneCountInString(input.Quote) > 1200 || len(input.FileIDs) > 5 {
		return problem(422, "Допустимо до 2000 символов вопроса, до 1200 символов цитаты и до пяти вложений.")
	}
	chatID := r.PathValue("chat")
	var answerID string
	var job *runJob
	err = s.store.access(who, true, func(state *workspace, _ blobs) error {
		item, err := chatByID(state, chatID)
		if err != nil {
			return err
		}
		for index := 0; index < len(item.Messages); index += 2 {
			if item.Messages[index].ID == input.RequestID {
				answerID = item.Messages[index+1].ID
				return nil
			}
		}
		if len(item.Messages) >= maxMessages {
			return problem(409, "Диалог достиг лимита сообщений. Начните новый.")
		}
		if len(item.Messages) > 0 && item.Messages[len(item.Messages)-1].State == "working" {
			return problem(409, "Дождитесь завершения текущего ответа.")
		}
		user := message{ID: input.RequestID, Role: "user", Text: input.Text, Quote: input.Quote, CreatedAt: now()}
		seen := make(map[string]bool)
		for _, id := range input.FileIDs {
			file, ok := item.Files[id]
			if !ok || seen[id] {
				return problem(422, "Вложение отсутствует в этом диалоге или указано дважды.")
			}
			seen[id] = true
			user.Attachments = append(user.Attachments, file)
		}
		answer := workingMessage()
		answerID = answer.ID
		if len(item.Messages) == 0 {
			item.Title = titled(user.Text)
			if item.Title == "" {
				item.Title = titled(user.Attachments[0].Name)
			}
		}
		item.Messages = append(item.Messages, user, answer)
		item.UpdatedAt = now()
		job = captureRun(who, item, state.Profile, len(item.Messages)-2)
		return nil
	})
	if err != nil {
		return err
	}
	if job != nil {
		s.events.publish(who, event{Type: "chats.updated", ChatID: chatID})
		go s.run(*job)
	}
	writeJSON(w, 202, map[string]string{"messageId": answerID})
	return nil
}

func (s *server) retry(w http.ResponseWriter, r *http.Request) error {
	who, err := s.actor(r)
	if err != nil {
		return err
	}
	var job *runJob
	err = s.store.access(who, true, func(state *workspace, _ blobs) error {
		item, err := chatByID(state, r.PathValue("chat"))
		if err != nil {
			return err
		}
		index := len(item.Messages) - 1
		if index < 1 || item.Messages[index].ID != r.PathValue("message") || item.Messages[index].State != "failed" {
			return problem(409, "Можно повторить последний запрос, завершившийся ошибкой.")
		}
		answer := workingMessage()
		answer.ID = item.Messages[index].ID
		item.Messages[index] = answer
		item.UpdatedAt = now()
		job = captureRun(who, item, state.Profile, index-1)
		return nil
	})
	if err == nil {
		s.events.publish(who, event{Type: "chat.updated", ChatID: job.chat})
		go s.run(*job)
		writeJSON(w, 202, map[string]string{"messageId": job.id})
	}
	return err
}

func (s *server) updateRun(job runJob, apply func(*message)) bool {
	err := s.store.access(job.who, true, func(state *workspace, _ blobs) error {
		item, err := chatByID(state, job.chat)
		if err != nil {
			return err
		}
		last := &item.Messages[len(item.Messages)-1]
		if last.ID != job.id || last.State != "working" || !last.CreatedAt.Equal(job.start) {
			return problem(409, "Ответ уже завершён.")
		}
		apply(last)
		return nil
	})
	if err == nil {
		s.events.publish(job.who, event{Type: "chat.updated", ChatID: job.chat})
	}
	return err == nil
}

func (s *server) run(job runJob) {
	if !s.updateRun(job, func(item *message) {
		item.Activity = append(item.Activity, activity{Kind: "request", Label: "Агент обрабатывает вопрос", Elapsed: time.Since(job.start).Milliseconds()})
	}) {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 115*time.Second)
	defer cancel()
	job.input.ReadAttachment = func(id string) ([]byte, error) {
		_, data, err := s.attachmentData(job.who, job.chat, id)
		return data, err
	}
	answer, err := s.agent.answer(ctx, job.input, func(step activity) {
		step.Elapsed = time.Since(job.start).Milliseconds()
		s.updateRun(job, func(item *message) {
			if len(item.Activity) < 40 {
				item.Activity = append(item.Activity, step)
			}
		})
	})
	s.updateRun(job, func(item *message) {
		item.Duration = time.Since(job.start).Milliseconds()
		if err != nil {
			item.State = "failed"
			item.Error = err.Error()
			return
		}
		item.State, item.Status, item.Text = "complete", answer.Status, answer.Answer
		item.Citations, item.Artifacts = answer.Citations, answer.Artifacts
	})
}
