package agentclient

import (
	"bytes"
	"context"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/qubeyond/neti-next/backend/internal/chat"
)

type ocrLine struct {
	key   string
	text  string
	left  int
	top   int
	right int
	end   int
}

func normalizeOCR(text string) string {
	words := strings.FieldsFunc(text, func(char rune) bool {
		return !unicode.IsLetter(char) && !unicode.IsNumber(char)
	})
	return strings.ToLower(strings.Join(words, " "))
}

func ocrLines(tsv []byte, width, height int) []ocrLine {
	var lines []ocrLine
	for _, row := range strings.Split(string(tsv), "\n") {
		fields := strings.SplitN(row, "\t", 12)
		if len(fields) != 12 || fields[0] != "5" || strings.TrimSpace(fields[11]) == "" {
			continue
		}
		box := [4]int{}
		valid := true
		for index, field := range fields[6:10] {
			value, err := strconv.Atoi(field)
			if err != nil || value < 0 || value > max(width, height) {
				valid = false
				break
			}
			box[index] = value
		}
		left, top, right, end := box[0], box[1], box[0]+box[2], box[1]+box[3]
		if !valid || right <= left || end <= top || right > width || end > height {
			continue
		}
		key := strings.Join(fields[1:5], "/")
		if len(lines) == 0 || lines[len(lines)-1].key != key {
			lines = append(lines, ocrLine{key, fields[11], left, top, right, end})
			continue
		}
		line := &lines[len(lines)-1]
		line.text += " " + fields[11]
		line.left, line.top = min(line.left, left), min(line.top, top)
		line.right, line.end = max(line.right, right), max(line.end, end)
	}
	return lines
}

func findImageRegion(lines []ocrLine, quote string, width, height int) *chat.ImageRegion {
	text := normalizeOCR(quote)
	if len([]rune(text)) < 8 {
		return nil
	}
	var matched *chat.ImageRegion
	for _, line := range lines {
		if !strings.Contains(" "+normalizeOCR(line.text)+" ", " "+text+" ") {
			continue
		}
		if matched != nil {
			return nil
		}
		matched = &chat.ImageRegion{
			X: float64(line.left) / float64(width), Y: float64(line.top) / float64(height),
			Width: float64(line.right-line.left) / float64(width), Height: float64(line.end-line.top) / float64(height),
		}
	}
	return matched
}

func addImageRegions(parent context.Context, photo *recognizedImage, citations []chat.Citation) {
	if photo == nil || len(citations) == 0 {
		return
	}
	config, _, err := image.DecodeConfig(bytes.NewReader(photo.data))
	if err != nil || config.Width < 1 || config.Height < 1 || config.Width > 5000 || config.Height > 5000 {
		return
	}
	ctx, cancel := context.WithTimeout(parent, 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "tesseract", "stdin", "stdout", "-l", "rus+eng", "tsv")
	cmd.Env = append(os.Environ(), "OMP_THREAD_LIMIT=1")
	cmd.Stdin = bytes.NewReader(photo.data)
	tsv, err := cmd.Output()
	if err != nil {
		return
	}
	lines := ocrLines(tsv, config.Width, config.Height)
	for index := range citations {
		item := &citations[index]
		if item.AttachmentID == photo.ID {
			item.Region = findImageRegion(lines, item.Quote, config.Width, config.Height)
		}
	}
}
