# Privacy MCP Gateway

To wersja 0.1 przeznaczona do pracy na wyodrębnionym tekście. Lokalny serwer MCP działa przez stdio. Administrator rejestruje pliki z dozwolonych katalogów i otrzymuje losowe identyfikatory `doc_…`. Narzędzia MCP przyjmują wyłącznie te identyfikatory. Odczyt źródła przechodzi przez kontrolę katalogu i łączy bez podążania za symlinkami; źródła są zawsze otwierane tylko do odczytu. Wynik ekstrakcji trafia do lokalnego Presidio, a klient dostaje tekst z pseudonimami HMAC. Ten sam fragment PII ma ten sam pseudonim w ramach `access_scope` i klucza. Identyfikatory plików, metadane i błędy narzędzi nie ujawniają oryginalnych nazw ani ścieżek.

## Instalacja

Wymagany Python 3.11–3.14, lokalny model spaCy, dostęp do pakietów podczas instalacji. Przykład dla angielskiego modelu (produkcja nie pobiera modelu automatycznie):

```sh
python3 -m venv .venv
.venv/bin/pip install '.[documents]'
.venv/bin/python -m spacy download en_core_web_lg
cp config.example.json config.json
```

W `config.json` ustaw bezwzględne `source_roots` i osobny `workspace`. Przykład używa `language: en`. Dla polskiego ustaw `language: pl`, `spacy_model` na nazwę **lokalnie zainstalowanego, zgodnego modelu spaCy**, i dobierz `entities` do własnej polityki, np. `PERSON`, `LOCATION`, `PESEL`, `NIP`, `REGON`. Trzy polskie numery korzystają z wzorców Presidio; dopasowanie wzorca nie sprawdza sum kontrolnych. Zwiększ `policy_version` po zmianie własnych reguł/modelu, aby rozdzielić cache. Brak modelu albo błąd inicjalizacji Presidio blokuje start. Zweryfikuj model i politykę na własnym zestawie dokumentów przed użyciem wrażliwych danych.

```sh
.venv/bin/privacy-gateway init-key /secure/path/gateway.key
.venv/bin/privacy-gateway register --config config.json --key-file /secure/path/gateway.key /absolute/allowed/path/file.txt
.venv/bin/privacy-gateway serve --config config.json --key-file /secure/path/gateway.key
```

`init-key` tworzy plik 32 losowych bajtów z uprawnieniami `0600`; przechowuj go poza katalogami źródłowymi i repozytorium. Utrata klucza uniemożliwia odczyt rejestru i cache. Zmiana klucza zmienia pseudonimy. Serwer MCP skonfiguruj w kliencie jako lokalną komendę `privacy-gateway serve …`, z transportem stdio. Rejestracja jest czynnością administratora w CLI i nie jest narzędziem MCP.

## Narzędzia

`privacy_list_files()` zwraca identyfikatory; `privacy_read_file(document_id)` sanitizowany tekst; `privacy_search(query)` fragmenty sanitizowanego tekstu; `privacy_get_metadata(document_id)` rodzaj, wersję i `text/plain`; `privacy_create_file(content)` i `privacy_update_file(document_id, content, expected_version)` tworzą lub zastępują dokument roboczy po ponownej analizie PII; `privacy_export_file(document_id)` zapisuje atomowo plik `.txt` w `workspace/exports` i zwraca jego nazwę. Aktualizacja wymaga bieżącej wersji. Żadne narzędzie nie zapisuje źródła.

TXT musi być UTF-8. DOCX, PDF i XLSX są wyodrębniane do zwykłego tekstu w osobnym procesie z limitem pamięci 768 MiB, CPU 15 s i czasu 20 s. Serwer dopuszcza jeden proces parsera naraz; oczekiwanie na wolny slot kończy się po 20 s odmową. Format, układ, obrazy, formuły i część zaawansowanych elementów dokumentu nie są zachowane. Obrazy w PDF/DOCX/XLSX, zaszyfrowany PDF, puste strony PDF, formuły i zewnętrzne połączenia XLSX są odrzucane. Nie ma OCR. Nie używaj eksportu `.txt` jako zachowanej, bezpiecznej kopii natywnego dokumentu. Presidio może przeoczyć dane osobowe, a wzorce mogą dawać fałszywe wyniki. Weryfikuj politykę detekcji i eksporty przed udostępnieniem dalej. Limity procesu działają na Linuksie i ograniczają zużycie zasobów; proces parsera dziedziczy tożsamość systemową, dostęp do plików i środowisko serwera, więc nie stanowi osobnej piaskownicy bezpieczeństwa. Stosuj opisane niżej ograniczenia kontenera i uprawnień.

Cache SQLite i rejestr zawierają zaszyfrowane wartości uwierzytelnionym Fernet. Klucz cache uwzględnia SHA-256 źródła, politykę, identyfikator detektora, wersje parserów, zakres dostępu i format. Źródło jest ponownie otwierane i sprawdzane przed trafieniem w cache. Cache ma TTL. Eksportowane pliki `.txt` są celowo jawne w prywatnym `workspace/exports`, więc chroń ten katalog. Serwer nie loguje treści, nazw źródłowych ani ścieżek.

## Izolacja hosta

MCP nie jest zaporą sieciową ani barierą uprawnień dla innych narzędzi klienta. Uruchom proces na koncie bez dostępu do innych poufnych katalogów; przyznaj mu odczyt wyłącznie katalogów źródłowych oraz zapis wyłącznie do workspace. W kontenerze zamontuj źródła jako `:ro`, klucz jako `:ro`, workspace jako jedyny wolumen zapisywalny, uruchom bez sieci (`--network none`) i bez nowych uprawnień (`--security-opt no-new-privileges`). Nie montuj całego katalogu domowego ani gniazda Dockera. Sam serwer stdio nie wystawia portu. Przykładowy `Dockerfile` pobiera model podczas budowania obrazu; serwer produkcyjny nie pobiera go przy starcie.

```sh
docker build -t privacy-gateway .
docker run --rm -i --network none --read-only --security-opt no-new-privileges \
  --memory 1g --pids-limit 64 --cpus 1 \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m --user "$(id -u):$(id -g)" \
  --mount type=bind,src=/host/sources,dst=/sources,readonly \
  --mount type=bind,src=/host/workspace,dst=/workspace \
  --mount type=bind,src=/host/gateway.key,dst=/run/gateway.key,readonly \
  --mount type=bind,src=/host/config.json,dst=/etc/gateway/config.json,readonly \
  privacy-gateway serve --config /etc/gateway/config.json --key-file /run/gateway.key
```

W pliku konfiguracyjnym użytym w kontenerze ustaw `source_roots: ["/sources"]` i `workspace: "/workspace"`. Katalog `/host/workspace` musi już istnieć i być zapisywalny przez wskazany UID. Gniazdo klienta MCP uruchamia powyższą komendę jako podproces stdio.

Poza zakresem wersji 0.1 są natywne wyjście zachowujące format, dwupoziomowy cache natywnych dokumentów, odwracalna mapa pseudonimów, lokalny OCR i formalna certyfikacja DLP.

## Testy

```sh
python3 -m unittest discover -s tests -v
```

Testy używają jawnie wstrzykniętego, deterministycznego detektora zamiast Presidio i sprawdzają ścieżki, cache oraz szyfrowanie. Produkcyjny start zawsze wymaga Presidio i lokalnego modelu.
# privacy_gateway_mcp
