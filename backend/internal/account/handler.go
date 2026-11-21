package account

import (
	"net/http"
	"strings"

	"github.com/qubeyond/neti-next/backend/internal/httpapi"
	"github.com/qubeyond/neti-next/backend/internal/identity"
)

const SessionCookie = "neti_session"

type Handler struct {
	service       *Service
	secureCookies bool
}

func NewHandler(service *Service, secureCookies bool) *Handler {
	return &Handler{service: service, secureCookies: secureCookies}
}

func (h *Handler) RegisterRoutes(mux *http.ServeMux) {
	mux.HandleFunc("POST /api/v1/auth/register", httpapi.Handle(h.register))
	mux.HandleFunc("POST /api/v1/auth/login", httpapi.Handle(h.login))
	mux.HandleFunc("POST /api/v1/auth/logout", httpapi.Handle(h.logout))
	mux.HandleFunc("POST /api/v1/auth/guest", httpapi.Handle(h.createGuest))
	mux.HandleFunc("DELETE /api/v1/auth/guest", httpapi.Handle(h.deleteGuest))
	mux.HandleFunc("GET /api/v1/profile", httpapi.Handle(h.getProfile))
	mux.HandleFunc("PATCH /api/v1/profile", httpapi.Handle(h.updateProfile))
	mux.HandleFunc("DELETE /api/v1/profile", httpapi.Handle(h.deleteProfile))
}

type credentialsInput struct {
	Name     string `json:"name"`
	Email    string `json:"email"`
	Password string `json:"password"`
}

func (h *Handler) cookie(w http.ResponseWriter, r *http.Request, value string, maxAge int) {
	http.SetCookie(w, &http.Cookie{Name: SessionCookie, Value: value, Path: "/api/v1",
		HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: h.secureCookies || r.TLS != nil, MaxAge: maxAge})
}

func (h *Handler) account(r *http.Request) (identity.Actor, error) {
	cookie, err := r.Cookie(SessionCookie)
	if err != nil {
		return identity.Actor{}, identity.ErrSessionEnded
	}
	return h.service.Authenticate(cookie.Value, false)
}

func (h *Handler) Actor(r *http.Request) (identity.Actor, error) {
	if header := r.Header.Get("Authorization"); header != "" {
		value, found := strings.CutPrefix(header, "Bearer ")
		if !found {
			return identity.Actor{}, identity.ErrSessionEnded
		}
		return h.service.Authenticate(value, true)
	}
	return h.account(r)
}

func (h *Handler) register(w http.ResponseWriter, r *http.Request) error {
	var input credentialsInput
	if err := httpapi.ReadJSON(w, r, &input); err != nil {
		return err
	}
	profile, token, err := h.service.Register(input.Name, input.Email, input.Password)
	if err != nil {
		return err
	}
	h.cookie(w, r, token, int(SessionLifetime.Seconds()))
	httpapi.WriteJSON(w, 201, profile)
	return nil
}

func (h *Handler) login(w http.ResponseWriter, r *http.Request) error {
	var input credentialsInput
	if err := httpapi.ReadJSON(w, r, &input); err != nil {
		return err
	}
	profile, token, err := h.service.Login(input.Email, input.Password)
	if err != nil {
		return err
	}
	h.cookie(w, r, token, int(SessionLifetime.Seconds()))
	httpapi.WriteJSON(w, 200, profile)
	return nil
}

func (h *Handler) logout(w http.ResponseWriter, r *http.Request) error {
	if cookie, err := r.Cookie(SessionCookie); err == nil {
		if err := h.service.Logout(cookie.Value); err != nil {
			return err
		}
	}
	h.cookie(w, r, "", -1)
	w.WriteHeader(204)
	return nil
}

func (h *Handler) createGuest(w http.ResponseWriter, _ *http.Request) error {
	token, err := h.service.NewGuest()
	if err == nil {
		httpapi.WriteJSON(w, 201, map[string]string{"token": token})
	}
	return err
}

func (h *Handler) deleteGuest(w http.ResponseWriter, r *http.Request) error {
	who, err := h.Actor(r)
	if err != nil {
		return err
	}
	if err := h.service.EndGuest(who); err != nil {
		return err
	}
	w.WriteHeader(204)
	return nil
}

func (h *Handler) getProfile(w http.ResponseWriter, r *http.Request) error {
	who, err := h.account(r)
	if err != nil {
		return err
	}
	profile, err := h.service.Profile(who)
	if err == nil {
		httpapi.WriteJSON(w, 200, profile)
	}
	return err
}

func (h *Handler) updateProfile(w http.ResponseWriter, r *http.Request) error {
	who, err := h.account(r)
	if err != nil {
		return err
	}
	var input ProfileUpdate
	if err := httpapi.ReadJSON(w, r, &input); err != nil {
		return err
	}
	profile, err := h.service.UpdateProfile(who, input)
	if err == nil {
		httpapi.WriteJSON(w, 200, profile)
	}
	return err
}

func (h *Handler) deleteProfile(w http.ResponseWriter, r *http.Request) error {
	who, err := h.account(r)
	if err != nil {
		return err
	}
	if err := h.service.DeleteProfile(who); err != nil {
		return err
	}
	h.cookie(w, r, "", -1)
	w.WriteHeader(204)
	return nil
}
