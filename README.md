# Privacy MCP Gateway

## Tryb proxy MCP v0.2

`serve-proxy` udostępnia wybrane lokalne narzędzia istniejących serwerów MCP dla zarejestrowanych dokumentów DOCX, XLSX, PPTX oraz TXT/MD/CSV/HTML. Konfigurację operatora pokazuje [config.proxy.example.json](config.proxy.example.json). Zastąp ścieżki przykładowe rzeczywistymi, absolutnymi ścieżkami do lokalnie zainstalowanych i przypiętych wersji serwerów. Nie uruchamiaj `npx --yes` ani instalacji z sieci przy starcie bramki. Podane w przykładzie ścieżki instalacji i pliki wykonywalne trzeba dostosować do przypiętych wersji; bramka sprawdza obecność i wymagany schemat narzędzi przy starcie. Python MCP SDK wymaga wersji 1.26–1.x.

```sh
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/python -m spacy download en_core_web_lg
cp config.proxy.example.json config.proxy.json
chmod 600 config.proxy.json
```

Zainstaluj osobno lokalne wersje [knorq DOCX](https://github.com/knorq-ai/docx-mcp-server), [negokaz Excel](https://github.com/negokaz/excel-mcp-server), [knorq PPTX](https://github.com/knorq-ai/pptx-mcp-server) i [MCP filesystem](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem). Przykład zakłada zbudowany plik Node dla DOCX i filesystem, plik wykonywalny Go dla Excela oraz lokalny skrypt CLI Pythona dla PPTX. Ustaw ścieżki do tych konkretnych instalacji w `config.proxy.json`, a także dokumenty, katalog źródeł, prywatny workspace i aliasy arkuszy.

Usuń nieużywane backendy i dokumenty z konfiguracji. Przykład używa modelu angielskiego; dla polskiego ustaw `language: pl`, zainstalowany lokalnie model spaCy oraz encje obsługiwane w tym języku. Brak modelu lub wymaganego detektora blokuje start. Do trybu proxy nie potrzeba dodatku `documents` z parserami wersji 0.1.

```sh
.venv/bin/privacy-gateway init-key /secure/path/gateway.key
.venv/bin/privacy-gateway serve-proxy --config config.proxy.json --key-file /secure/path/gateway.key
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

Backendy dostają ścieżki tylko do kopii roboczych. Uruchamiaj je bez dostępu do sieci, na koncie z minimalnymi uprawnieniami; skonfigurowany serwer filesystem musi mieć na swojej liście dozwolonych katalogów prywatny `workspace`, ponieważ tam powstają kopie. Bramka nie kontroluje kodu wykonywanego wewnątrz zewnętrznego backendu, który może mieć uprawnienia systemowe do oryginałów. Klient nie powinien mieć bezpośredniego dostępu do tych backendów ani do plików źródłowych. Tryb `serve` z wersji 0.1 nadal działa oddzielnie.

Wersja 0.2 nie jest jeszcze potwierdzona testem z rzeczywistymi serwerami Office w tym środowisku. Testy jednostkowe używają syntetycznego backendu; przed produkcyjną edycją dokumentów trzeba przypiąć i uruchomić backendi oraz wykonać próbę odczyt–edycja–ponowny odczyt na kopiach reprezentatywnych plików, w tym polskich danych, tabel, rewizji i nazw arkuszy.

## Tryb ekstrakcji v0.1

Tryb 0.1 jest przeznaczony do pracy na wyodrębnionym tekście. Lokalny serwer MCP działa przez stdio. Administrator rejestruje pliki z dozwolonych katalogów i otrzymuje losowe identyfikatory `doc_…`. Narzędzia MCP przyjmują wyłącznie te identyfikatory. Odczyt źródła przechodzi przez kontrolę katalogu i łączy bez podążania za symlinkami; źródła są zawsze otwierane tylko do odczytu. Wynik ekstrakcji trafia do lokalnego Presidio, a klient dostaje tekst z pseudonimami HMAC. Ten sam fragment PII ma ten sam pseudonim w ramach `access_scope` i klucza. Identyfikatory plików, metadane i błędy narzędzi nie ujawniają oryginalnych nazw ani ścieżek.

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

