# Private proxy: Docker + zewnętrzne serwery MCP

Obraz zawiera **tylko nasze proxy i model detekcji PII**. Zewnętrzne serwery MCP
instaluje się osobno i dodaje w konfiguracji, bez przebudowywania obrazu proxy.

## Uruchomienie proxy

```console
docker compose up -d --build
docker compose ps
docker compose exec gateway python -m privacy_gateway.smoke
```

Wymagany Docker z Compose v2 i kontenerami linuksowymi. Nie trzeba kopiować plików
na pierwszy start: domyślne `config.container.example.toml` ma pustą mapę serwerów
i listę dokumentów. Wynik `0 available tools` jest wtedy prawidłowy. Endpoint dla
Codexa: `http://127.0.0.1:8000/mcp` (Streamable HTTP).

Klucz jest tworzony automatycznie w trwałym `private_data`; nie jest nadpisywany.
Obraz nie zawiera demonstracyjnych dokumentów ani backendów DOCX/XLSX/PPTX/filesystem.
Sam stan `healthy` nie potwierdza routingu i filtrowania dokumentów.

## Dodanie serwerów

Najwygodniej wykonać instalację z przygotowanym
[skillem private-proxy-setup](../skills/private-proxy-setup/SKILL.md).

1. Skopiuj `.env.example` do `.env`, a `config.container.example.toml` do
   `config.container.toml`; ustaw `PG_CONFIG=./config.container.toml`.
2. Zainstaluj wybrane backendy poza obrazem proxy. W konfiguracji użyj `url` dla
   zewnętrznego MCP HTTP. Serwer stdio na hoście można wystawić za pomocą naszej
   osobnej komendy `bridge`; nie dodawaj jego adresu bezpośrednio do Codexa.
3. Ustaw `PG_SOURCES` na zatwierdzony katalog źródeł i `PG_WORKSPACE` na osobny,
   prywatny katalog roboczy hosta. UID 1000 musi móc w nim czytać i pisać. Backendy
   muszą widzieć te same kopie robocze; samo podanie adresu URL nie współdzieli plików.
4. Dodaj wpisy `mcp_servers`. Przykład:

```toml
[mcp_servers.files]
profile = "filesystem"
url = "http://host.docker.internal:8101/mcp"
bearer_token_env = "FILES_BACKEND_TOKEN"
workspace_path = "/home/operator/private-proxy-workspace"
enabled_tools = ["read_text_file", "edit_file"]

```

Wywołuj `files__read_text_file` z `path: "/sources/note.txt"`; rejestracja nie jest
potrzebna. Dodatkowe katalogi hosta muszą być zamontowane w kontenerze, aby proxy
mogło je odczytać.
Sekrety przekaż przez prywatny plik środowiska i `PG_BACKEND_ENV_FILE`; domyślnie
Compose używa pustego `backend-secrets.example.env`. Nie wpisuj wartości tokenów
do repozytorium. Narzędzia w Codexie będą miały nazwy np. `files__read_text_file`.

Przy zmianie zawartości konfiguracji zrestartuj proxy. Przy zmianie mountów,
zmiennych lub `.env` odtwórz kontener:

```console
docker compose up -d --force-recreate gateway
```

Lista wystawianych narzędzi pochodzi z konfiguracji i walidacji odkrytego schematu
backendu. Obsługiwane profile: DOCX, Excel, PPTX i filesystem. Dowolne inne narzędzia
wymagają profilu/polityki; bramka nie otwiera niesprawdzonego passthrough.

Szczegóły instalacji backendów, bridge, współdzielenia ścieżek Windows/Linux/macOS
oraz testów odczyt–edycja–ponowny odczyt są w
[instrukcji skilla](../skills/private-proxy-setup/references/servers.md).

## Codex i dostęp do dokumentów

Skill przygotowuje kandydata centralnego `config.toml`, następnie po weryfikacji
robi kopię oryginału i stosuje zmianę. Wyłącza bezpośrednie MCP oraz znane narzędzia
omijające proxy i ustawia profil odmowy dostępu do chronionych ścieżek. Wymagany
restart Codexa oraz test odmowy odczytu — `read-only` nadal pozwala czytać.

Konfiguracja użytkownika nie zastępuje izolacji systemowej. Dla twardej gwarancji
Codex powinien działać jako inne konto lub w środowisku bez mountów dokumentów,
workspace, klucza, danych backendów i gniazda Dockera. Udostępnij mu tylko endpoint
proxy. Instrukcje, kopie zapasowe i przywracanie opisano w
[procedurze migracji](../skills/private-proxy-setup/references/codex.md).

## Sieć, dane i autostart

Proxy publikuje port tylko na loopback hosta. HTTP proxy nie ma uwierzytelniania;
lokalne procesy mogą z niego korzystać. Nie udostępniaj go publicznie bez osobnego
uwierzytelniania/TLS. Klient w chmurze nie połączy się z Twoim localhost.

Host bridge przetwarza niesanitowane dane: wymaga tokenu również lokalnie i musi
być odseparowany od Codexa. Ogranicz dostęp backendów do współdzielonego workspace.
Standardowa sieć bridge kontenera pozwala na połączenia wychodzące; nie jest zaporą.

Na Windows/macOS włącz start Docker Desktop po zalogowaniu. Na Ubuntu skonfiguruj
start silnika Docker wraz z systemem. `restart: unless-stopped` uruchomi utworzony
kontener po starcie silnika, jeśli wcześniej nie zatrzymano go ręcznie.

Nie wykonuj `docker compose down -v`, jeśli chcesz zachować klucz, rejestr i dane.
`private_data` zawiera klucz, a `work_data` prywatny workspace i szyfrowany rejestr.
Nie udostępniaj wolumenu klucza backendom. Przy migracji ze starego układu zachowaj
stary wolumen i konfigurację; nie kasuj ich jako sposobu naprawy błędu startu.
