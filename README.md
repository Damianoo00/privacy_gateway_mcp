# Privacy MCP Gateway

Lokalne proxy prywatności MCP: **Codex → nasze proxy → skonfigurowane serwery MCP**.
Proxy filtruje odpowiedzi i wykonuje routing, a backendy instaluje się osobno.
Dockerfile nie zawiera zewnętrznych serwerów Office ani filesystem.

```console
docker compose up -d --build
```

Endpoint: `http://127.0.0.1:8000/mcp` (Streamable HTTP). Domyślnie proxy startuje bez
serwerów i dokumentów; dodaje się je w pliku konfiguracyjnym bez zmiany obrazu.
[Uruchomienie Docker](docs/DOCKER_HTTP.md).

## Instalacja przez skilla

[private-proxy-setup](skills/private-proxy-setup/SKILL.md) prowadzi przez instalację
proxy i backendów, test filtrowania oraz migrację centralnego `config.toml` Codexa.
Helper najpierw przygotowuje plan i kandydata, następnie robi dokładny backup przed
nadpisaniem. Nie stosuje zmian przy samym tworzeniu/instalacji skilla.

Skill wyłącza bezpośrednie integracje i narzędzia wykonawcze, ustawia profil odmowy
odczytu i wymaga weryfikacji po restarcie. Nie zastępuje izolacji systemowej Codexa.
Samo `read-only` nie blokuje czytania dokumentów.

## Konfiguracja serwerów

JSON i TOML obsługują `mcp_servers` z własnymi nazwami, podobnie jak konfiguracja
Codexa. Każdy serwer ma dodatkowo `profile` i listę `enabled_tools`. Przykłady:

```toml
[mcp_servers.word_local]
profile = "docx"
url = "http://private-backend:8102/mcp"
bearer_token_env = "WORD_BACKEND_TOKEN"
enabled_tools = ["read_document", "edit_paragraphs"]
```

Dla natywnego proxy serwer może zamiast `url` mieć `command`, `args` i opcjonalne
`env`. Komendy hosta nie są wykonywalne w kontenerze proxy. Serwery stdio można
uruchamiać poza nim za pomocą `privacy-gateway bridge`, z uwierzytelnianiem HTTP.
Zewnętrzny backend musi widzieć tę samą kopię roboczą; `workspace_path` pozwala
mapować jej ścieżkę na host Windows/Linux/macOS.

Przykładowe pliki: [kontener TOML](config.container.example.toml),
[kontener JSON](config.container.example.json),
[dotychczasowa konfiguracja natywna JSON](config.proxy.example.json).
Stara mapa `backends` jest nadal odczytywana; nie łącz jej z `mcp_servers`.

| Profil | Odczyt | Edycja |
| --- | --- | --- |
| docx | read_document | edit_paragraphs |
| excel | excel_describe_sheets, excel_read_sheet | excel_write_to_sheet |
| pptx | pptx_get_info, pptx_read_slide | pptx_edit_text |
| filesystem | read_text_file | edit_file |

Nazwy publiczne mają prefiks nazwy serwera, np. `word_local__read_document`.
Lista komend jest sprawdzana ze schematami odkrytymi na backendzie. Nieobsługiwane
profile i narzędzia są odrzucane; nie ma dowolnego, niesprawdzonego passthrough.
Dodanie konfiguracji wymaga restartu proxy.

## Schemat ochrony dokumentów

Klient podaje bezpośrednio bezwzględną ścieżkę `path`, np.
`files__read_text_file({"path": "/sources/note.txt"})`. Nie ma rejestracji
dokumentów ani kroku `resolve_document`. Dostępne są pliki widoczne dla procesu
proxy z uwzględnieniem uprawnień systemowych i mountów kontenera. Prywatny workspace,
symlinki, hardlinki i pliki przekraczające limit rozmiaru są odrzucane.
Stare pola `documents`, `auto_documents` i `source_roots` są ignorowane;
nie ograniczają dostępu. Nowe pliki nie wymagają restartu.
Wyniki tekstowe i zagnieżdżony JSON przechodzą przez Presidio oraz reguły lokalne.
PII zastępują odwracalne tokeny `[[PG:…]]` zapisane w szyfrowanym rejestrze. Token
można odtworzyć przy edycji tylko dla dokumentu, dla którego go wcześniej wydano.
Klucz pozostaje w prywatnym wolumenie; utrata klucza uniemożliwia odczyt rejestru.

Edycja wymaga `expected_sha256` z ostatniego odczytu. Backend pracuje na prywatnej
kopii, a proxy sprawdza źródło przed atomowym zastąpieniem. Błąd lub timeout nie
uprawnia do automatycznego ponowienia zapisu. Po zapisie ponownie odczytaj dokument.
DOCX korzysta z edycji bez nowych śledzonych rewizji. Excel zwraca nazwy arkuszy jako tokeny z `excel_describe_sheets`;
przekaż wybrany token w `sheet`. Formuły nie są dopuszczone. Obrazy, audio, zasoby binarne, sampling
oraz niezatwierdzone narzędzia nie są wystawiane. PDF i OCR nie są obsługiwane.

Oryginały na dysku nadal mogą zawierać dane wrażliwe. Detekcja nie daje gwarancji
znalezienia każdego przypadku; sprawdź wybrany model na własnych danych. Domyślny
obraz ma model angielski; dla polskiego dobuduj zgodny model i ustaw język/encje.
Izolacja hosta i uprawnień backendów pozostają konieczne.

## Testy

```console
python3 -m unittest discover -s tests -v
```

Testy obejmują routing HTTP/stdio, profilowe reguły ochrony, tokeny, konflikt zapisu,
uwierzytelnianie bridge i migrację konfiguracji na plikach tymczasowych. Zewnętrzne
serwery testuj po ich instalacji według [skilla](skills/private-proxy-setup/SKILL.md).
