# Proxy MCP przez Docker i Streamable HTTP

Bramka działa jako jeden serwis `gateway` na Windows, Linux i macOS. Wymaga
Docker Engine z Compose v2 na Linuksie albo Docker Desktop z kontenerami
linuksowymi na Windows/macOS. Endpoint klienta: `http://localhost:8000/mcp`.

## Przygotowanie obrazu i konfiguracji

**Bazowy Dockerfile nie zawiera zewnętrznych backendów Office ani filesystem.**
Przed uruchomieniem operator musi przygotować obraz zawierający wybrane,
przypięte wersje serwerów MCP i ich środowiska, np. Node.js. Bazowy obraz można
zbudować poleceniem `docker build -t privacy-gateway:base .`, a następnie rozszerzyć
własnym Dockerfile. Docelowy obraz nazwij `privacy-gateway:local` albo ustaw jego
nazwę w `PG_IMAGE`. Instalacje i model pobieraj podczas budowania, nie przy starcie.
Binaria backendów muszą odpowiadać architekturze obrazu, także na Apple Silicon.

1. Skopiuj `.env.example` jako `.env` i `config.container.example.json` jako
   `config.container.json`.
2. Utwórz katalog `sources` i włóż do niego kopie dokumentów do prób.
   Inny katalog ustaw przez `PG_SOURCES` w `.env`, np.
   `PG_SOURCES=C:/Users/Jan/Documents/Do analizy` na Windows.
3. W konfiguracji usuń nieużywane backendy i dokumenty. Przykładowe `command`
   oraz `args` zastąp ścieżkami istniejącymi **w przygotowanym obrazie**.
   Domyślne ścieżki danych to `/sources` i `/data/workspace`.
   Serwer filesystem musi dopuszczać `/data/workspace`.
4. Zdefiniuj `documents` z publicznymi identyfikatorami, ścieżkami pod `/sources`
   i aliasami arkuszy Excela. Nie ma komendy rejestracji dokumentów: konfiguracja
   operatora jest jedyną listą udostępnianych plików. Po jej zmianie zrestartuj bramkę.
5. Zapewnij UID 1000 możliwość odczytu i zapisu katalogu źródłowego. Proxy edytuje
   dokumenty, więc źródła są montowane jako zapisywalne. Workspace i klucz są
   w prywatnym wolumenie; backendy powinny pracować na kopiach w workspace.

Obraz bazowy zawiera angielski model `en_core_web_lg`. Polski wymaga instalacji
zgodnego modelu podczas budowania obrazu i zmiany języka oraz encji w konfiguracji.
Brak backendu, modelu lub zgodnego schematu narzędzi blokuje start.

## Pierwszy start

Z katalogu repozytorium, w PowerShell lub powłoce Linux/macOS:

```console
docker compose --profile setup run --rm bootstrap
docker compose up -d gateway
docker compose ps
```

`bootstrap` to jednorazowe zadanie tworzące klucz, a nie drugi stale działający
serwis. Wykonaj je tylko raz dla nowego wolumenu. Istniejącego klucza nie nadpisuje.

W lokalnym kliencie MCP wybierz Streamable HTTP i URL `http://localhost:8000/mcp`.
Składnia konfiguracji zależy od klienta. Klient w chmurze nie ma dostępu do Twojego
`localhost`. Po restarcie bramki klient musi ponowić połączenie. Nie powtarzaj
automatycznie zapisu dokumentu po błędzie lub zerwaniu połączenia.

## Autostart, dane i aktualizacje

Na Windows/macOS w Docker Desktop włącz **Settings → General → Start Docker
Desktop when you sign in to your computer**. Jest to start po zalogowaniu.
Na Linuksie skonfiguruj start silnika Docker wraz z systemem.

Polityka `restart: unless-stopped` uruchamia wcześniej utworzony kontener po
restarcie silnika, jeśli nie zatrzymano go ręcznie. `/healthz` sprawdza odpowiedź
HTTP; nie sprawdza wszystkich backendów i jakości anonimizacji. Status `unhealthy`
sam w sobie nie powoduje restartu kontenera.

```console
docker compose logs --tail 50 gateway
docker compose stop gateway
docker compose up -d gateway
```

Klucz i szyfrowany rejestr tokenów są w wolumenie `privacy-gateway_private_data`.
Nie usuwaj go ani nie wykonuj `docker compose down -v`, jeśli chcesz zachować dane.
Kopia wolumenu zawiera także klucz i wymaga ochrony. Przed aktualizacją zatrzymaj
bramkę i wykonaj kopię wolumenu, potem zbuduj nowy obraz i wykonaj `up -d`.
Port hosta można zmienić przez `PG_PORT` w `.env` i odtworzenie kontenera.

Jeżeli używałeś poprzedniej konfiguracji z `gateway-proxy`, najpierw zatrzymaj
stary kontener i usuń go poleceniem `docker compose rm gateway-proxy` przy użyciu
starego pliku Compose. Zachowaj wolumen danych. W nowej konfiguracji nie ma profili
`proxy` ani `extraction`, a jedyną komendą serwera jest `serve`.

## Granica dostępu

HTTP nie ma uwierzytelniania. Każdy lokalny proces z dostępem do portu może wywołać
narzędzia. Compose publikuje port tylko pod `127.0.0.1`. Kontrole Host/Origin nie
zastępują uwierzytelniania. Nie zmieniaj mapowania na publiczny adres bez dodania
uwierzytelniania i TLS. Endpoint ma jeden zakres dostępu, nie izoluje użytkowników.

Sieć `internal: true` umożliwia HTTP, ale nie zastępuje `--network none`.
Dostęp do hosta i kontenerów tej sieci zależy od ustawień Dockera. Backendy muszą
być zaufane i mieć minimalne uprawnienia. Nie montuj całego katalogu domowego ani
gniazda Dockera.

Na systemie zgodnym z obecnym kodem uniksowym można też uruchomić proxy bez Dockera:

```console
privacy-gateway serve --config config.proxy.json --key-file gateway.key --host 127.0.0.1 --port 8000
```

Nasłuch poza loopback wymaga jawnego `--allow-non-loopback`; Compose używa tej
opcji wewnątrz kontenera z lokalnym mapowaniem portu. Nie dodaje ona uwierzytelniania.
Wariant przenośny dla trzech systemów korzysta z kontenera Linux.

Transport przetestowano z syntetycznym backendem. Obraz z rzeczywistymi serwerami
Office wymaga próby odczyt–edycja–ponowny odczyt na reprezentatywnych dokumentach.
