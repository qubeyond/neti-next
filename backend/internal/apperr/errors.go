package apperr

type Kind int

const (
	Unauthorized Kind = iota + 1
	Forbidden
	NotFound
	Conflict
	TooLarge
	InvalidInput
	LimitExceeded
)

type Error struct {
	Kind    Kind
	Message string
}

func (err Error) Error() string { return err.Message }

func New(kind Kind, message string) error {
	return Error{Kind: kind, Message: message}
}
