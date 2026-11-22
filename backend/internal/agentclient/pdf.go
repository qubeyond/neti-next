package agentclient

import (
	"cmp"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/qubeyond/neti-next/backend/internal/apperr"
	"github.com/qubeyond/neti-next/backend/internal/chat"
)

type pdfLimits struct {
	pages        int
	minTextChars int
	timeout      time.Duration
}

func loadPDFLimits() (pdfLimits, error) {
	read := func(name, fallback string) (int, error) {
		value, err := strconv.Atoi(cmp.Or(os.Getenv(name), fallback))
		if err != nil || value <= 0 {
			return 0, fmt.Errorf("%s должен быть положительным целым числом", name)
		}
		return value, nil
	}
	pages, err := read("BACKEND_PDF_MAX_PAGES", "3")
	if err != nil {
		return pdfLimits{}, err
	}
	text, err := read("BACKEND_PDF_MIN_TEXT_CHARS", "20")
	if err != nil {
		return pdfLimits{}, err
	}
	seconds, err := read("BACKEND_PDF_TIMEOUT_SECONDS", "15")
	if err != nil {
		return pdfLimits{}, err
	}
	return pdfLimits{pages: pages, minTextChars: text, timeout: time.Duration(seconds) * time.Second}, nil
}

func readPreparedFile(path string) ([]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, chat.MaxFileBytes+1))
	if len(data) > chat.MaxFileBytes {
		return nil, errors.New("Подготовленная страница превышает лимит.")
	}
	return data, err
}

func pdfPageCount(info []byte) int {
	for _, line := range strings.Split(string(info), "\n") {
		if count, ok := strings.CutPrefix(strings.TrimSpace(line), "Pages:"); ok {
			value, err := strconv.Atoi(strings.TrimSpace(count))
			if err == nil {
				return value
			}
			break
		}
	}
	return 0
}

func (service inferenceClient) preparePDF(ctx context.Context, media chat.Attachment, data []byte) (recognition, error) {
	dir, err := os.MkdirTemp("", "neti-pdf-")
	if err != nil {
		return recognition{}, errors.New("Не удалось подготовить PDF.")
	}
	defer os.RemoveAll(dir)
	source := filepath.Join(dir, "input.pdf")
	if err := os.WriteFile(source, data, 0600); err != nil {
		return recognition{}, errors.New("Не удалось подготовить PDF.")
	}
	parseContext, cancel := context.WithTimeout(ctx, service.pdf.timeout)
	defer cancel()
	info, err := exec.CommandContext(parseContext, "pdfinfo", source).Output()
	count := pdfPageCount(info)
	if err != nil || count <= 0 {
		return recognition{}, apperr.HTTP(422, "Не удалось прочитать PDF.")
	}

	limit := min(count, service.pdf.pages)
	output := filepath.Join(dir, "text.txt")
	lastPage := strconv.Itoa(limit)
	if err := exec.CommandContext(parseContext, "pdftotext", "-layout", "-l", lastPage, source, output).Run(); err != nil {
		return recognition{}, apperr.HTTP(422, "Не удалось извлечь текст PDF.")
	}
	raw, err := readPreparedFile(output)
	if err != nil {
		return recognition{}, errors.New("Не удалось прочитать извлечённый текст PDF.")
	}

	pages := strings.Split(string(raw), "\f")
	texts := make([]string, limit)
	scans := make(map[int][]byte)
	for index := range texts {
		if index < len(pages) {
			texts[index] = strings.TrimSpace(pages[index])
		}
		if utf8.RuneCountInString(texts[index]) >= service.pdf.minTextChars {
			continue
		}

		page := strconv.Itoa(index + 1)
		pattern := filepath.Join(dir, "page-%d.pdf")
		if err := exec.CommandContext(parseContext, "pdfseparate", "-f", page, "-l", page, source, pattern).Run(); err != nil {
			return recognition{}, apperr.HTTP(422, "Не удалось выделить страницу PDF для OCR.")
		}
		scans[index], err = readPreparedFile(filepath.Join(dir, "page-"+page+".pdf"))
		if err != nil {
			return recognition{}, errors.New("Не удалось прочитать страницу PDF для OCR.")
		}
	}
	cancel()

	for index, text := range texts {
		if scan := scans[index]; scan != nil {
			result, err := service.recognize(ctx, media, scan)
			if err != nil {
				return recognition{}, err
			}
			text = result.Text
		}
		texts[index] = fmt.Sprintf("Страница %d\n%s", index+1, text)
	}
	return recognition{Text: strings.Join(texts, "\n\n"), Truncated: count > limit}, nil
}
