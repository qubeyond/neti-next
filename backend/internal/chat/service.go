package chat

import (
	"context"
	"slices"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/qubeyond/neti-next/backend/internal/apperr"
	"github.com/qubeyond/neti-next/backend/internal/identity"
)

const (
	maxChats    = 60
	maxMessages = 100
)

type Events interface {
	Publish(who identity.Actor, kind, chatID string)
}

type Service struct {
	repo   Repository
	agent  Agent
	events Events
}

func NewService(repo Repository, agent Agent, events Events) *Service {
	return &Service{repo: repo, agent: agent, events: events}
}

func (s *Service) List(who identity.Actor) ([]Summary, error) {
	list := make([]Summary, 0)
	err := s.repo.ViewChats(who, func(state *State, _ Blobs) error {
		for _, item := range state.Chats {
			list = append(list, item.Summary)
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Slice(list, func(i, j int) bool { return list[i].UpdatedAt.After(list[j].UpdatedAt) })
	return list, nil
}

func (s *Service) Create(who identity.Actor) (Summary, error) {
	item := StoredChat{
		Chat:  Chat{Summary: Summary{ID: identity.NewID(), Title: "Новый диалог", UpdatedAt: time.Now().UTC()}, Messages: []Message{}},
		Files: make(map[string]Attachment),
	}
	summary := item.Summary
	err := s.repo.UpdateChats(who, func(state *State, _ Blobs) error {
		if len(state.Chats) >= maxChats {
			return apperr.New(apperr.Conflict, "Достигнут лимит диалогов. Удалите один из старых.")
		}
		state.Chats[item.ID] = &item
		return nil
	})
	if err == nil {
		s.events.Publish(who, "chats.updated", summary.ID)
	}
	return summary, err
}

func chatByID(state *State, id string) (*StoredChat, error) {
	item := state.Chats[id]
	if item == nil {
		return nil, apperr.New(apperr.NotFound, "Диалог не найден.")
	}
	return item, nil
}

func (s *Service) Get(who identity.Actor, id string) (Chat, error) {
	var result Chat
	err := s.repo.ViewChats(who, func(state *State, _ Blobs) error {
		item, err := chatByID(state, id)
		if err != nil {
			return err
		}
		result = item.Chat
		result.Messages = slices.Clone(item.Messages)
		return nil
	})
	return result, err
}

func removeChat(state *State, storage Blobs, item *StoredChat) error {
	for key, file := range item.Files {
		if err := storage.Delete(key); err != nil {
			return err
		}
		state.Bytes -= file.Size
	}
	delete(state.Chats, item.ID)
	return nil
}

func (s *Service) Delete(who identity.Actor, id string) error {
	err := s.repo.UpdateChats(who, func(state *State, storage Blobs) error {
		item, err := chatByID(state, id)
		if err != nil {
			return err
		}
		return removeChat(state, storage, item)
	})
	if err == nil {
		s.events.Publish(who, "chats.updated", id)
	}
	return err
}

func (s *Service) DeleteAll(who identity.Actor) error {
	err := s.repo.UpdateChats(who, func(state *State, storage Blobs) error {
		for _, item := range state.Chats {
			if err := removeChat(state, storage, item); err != nil {
				return err
			}
		}
		return nil
	})
	if err == nil {
		s.events.Publish(who, "chats.updated", "")
	}
	return err
}

type SendInput struct {
	RequestID string   `json:"requestId"`
	Text      string   `json:"text"`
	Quote     string   `json:"quote"`
	FileIDs   []string `json:"fileIds"`
}

type runJob struct {
	who   identity.Actor
	chat  string
	id    string
	start time.Time
	input AgentRequest
}

func captureRun(who identity.Actor, item *StoredChat, state *State, userIndex int) *runJob {
	answer := &item.Messages[userIndex+1]
	return &runJob{who: who, chat: item.ID, id: answer.ID, start: answer.CreatedAt, input: AgentRequest{
		ChatID: item.ID, History: append([]Message(nil), item.Messages[:userIndex]...),
		Message: item.Messages[userIndex], DisplayName: state.DisplayName, Instructions: state.Instructions,
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

func workingMessage() Message {
	return Message{ID: identity.NewID(), Role: "assistant", State: "working", CreatedAt: time.Now().UTC(),
		Activity: []Activity{{Kind: "queued", Label: "Запрос принят"}}}
}

func (s *Service) Send(who identity.Actor, chatID string, input SendInput) (string, error) {
	input.Text = strings.TrimSpace(input.Text)
	input.Quote = strings.TrimSpace(input.Quote)
	if len(input.RequestID) < 20 || len(input.RequestID) > 64 ||
		strings.ContainsFunc(input.RequestID, func(c rune) bool {
			return c != '-' && !(c >= 'a' && c <= 'z') && !(c >= '0' && c <= '9')
		}) {
		return "", apperr.New(apperr.InvalidInput, "Некорректный идентификатор сообщения.")
	}
	if input.Text == "" && len(input.FileIDs) == 0 {
		return "", apperr.New(apperr.InvalidInput, "Добавьте вопрос или вложение.")
	}
	if utf8.RuneCountInString(input.Text) > 2000 || utf8.RuneCountInString(input.Quote) > 1200 || len(input.FileIDs) > 5 {
		return "", apperr.New(apperr.InvalidInput, "Допустимо до 2000 символов вопроса, до 1200 символов цитаты и до пяти вложений.")
	}
	var answerID string
	var job *runJob
	err := s.repo.UpdateChats(who, func(state *State, _ Blobs) error {
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
			return apperr.New(apperr.Conflict, "Диалог достиг лимита сообщений. Начните новый.")
		}
		if len(item.Messages) > 0 && item.Messages[len(item.Messages)-1].State == "working" {
			return apperr.New(apperr.Conflict, "Дождитесь завершения текущего ответа.")
		}
		user := Message{ID: input.RequestID, Role: "user", Text: input.Text, Quote: input.Quote, CreatedAt: time.Now().UTC()}
		seen := make(map[string]bool)
		for _, id := range input.FileIDs {
			file, ok := item.Files[id]
			if !ok || seen[id] {
				return apperr.New(apperr.InvalidInput, "Вложение отсутствует в этом диалоге или указано дважды.")
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
		item.UpdatedAt = time.Now().UTC()
		job = captureRun(who, item, state, len(item.Messages)-2)
		return nil
	})
	if err != nil {
		return "", err
	}
	if job != nil {
		s.events.Publish(who, "chats.updated", chatID)
		go s.run(*job)
	}
	return answerID, nil
}

func (s *Service) Retry(who identity.Actor, chatID, messageID string) (string, error) {
	var job *runJob
	err := s.repo.UpdateChats(who, func(state *State, _ Blobs) error {
		item, err := chatByID(state, chatID)
		if err != nil {
			return err
		}
		index := len(item.Messages) - 1
		if index < 1 || item.Messages[index].ID != messageID || item.Messages[index].State != "failed" {
			return apperr.New(apperr.Conflict, "Можно повторить последний запрос, завершившийся ошибкой.")
		}
		answer := workingMessage()
		answer.ID = item.Messages[index].ID
		item.Messages[index] = answer
		item.UpdatedAt = time.Now().UTC()
		job = captureRun(who, item, state, index-1)
		return nil
	})
	if err != nil {
		return "", err
	}
	s.events.Publish(who, "chat.updated", job.chat)
	go s.run(*job)
	return job.id, nil
}

func (s *Service) updateRun(job runJob, apply func(*Message)) bool {
	err := s.repo.UpdateChats(job.who, func(state *State, _ Blobs) error {
		item, err := chatByID(state, job.chat)
		if err != nil {
			return err
		}
		last := &item.Messages[len(item.Messages)-1]
		if last.ID != job.id || last.State != "working" || !last.CreatedAt.Equal(job.start) {
			return apperr.New(apperr.Conflict, "Ответ уже завершён.")
		}
		apply(last)
		return nil
	})
	if err == nil {
		s.events.Publish(job.who, "chat.updated", job.chat)
	}
	return err == nil
}

func (s *Service) run(job runJob) {
	if !s.updateRun(job, func(item *Message) {
		item.Activity = append(item.Activity, Activity{Kind: "request", Label: "Агент обрабатывает вопрос", Elapsed: time.Since(job.start).Milliseconds()})
	}) {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 115*time.Second)
	defer cancel()
	job.input.ReadAttachment = func(id string) ([]byte, error) {
		_, data, err := s.Attachment(job.who, job.chat, id)
		return data, err
	}
	answer, err := s.agent.Answer(ctx, job.input, func(step Activity) {
		step.Elapsed = time.Since(job.start).Milliseconds()
		s.updateRun(job, func(item *Message) {
			if len(item.Activity) < 40 {
				item.Activity = append(item.Activity, step)
			}
		})
	})
	s.updateRun(job, func(item *Message) {
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
