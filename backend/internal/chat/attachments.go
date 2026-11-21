package chat

import (
	"bytes"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"mime/multipart"
	"net/http"
	"path"
	"strings"
	"unicode"

	"github.com/qubeyond/neti-next/backend/internal/apperr"
	"github.com/qubeyond/neti-next/backend/internal/identity"
)

const (
	maxFilesPerChat = 60
	MaxFileBytes    = 16 << 20
	maxStoredBytes  = 64 << 20
)

func NewAttachment(filename, declaredType string, payload []byte) (Attachment, error) {
	if len(payload) > MaxFileBytes {
		return Attachment{}, apperr.New(apperr.TooLarge, "Размер файла превышает 16 МБ.")
	}
	if len(payload) == 0 {
		return Attachment{}, apperr.New(apperr.InvalidInput, "Файл пуст.")
	}
	name := path.Base(strings.ReplaceAll(filename, "\\", "/"))
	name = strings.TrimSpace(strings.Map(func(c rune) rune {
		if unicode.IsControl(c) {
			return -1
		}
		return c
	}, name))
	if name == "" || name == "." || name == ".." || name == "/" || len([]rune(name)) > 180 {
		return Attachment{}, apperr.New(apperr.InvalidInput, "Некорректное имя файла.")
	}
	content := http.DetectContentType(payload)
	if content == "video/webm" && declaredType == "audio/webm" {
		content = "audio/webm"
	}
	if content == "video/mp4" && declaredType == "audio/mp4" {
		content = "audio/mp4"
	}
	if content == "application/ogg" {
		content = "audio/ogg"
	}
	if content == "image/jpeg" || content == "image/png" {
		size, _, err := image.DecodeConfig(bytes.NewReader(payload))
		if err != nil {
			return Attachment{}, apperr.New(apperr.InvalidInput, "Изображение не удалось прочитать.")
		}
		const maxPixels = 25_000_000
		if size.Height <= 0 || size.Width <= 0 || size.Width > maxPixels/size.Height {
			return Attachment{}, apperr.New(apperr.TooLarge, "Разрешение изображения превышает лимит.")
		}
	}
	return Attachment{ID: identity.NewID(), Name: name, MIME: content, Size: len(payload)}, nil
}

func ReadAttachmentPart(part *multipart.Part) (Attachment, []byte, error) {
	if part.FormName() != "file" || part.FileName() == "" {
		return Attachment{}, nil, apperr.New(apperr.InvalidInput, "Ожидается файл в поле file.")
	}
	payload, err := io.ReadAll(io.LimitReader(part, MaxFileBytes+1))
	if err != nil {
		return Attachment{}, nil, err
	}
	file, err := NewAttachment(part.FileName(), part.Header.Get("Content-Type"), payload)
	return file, payload, err
}

func (s *Service) Upload(who identity.Actor, chatID string, file Attachment, payload []byte) (Attachment, error) {
	err := s.repo.UpdateChats(who, func(state *State, storage Blobs) error {
		item, err := chatByID(state, chatID)
		if err != nil {
			return err
		}
		if len(item.Files) >= maxFilesPerChat || state.Bytes+len(payload) > maxStoredBytes {
			return apperr.New(apperr.TooLarge, "Достигнут лимит вложений в диалоге или хранилище.")
		}
		if err := storage.Put(file.ID, payload); err != nil {
			return err
		}
		item.Files[file.ID] = file
		state.Bytes += file.Size
		return nil
	})
	return file, err
}

func (s *Service) Attachment(who identity.Actor, chatID, fileID string) (Attachment, []byte, error) {
	var file Attachment
	var payload []byte
	err := s.repo.ViewChats(who, func(state *State, storage Blobs) error {
		item, err := chatByID(state, chatID)
		if err != nil {
			return err
		}
		var exists bool
		file, exists = item.Files[fileID]
		if !exists {
			return apperr.New(apperr.NotFound, "Вложение не найдено.")
		}
		data, err := storage.Get(file.ID)
		payload = bytes.Clone(data)
		return err
	})
	return file, payload, err
}

func (s *Service) DeleteAttachment(who identity.Actor, chatID, fileID string) error {
	return s.repo.UpdateChats(who, func(state *State, storage Blobs) error {
		item, err := chatByID(state, chatID)
		if err != nil {
			return err
		}
		file, exists := item.Files[fileID]
		if !exists {
			return apperr.New(apperr.NotFound, "Вложение не найдено.")
		}
		for _, entry := range item.Messages {
			for _, linked := range entry.Attachments {
				if linked.ID == file.ID {
					return apperr.New(apperr.Conflict, "Вложение уже отправлено в диалог.")
				}
			}
		}
		if err := storage.Delete(file.ID); err != nil {
			return err
		}
		delete(item.Files, file.ID)
		state.Bytes -= file.Size
		return nil
	})
}
