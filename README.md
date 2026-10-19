# Privacy MCP Gateway

## Docker + Streamable HTTP na Windows, Linux i macOS

Nowy sposób uruchamiania opisuje [instrukcja Docker Compose](docs/DOCKER_HTTP.md).
Bramka działa niezależnie od klienta MCP pod `http://localhost:8000/mcp`.
Compose udostępnia port wyłącznie na lokalnym komputerze i przechowuje klucz oraz
bazę w trwałym wolumenie. Publicznym transportem bramki jest wyłącznie Streamable HTTP.
Kontener używa Linuksa na wszystkich trzech systemach; nie jest to natywny port Pythona na Windows.

## Proxy MCP

`serve` udostępnia wybrane lokalne narzędzia istniejących serwerów MCP dla zarejestrowanych dokumentów DOCX, XLSX, PPTX oraz TXT/MD/CSV/HTML. Konfigurację operatora pokazuje [config.proxy.example.json](config.proxy.example.json). Zastąp ścieżki przykładowe rzeczywistymi, absolutnymi ścieżkami do lokalnie zainstalowanych i przypiętych wersji serwerów. Nie uruchamiaj `npx --yes` ani instalacji z sieci przy starcie bramki. Podane w przykładzie ścieżki instalacji i pliki wykonywalne trzeba dostosować do przypiętych wersji; bramka sprawdza obecność i wymagany schemat narzędzi przy starcie. Python MCP SDK wymaga wersji 1.26–1.x.

```sh
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/python -m spacy download en_core_web_lg
cp config.proxy.example.json config.proxy.json
chmod 600 config.proxy.json
```

