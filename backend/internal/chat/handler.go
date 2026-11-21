package chat

import (
	"bytes"
	"errors"
	"io"
	"mime"
	"net/http"
	"time"

	"github.com/qubeyond/neti-next/backend/internal/apperr"
	"github.com/qubeyond/neti-next/backend/internal/httpapi"
	"github.com/qubeyond/neti-next/backend/internal/identity"
)

type Handler struct {
	service *Service
	actor   func(*http.Request) (identity.Actor, error)
}

func NewHandler(service *Service, actor func(*http.Request) (identity.Actor, error)) *Handler {
	return &Handler{service: service, actor: actor}
}

func (h *Handler) RegisterRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /api/v1/chats", httpapi.Handle(h.list))
	mux.HandleFunc("POST /api/v1/chats", httpapi.Handle(h.create))
	mux.HandleFunc("DELETE /api/v1/chats", httpapi.Handle(h.deleteAll))
	mux.HandleFunc("GET /api/v1/chats/{chat}", httpapi.Handle(h.get))
	mux.HandleFunc("DELETE /api/v1/chats/{chat}", httpapi.Handle(h.delete))
	mux.HandleFunc("POST /api/v1/chats/{chat}/files", httpapi.Handle(h.upload))
	mux.HandleFunc("GET /api/v1/chats/{chat}/files/{file}", httpapi.Handle(h.download))
	mux.HandleFunc("DELETE /api/v1/chats/{chat}/files/{file}", httpapi.Handle(h.deleteFile))
	mux.HandleFunc("POST /api/v1/chats/{chat}/messages", httpapi.Handle(h.send))
	mux.HandleFunc("POST /api/v1/chats/{chat}/messages/{message}/retry", httpapi.Handle(h.retry))
}

func (h *Handler) list(w http.ResponseWriter, r *http.Request) error {
	who, err := h.actor(r)
	if err != nil {
		return err
	}
	chats, err := h.service.List(who)
	if err == nil {
		httpapi.WriteJSON(w, 200, chats)
	}
	return err
}

func (h *Handler) create(w http.ResponseWriter, r *http.Request) error {
	who, err := h.actor(r)
	if err != nil {
		return err
	}
	summary, err := h.service.Create(who)
	if err == nil {
		httpapi.WriteJSON(w, 201, summary)
	}
	return err
}

func (h *Handler) get(w http.ResponseWriter, r *http.Request) error {
	who, err := h.actor(r)
	if err != nil {
		return err
	}
	chat, err := h.service.Get(who, r.PathValue("chat"))
	if err == nil {
		httpapi.WriteJSON(w, 200, chat)
	}
	return err
}

func (h *Handler) delete(w http.ResponseWriter, r *http.Request) error {
	who, err := h.actor(r)
	if err != nil {
		return err
	}
	if err := h.service.Delete(who, r.PathValue("chat")); err != nil {
		return err
	}
	w.WriteHeader(204)
	return nil
}

func (h *Handler) deleteAll(w http.ResponseWriter, r *http.Request) error {
	who, err := h.actor(r)
	if err != nil {
		return err
	}
	if err := h.service.DeleteAll(who); err != nil {
		return err
	}
	w.WriteHeader(204)
	return nil
}

func (h *Handler) send(w http.ResponseWriter, r *http.Request) error {
	who, err := h.actor(r)
	if err != nil {
		return err
	}
	var input SendInput
	if err := httpapi.ReadJSON(w, r, &input); err != nil {
		return err
	}
	id, err := h.service.Send(who, r.PathValue("chat"), input)
	if err == nil {
		httpapi.WriteJSON(w, 202, map[string]string{"messageId": id})
	}
	return err
}

func (h *Handler) retry(w http.ResponseWriter, r *http.Request) error {
	who, err := h.actor(r)
	if err != nil {
		return err
	}
	id, err := h.service.Retry(who, r.PathValue("chat"), r.PathValue("message"))
	if err == nil {
		httpapi.WriteJSON(w, 202, map[string]string{"messageId": id})
	}
	return err
}

func (h *Handler) upload(w http.ResponseWriter, r *http.Request) error {
	who, err := h.actor(r)
	if err != nil {
		return err
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxFileBytes+(64<<10))
	reader, err := r.MultipartReader()
	if err != nil {
		return apperr.New(apperr.InvalidInput, "Ожидается файл в поле file.")
	}
	part, err := reader.NextPart()
	if err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			return err
		}
		return apperr.New(apperr.InvalidInput, "Ожидается файл в поле file.")
	}
	defer r.Body.Close()
	if part.FormName() != "file" || part.FileName() == "" {
		return apperr.New(apperr.InvalidInput, "Ожидается файл в поле file.")
	}
	payload, err := io.ReadAll(io.LimitReader(part, maxFileBytes+1))
	if err != nil {
		return err
	}
	file, err := h.service.Upload(who, r.PathValue("chat"), part.FileName(), part.Header.Get("Content-Type"), payload)
	if err == nil {
		httpapi.WriteJSON(w, 201, file)
	}
	return err
}

func (h *Handler) download(w http.ResponseWriter, r *http.Request) error {
	who, err := h.actor(r)
	if err != nil {
		return err
	}
	file, payload, err := h.service.Attachment(who, r.PathValue("chat"), r.PathValue("file"))
	if err != nil {
		return err
	}
	w.Header().Set("Content-Type", file.MIME)
	w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": file.Name}))
	http.ServeContent(w, r, file.Name, time.Time{}, bytes.NewReader(payload))
	return nil
}

func (h *Handler) deleteFile(w http.ResponseWriter, r *http.Request) error {
	who, err := h.actor(r)
	if err != nil {
		return err
	}
	if err := h.service.DeleteAttachment(who, r.PathValue("chat"), r.PathValue("file")); err != nil {
		return err
	}
	w.WriteHeader(204)
	return nil
}
