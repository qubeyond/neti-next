package account

import (
	"bytes"
	"errors"
	"net/mail"
	"strings"
	"unicode/utf8"

	"github.com/qubeyond/neti-next/backend/internal/apperr"
	"github.com/qubeyond/neti-next/backend/internal/identity"
	"golang.org/x/crypto/bcrypt"
)

type SessionEvents interface {
	Disconnect(func(identity.Actor) bool)
}

type Service struct {
	repo   Repository
	events SessionEvents
}

func NewService(repo Repository, events SessionEvents) *Service {
	return &Service{repo: repo, events: events}
}

func goodPassword(password string) bool {
	return len(password) <= 72 && utf8.RuneCountInString(password) >= 8
}

func goodName(name string) bool {
	return name != "" && utf8.RuneCountInString(name) <= 60
}

func (s *Service) Register(name, email, password string) (Profile, string, error) {
	email = strings.ToLower(strings.TrimSpace(email))
	name = strings.TrimSpace(name)
	address, err := mail.ParseAddress(email)
	if err != nil || address.Address != email || len(email) > 254 {
		return Profile{}, "", apperr.New(apperr.InvalidInput, "Введите корректный адрес почты.")
	}
	if !goodName(name) {
		return Profile{}, "", apperr.New(apperr.InvalidInput, "Введите имя длиной до 60 символов.")
	}
	if !goodPassword(password) {
		return Profile{}, "", apperr.New(apperr.InvalidInput, "Пароль должен содержать от 8 символов и занимать до 72 байт.")
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		return Profile{}, "", err
	}
	profile := Profile{ID: identity.NewID(), Email: email, Name: name, Theme: "light"}
	token := identity.NewID()
	if err := s.repo.Register(profile, hash, token); err != nil {
		return Profile{}, "", err
	}
	return profile, token, nil
}

func (s *Service) Login(email, password string) (Profile, string, error) {
	invalid := apperr.New(apperr.Unauthorized, "Неверная почта или пароль.")
	if !goodPassword(password) {
		return Profile{}, "", invalid
	}
	snapshot, err := s.repo.FindCredentials(strings.ToLower(strings.TrimSpace(email)))
	if errors.Is(err, ErrInvalidCredentials) {
		return Profile{}, "", invalid
	}
	if err != nil {
		return Profile{}, "", err
	}
	if bcrypt.CompareHashAndPassword(snapshot.PasswordHash, []byte(password)) != nil {
		return Profile{}, "", invalid
	}
	token := identity.NewID()
	profile, err := s.repo.StartSession(snapshot.Profile.ID, snapshot.PasswordHash, token)
	if errors.Is(err, ErrInvalidCredentials) {
		return Profile{}, "", invalid
	}
	return profile, token, err
}

func (s *Service) Logout(token string) error {
	key, err := s.repo.EndSession(token)
	if err == nil {
		s.events.Disconnect(func(who identity.Actor) bool { return who.Token == key })
	}
	return err
}

func (s *Service) NewGuest() (string, error) {
	return s.repo.NewGuest()
}

func (s *Service) EndGuest(who identity.Actor) error {
	if !who.Guest {
		return apperr.New(apperr.Forbidden, "Требуется гостевая сессия.")
	}
	s.repo.EndGuest(who)
	s.events.Disconnect(func(subscriber identity.Actor) bool { return subscriber == who })
	return nil
}

func (s *Service) Authenticate(token string, guest bool) (identity.Actor, error) {
	return s.repo.Authenticate(token, guest)
}

func (s *Service) SessionAlive(token string, who identity.Actor) bool {
	if who.Guest {
		return s.repo.GuestAlive(who)
	}
	_, err := s.repo.Authenticate(token, false)
	return err == nil
}

func (s *Service) Profile(who identity.Actor) (Profile, error) {
	creds, err := s.repo.GetCredentials(who)
	return creds.Profile, err
}

type ProfileUpdate struct {
	Name            *string `json:"name"`
	Theme           *string `json:"theme"`
	Instructions    *string `json:"instructions"`
	CurrentPassword string  `json:"currentPassword"`
	Password        string  `json:"password"`
}

func (s *Service) UpdateProfile(who identity.Actor, input ProfileUpdate) (Profile, error) {
	if input.Name != nil {
		name := strings.TrimSpace(*input.Name)
		if !goodName(name) {
			return Profile{}, apperr.New(apperr.InvalidInput, "Введите имя длиной до 60 символов.")
		}
		input.Name = &name
	}
	if input.Theme != nil && *input.Theme != "light" && *input.Theme != "dark" {
		return Profile{}, apperr.New(apperr.InvalidInput, "Выберите светлую или тёмную тему.")
	}
	if input.Instructions != nil && len([]rune(*input.Instructions)) > 2000 {
		return Profile{}, apperr.New(apperr.InvalidInput, "Инструкция должна быть не длиннее 2000 символов.")
	}
	var oldHash, newHash []byte
	if input.Password != "" {
		if !goodPassword(input.Password) {
			return Profile{}, apperr.New(apperr.InvalidInput, "Пароль должен содержать от 8 символов и занимать до 72 байт.")
		}
		snapshot, err := s.repo.GetCredentials(who)
		if err != nil {
			return Profile{}, err
		}
		oldHash = snapshot.PasswordHash
		if bcrypt.CompareHashAndPassword(oldHash, []byte(input.CurrentPassword)) != nil {
			return Profile{}, apperr.New(apperr.Forbidden, "Текущий пароль введён неверно.")
		}
		newHash, err = bcrypt.GenerateFromPassword([]byte(input.Password), bcrypt.DefaultCost)
		if err != nil {
			return Profile{}, err
		}
	}
	profile, err := s.repo.ChangeProfile(who, func(profile *Profile, currentHash []byte) ([]byte, error) {
		if newHash != nil && !bytes.Equal(oldHash, currentHash) {
			return nil, apperr.New(apperr.Conflict, "Пароль уже изменился. Попробуйте ещё раз.")
		}
		if input.Name != nil {
			profile.Name = *input.Name
		}
		if input.Theme != nil {
			profile.Theme = *input.Theme
		}
		if input.Instructions != nil {
			profile.Instructions = *input.Instructions
		}
		return newHash, nil
	})
	if err == nil && newHash != nil {
		s.events.Disconnect(func(subscriber identity.Actor) bool {
			return subscriber.Owner == who.Owner && subscriber.Token != who.Token
		})
	}
	return profile, err
}

func (s *Service) DeleteProfile(who identity.Actor) error {
	err := s.repo.DeleteProfile(who)
	if err == nil {
		s.events.Disconnect(func(subscriber identity.Actor) bool { return subscriber.Owner == who.Owner })
	}
	return err
}
