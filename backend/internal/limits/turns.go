package limits

import (
	"context"
	"math"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/qubeyond/neti-next/backend/internal/apperr"
	"github.com/qubeyond/neti-next/backend/internal/httpapi"
)

type ownerQuota struct {
	window time.Time
	used   int
	active int
}

type Permit struct {
	Context context.Context
}

type activeTurn struct {
	owner  string
	chat   string
	cancel context.CancelFunc
}

type Turns struct {
	mu               sync.Mutex
	timeout          time.Duration
	perMinute        int
	concurrent       int
	globalConcurrent int
	quotas           map[string]*ownerQuota
	active           map[*Permit]activeTurn
	lastSweep        time.Time
}

func PositiveSetting(name, fallback string) int {
	value, err := strconv.Atoi(os.Getenv(name))
	if err != nil || value < 1 || value > 3600 {
		value, _ = strconv.Atoi(fallback)
	}
	return value
}

func New() *Turns {
	return &Turns{
		timeout:          time.Duration(PositiveSetting("BACKEND_TURN_TIMEOUT_SECONDS", "115")) * time.Second,
		perMinute:        PositiveSetting("BACKEND_MAX_TURNS_PER_MINUTE", "12"),
		concurrent:       PositiveSetting("BACKEND_MAX_ACTIVE_TURNS", "2"),
		globalConcurrent: PositiveSetting("BACKEND_MAX_GLOBAL_TURNS", "16"),
		quotas:           make(map[string]*ownerQuota),
		active:           make(map[*Permit]activeTurn),
	}
}

func (limits *Turns) Admit(owner, chat, id string) (*Permit, error) {
	limits.mu.Lock()
	defer limits.mu.Unlock()
	t := time.Now()
	if t.Sub(limits.lastSweep) >= time.Minute {
		for owner, quota := range limits.quotas {
			if quota.active == 0 && t.Sub(quota.window) >= time.Minute {
				delete(limits.quotas, owner)
			}
		}
		limits.lastSweep = t
	}
	quota := limits.quotas[owner]
	if quota == nil {
		quota = &ownerQuota{}
		limits.quotas[owner] = quota
	}
	if t.Sub(quota.window) >= time.Minute {
		quota.window, quota.used = t, 0
	}
	if quota.used >= limits.perMinute {
		retry := int(math.Ceil(quota.window.Add(time.Minute).Sub(t).Seconds()))
		return nil, httpapi.Retry(apperr.HTTP(429, "Достигнут лимит запросов. Попробуйте позже."), max(1, retry))
	}
	if quota.active >= limits.concurrent || len(limits.active) >= limits.globalConcurrent {
		return nil, httpapi.Retry(apperr.HTTP(429, "Слишком много активных запросов. Попробуйте позже."), 1)
	}
	quota.active++
	quota.used++
	ctx, cancel := context.WithTimeout(httpapi.WithRequestID(context.Background(), id), limits.timeout)
	job := &Permit{Context: ctx}
	limits.active[job] = activeTurn{owner, chat, cancel}
	return job, nil
}

func (limits *Turns) Release(job *Permit) {
	limits.mu.Lock()
	defer limits.mu.Unlock()
	if turn, exists := limits.active[job]; exists {
		turn.cancel()
		limits.quotas[turn.owner].active--
		delete(limits.active, job)
	}
}

func (limits *Turns) Cancel(owner, chat string) {
	limits.mu.Lock()
	defer limits.mu.Unlock()
	for _, turn := range limits.active {
		if turn.owner == owner && (chat == "" || turn.chat == chat) {
			turn.cancel()
		}
	}
}