Zainstaluj osobno lokalne wersje [knorq DOCX](https://github.com/knorq-ai/docx-mcp-server), [negokaz Excel](https://github.com/negokaz/excel-mcp-server), [knorq PPTX](https://github.com/knorq-ai/pptx-mcp-server) i [MCP filesystem](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem). Przykład zakłada zbudowany plik Node dla DOCX i filesystem, plik wykonywalny Go dla Excela oraz lokalny skrypt CLI Pythona dla PPTX. Ustaw ścieżki do tych konkretnych instalacji w `config.proxy.json`, a także dokumenty, katalog źródeł, prywatny workspace i aliasy arkuszy.

Usuń nieużywane backendy i dokumenty z konfiguracji. Przykład używa modelu angielskiego; dla polskiego ustaw `language: pl`, zainstalowany lokalnie model spaCy oraz encje obsługiwane w tym języku. Brak modelu lub wymaganego detektora blokuje start.

```sh
.venv/bin/privacy-gateway init-key /secure/path/gateway.key
.venv/bin/privacy-gateway serve --config config.proxy.json --key-file /secure/path/gateway.key
```

`documents` jest listą nadaną przez operatora. Każdy publiczny `doc_…` wskazuje dokładnie jeden istniejący plik z `source_roots`. Klient MCP nie otrzymuje ścieżki. Dla Excela `sheets` przypisuje publiczne aliasy wszystkim arkuszom, które mają być dostępne. Każdy backend ma jawną listę `tools`; wbudowane profile dopuszczają tylko:

| Backend | Odczyt | Edycja |
| --- | --- | --- |
| `docx` | `read_document` | `edit_paragraphs` |
| `excel` | `excel_describe_sheets`, `excel_read_sheet` | `excel_write_to_sheet` |
| `pptx` | `pptx_get_info`, `pptx_read_slide` | `pptx_edit_text` |
| `filesystem` | `read_text_file` | `edit_file` |

Publiczne nazwy mają prefiks, np. `docx__read_document`. Wejście ma `document_id` i zatwierdzone parametry narzędzia; `file_path`/`path`/`fileAbsolutePath` są wstrzykiwane lokalnie. Dla edycji podaj `expected_sha256` z ostatniego odczytu; `document_id` i SHA-256 są zwracane także w osobnym bloku tekstowym dla klientów bez obsługi danych strukturalnych. Konflikt wersji blokuje zapis. DOCX: `edits=[{"paragraph_index":0,"new_text":"..."}]` albo kotwica `anchor`; PPTX: `slide_index`, `shape_index`, `text` i opcjonalny `paragraph_index`; XLSX: alias `sheet`, zakres `range`, dwuwymiarowe `values` bez formuł; pliki tekstowe: `edits=[{"oldText":"...","newText":"..."}]`. Po zapisie wynik zwraca nowy SHA-256. Sprawdź wynik ponownym odczytem. Zapisy są wykonywane na prywatnej kopii, a po ponownym sprawdzeniu źródła atomowo podmieniany jest pojedynczy plik. Nie powtarzaj automatycznie zapisu po błędzie lub timeoutcie. Inni edytorzy powinni nie zmieniać plików równolegle; blokady obejmują tylko wywołania tej bramki.

Wyniki tekstowe i wartości zagnieżdżonego JSON są filtrowane; błędy backendu są zastępowane stałym komunikatem. Presidio oraz wzorce lokalne zamieniają wykryte dane na odwracalne tokeny `[[PG:…]]` przechowywane w szyfrowanym rejestrze. Identyczny fragment dostaje ten sam token w `access_scope`, ale odtworzenie przy edycji wymaga, by token wcześniej wydano dla konkretnego dokumentu. Nieznane tokeny i tokeny wydane tylko dla innego dokumentu blokują zapis. Ścieżki, adresy URL i długie identyfikatory liczbowe w odpowiedziach dostają tokeny; całkowite liczby w JSON o długości 9–19 cyfr stają się stringiem tokenu, który przy wstawieniu jako cała komórka jest przywracany jako liczba. Formuły nie są dopuszczone. Wyniki binarne, obrazy, audio, zasoby MCP, linki do zasobów, prompty i sampling nie są wystawiane. Detekcja PII nie daje gwarancji znalezienia każdego przypadku, więc przetestuj własne dane i model przed zastosowaniem produkcyjnym.

Backendy dostają ścieżki tylko do kopii roboczych. Uruchamiaj je bez dostępu do sieci, na koncie z minimalnymi uprawnieniami; skonfigurowany serwer filesystem musi mieć na swojej liście dozwolonych katalogów prywatny `workspace`, ponieważ tam powstają kopie. Bramka nie kontroluje kodu wykonywanego wewnątrz zewnętrznego backendu, który może mieć uprawnienia systemowe do oryginałów. Klient nie powinien mieć bezpośredniego dostępu do tych backendów ani do plików źródłowych.

Wersja 0.2 nie jest jeszcze potwierdzona testem z rzeczywistymi serwerami Office w tym środowisku. Testy jednostkowe używają syntetycznego backendu; przed produkcyjną edycją dokumentów trzeba przypiąć i uruchomić backendi oraz wykonać próbę odczyt–edycja–ponowny odczyt na kopiach reprezentatywnych plików, w tym polskich danych, tabel, rewizji i nazw arkuszy.

## Konfiguracja i dostęp

Dokumenty rejestruje operator w liście `documents` w konfiguracji; nie ma osobnej
komendy `register`. Bramka działa wyłącznie jako proxy przez Streamable HTTP.
Uruchom ją jako osobny proces i połącz klienta MCP z `http://localhost:8000/mcp`.
Dawne polecenie `serve-proxy` zastępuje `serve`.

`init-key` tworzy 32 losowe bajty w pliku z uprawnieniami `0600`. Utrata klucza
uniemożliwia odczyt szyfrowanego rejestru tokenów. W Dockerze klucz oraz workspace
pozostają w trwałym wolumenie. Nie usuwaj go przy aktualizacji.

Dla polskiego ustaw `language: pl`, zgodny lokalny model spaCy i odpowiednie encje,
np. `PERSON`, `LOCATION`, `PESEL`, `NIP`, `REGON`. Wzorce numerów nie sprawdzają sum
kontrolnych. Zweryfikuj detekcję na własnych danych. PDF i OCR nie są obsługiwane.

HTTP nie ma uwierzytelniania: domyślny nasłuch jest lokalny. Nie udostępniaj portu
publicznie. Kontener proxy potrzebuje zapisu w katalogu źródłowym do podmiany
edytowanych dokumentów; pozostałe poufne katalogi powinny być niedostępne.
MCP nie ogranicza uprawnień innych narzędzi klienta.

## Testy

```sh
python3 -m unittest discover -s tests -v
```

Testy używają deterministycznego detektora i syntetycznego backendu MCP.
Produkcja wymaga Presidio i lokalnego modelu. Testy nie potwierdzają zgodności
z rzeczywistymi serwerami Office ani jakości detekcji na danych użytkownika.
