package main

import (
	"context"
	"math"
	"strconv"
	"sync"
	"time"
)

type ownerQuota struct {
	window time.Time
	used   int
	active int
}

type activeTurn struct {
	owner  string
	chat   string
	cancel context.CancelFunc
}

type turnLimits struct {
	mu               sync.Mutex
	timeout          time.Duration
	perMinute        int
	concurrent       int
	globalConcurrent int
	quotas           map[string]*ownerQuota
	active           map[*runJob]activeTurn
	lastSweep        time.Time
}

func positiveSetting(name, fallback string) int {
	value, err := strconv.Atoi(setting(name, fallback))
	if err != nil || value < 1 || value > 3600 {
		value, _ = strconv.Atoi(fallback)
	}
	return value
}

func newTurnLimits() *turnLimits {
	return &turnLimits{
		timeout:          time.Duration(positiveSetting("BACKEND_TURN_TIMEOUT_SECONDS", "115")) * time.Second,
		perMinute:        positiveSetting("BACKEND_MAX_TURNS_PER_MINUTE", "12"),
		concurrent:       positiveSetting("BACKEND_MAX_ACTIVE_TURNS", "2"),
		globalConcurrent: positiveSetting("BACKEND_MAX_GLOBAL_TURNS", "16"),
		quotas:           make(map[string]*ownerQuota),
		active:           make(map[*runJob]activeTurn),
	}
}

func (limits *turnLimits) admit(job *runJob, id string) error {
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
	owner := job.who.owner
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
		return retryLater{problem(429, "Достигнут лимит запросов. Попробуйте позже."), max(1, retry)}
	}
	if quota.active >= limits.concurrent || len(limits.active) >= limits.globalConcurrent {
		return retryLater{problem(429, "Слишком много активных запросов. Попробуйте позже."), 1}
	}
	quota.active++
	quota.used++
	ctx, cancel := context.WithTimeout(context.WithValue(context.Background(), requestIDKey{}, id), limits.timeout)
	job.ctx = ctx
	limits.active[job] = activeTurn{owner, job.chat, cancel}
	return nil
}

func (limits *turnLimits) release(job *runJob) {
	limits.mu.Lock()
	defer limits.mu.Unlock()
	if turn, exists := limits.active[job]; exists {
		turn.cancel()
		limits.quotas[turn.owner].active--
		delete(limits.active, job)
	}
}

func (limits *turnLimits) cancel(owner, chat string) {
	limits.mu.Lock()
	defer limits.mu.Unlock()
	for _, turn := range limits.active {
		if turn.owner == owner && (chat == "" || turn.chat == chat) {
			turn.cancel()
		}
	}
}
