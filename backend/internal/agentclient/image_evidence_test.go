package agentclient

import "testing"

func TestImageRegionRequiresUniqueOCRMatch(t *testing.T) {
	tsv := []byte("5\t1\t1\t1\t1\t1\t20\t10\t80\t20\t97\tСправка\n" +
		"5\t1\t1\t1\t1\t2\t110\t10\t30\t20\t98\t204\n")
	lines := ocrLines(tsv, 200, 100)
	region := findImageRegion(lines, "Справка 204.", 200, 100)
	if region == nil || region.X != 0.1 || region.Width != 0.6 {
		t.Fatalf("Неверная область: %+v", region)
	}
	if findImageRegion(lines, "Справка 205", 200, 100) != nil {
		t.Fatal("Нельзя подсвечивать приблизительное совпадение")
	}
	lines = append(lines, lines[0])
	if findImageRegion(lines, "Справка 204", 200, 100) != nil {
		t.Fatal("Нельзя выбирать одну из двух одинаковых строк")
	}
}
