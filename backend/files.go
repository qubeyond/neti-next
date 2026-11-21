package main

import (
	"bytes"
	"errors"
	"io"
	"mime"
	"net/http"
	"path"
	"strings"
	"time"
	"unicode"
)

func (s *server) upload(w http.ResponseWriter, r *http.Request) error {
	who, err := s.actor(r)
	if err != nil {
		return err
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxFileBytes+(64<<10))
	reader, err := r.MultipartReader()
	if err != nil {
		return problem(422, "Ожидается файл в поле file.")
	}
	part, err := reader.NextPart()
	if err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			return err
		}
		return problem(422, "Ожидается файл в поле file.")
	}
	defer r.Body.Close()
	if part.FormName() != "file" || part.FileName() == "" {
		return problem(422, "Ожидается файл в поле file.")
	}
	payload, err := io.ReadAll(io.LimitReader(part, maxFileBytes+1))
	if err != nil {
		return err
	}
	if len(payload) > maxFileBytes {
		return problem(413, "Размер файла превышает 16 МБ.")
	}
	if len(payload) == 0 {
		return problem(422, "Файл пуст.")
	}
	name := path.Base(strings.ReplaceAll(part.FileName(), "\\", "/"))
	name = strings.TrimSpace(strings.Map(func(c rune) rune {
		if unicode.IsControl(c) {
			return -1
		}
		return c
	}, name))
	if name == "" || name == "." || name == ".." || name == "/" || len([]rune(name)) > 180 {
		return problem(422, "Некорректное имя файла.")
	}
	content := http.DetectContentType(payload)
	if content == "video/webm" && part.Header.Get("Content-Type") == "audio/webm" {
		content = "audio/webm"
	}
	if content == "video/mp4" && part.Header.Get("Content-Type") == "audio/mp4" {
		content = "audio/mp4"
	}
	if content == "application/ogg" {
		content = "audio/ogg"
	}
	file := attachment{ID: randomID(), Name: name, MIME: content, Size: len(payload)}
	err = s.store.access(who, true, func(state *workspace, storage blobs) error {
		item, err := chatByID(state, r.PathValue("chat"))
		if err != nil {
			return err
		}
		if len(item.Files) >= maxFilesPerChat || state.Bytes+len(payload) > maxStoredBytes {
			return problem(413, "Достигнут лимит вложений в диалоге или хранилище.")
		}
		if who.guest {
			total := 0
			for _, entry := range s.store.guests {
				total += entry.state.Bytes
			}
			if state.Bytes+len(payload) > 32<<20 || total+len(payload) > 256<<20 {
				return problem(413, "Достигнут лимит гостевого хранилища.")
			}
		}
		if err := storage.put(file.ID, payload); err != nil {
			return err
		}
		item.Files[file.ID] = file
		state.Bytes += file.Size
		return nil
	})
	if err == nil {
		writeJSON(w, 201, file)
	}
	return err
}

func (s *server) attachmentData(who identity, chatID, fileID string) (attachment, []byte, error) {
	var file attachment
	var payload []byte
	err := s.store.access(who, false, func(state *workspace, storage blobs) error {
		item, err := chatByID(state, chatID)
		if err != nil {
			return err
		}
		var exists bool
		file, exists = item.Files[fileID]
		if !exists {
			return problem(404, "Вложение не найдено.")
		}
		payload = bytes.Clone(storage.get(file.ID))
		return nil
	})
	return file, payload, err
}

func (s *server) download(w http.ResponseWriter, r *http.Request) error {
	who, err := s.actor(r)
	if err != nil {
		return err
	}
	file, payload, err := s.attachmentData(who, r.PathValue("chat"), r.PathValue("file"))
	if err != nil {
		return err
	}
	w.Header().Set("Content-Type", file.MIME)
	w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": file.Name}))
	http.ServeContent(w, r, file.Name, time.Time{}, bytes.NewReader(payload))
	return nil
}

func (s *server) deleteFile(w http.ResponseWriter, r *http.Request) error {
	who, err := s.actor(r)
	if err != nil {
		return err
	}
	err = s.store.access(who, true, func(state *workspace, storage blobs) error {
		item, err := chatByID(state, r.PathValue("chat"))
		if err != nil {
			return err
		}
		file, exists := item.Files[r.PathValue("file")]
		if !exists {
			return problem(404, "Вложение не найдено.")
		}
		for _, entry := range item.Messages {
			for _, linked := range entry.Attachments {
				if linked.ID == file.ID {
					return problem(409, "Вложение уже отправлено в диалог.")
				}
			}
		}
		if err := storage.delete(file.ID); err != nil {
			return err
		}
		delete(item.Files, file.ID)
		state.Bytes -= file.Size
		return nil
	})
	if err == nil {
		w.WriteHeader(204)
	}
	return err
}
