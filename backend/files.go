package main

import (
	"bytes"
	"errors"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"mime"
	"mime/multipart"
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
	file, payload, err := readAttachmentPart(part)
	if err != nil {
		return err
	}
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

func readAttachmentPart(part *multipart.Part) (attachment, []byte, error) {
	if part.FormName() != "file" || part.FileName() == "" {
		return attachment{}, nil, problem(422, "Ожидается файл в поле file.")
	}
	payload, err := io.ReadAll(io.LimitReader(part, maxFileBytes+1))
	if err != nil {
		return attachment{}, nil, err
	}
	if len(payload) > maxFileBytes {
		return attachment{}, nil, problem(413, "Размер файла превышает 16 МБ.")
	}
	if len(payload) == 0 {
		return attachment{}, nil, problem(422, "Файл пуст.")
	}
	name := path.Base(strings.ReplaceAll(part.FileName(), "\\", "/"))
	name = strings.TrimSpace(strings.Map(func(c rune) rune {
		if unicode.IsControl(c) {
			return -1
		}
		return c
	}, name))
	if name == "" || name == "." || name == ".." || name == "/" || len([]rune(name)) > 180 {
		return attachment{}, nil, problem(422, "Некорректное имя файла.")
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
	if content == "image/jpeg" || content == "image/png" {
		imageSize, _, err := image.DecodeConfig(bytes.NewReader(payload))
		if err != nil {
			return attachment{}, nil, problem(422, "Изображение не удалось прочитать.")
		}
		const maxPixels = 25_000_000
		if imageSize.Height <= 0 || imageSize.Width <= 0 || imageSize.Width > maxPixels/imageSize.Height {
			return attachment{}, nil, problem(413, "Разрешение изображения превышает лимит.")
		}
	}
	file := attachment{ID: randomID(), Name: name, MIME: content, Size: len(payload)}
	return file, payload, nil
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
		data, err := storage.get(file.ID)
		payload = append([]byte(nil), data...)
		return err
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
	http.ServeContent(&fileResponseWriter{ResponseWriter: w}, r, file.Name, time.Time{}, bytes.NewReader(payload))
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

type fileResponseWriter struct {
	http.ResponseWriter
	failed bool
}

func (w *fileResponseWriter) WriteHeader(status int) {
	if status >= 400 {
		w.failed = true
		w.Header().Del("Content-Disposition")
		w.Header().Del("Content-Length")
		writeError(w.ResponseWriter, problem(status, "Не удалось вернуть запрошенную часть файла."))
		return
	}
	w.ResponseWriter.WriteHeader(status)
}

func (w *fileResponseWriter) Write(data []byte) (int, error) {
	if w.failed {
		return len(data), nil
	}
	return w.ResponseWriter.Write(data)
}
