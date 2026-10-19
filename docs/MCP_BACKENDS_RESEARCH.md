# Weryfikacja serwerów MCP do pracy biurowej

Data przeglądu: 2026-10-18.

## Metoda i poziom pewności

Przejrzano dokumentację autorów, listy narzędzi oraz wybrane pliki źródłowe. Nie instalowano ani nie uruchamiano serwerów zewnętrznych; nie wykonano testów zachowania dokumentów po zapisie. Poniższe wyniki potwierdzają dostępność interfejsów, a nie jakość działania na dokumentach firmowych, bezpieczeństwo implementacji ani pełną zgodność z Office. Repozytoria na gałęzi main mogą się zmieniać; przed integracją trzeba wybrać i przypiąć konkretne wersje.

## Wynik

Można oprzeć aplikację na lokalnie uruchamianych, publicznie dostępnych serwerach MCP i ograniczyć własny kod do bramki prywatności, kontroli dostępu oraz polityk konkretnych narzędzi. Typowe operacje na DOCX, XLSX, PPTX i plikach tekstowych mają dostępne interfejsy. Nie ma podstaw do obietnicy uniwersalnego edytora wszystkich formatów i wszystkich funkcji Office. PDF i formaty historyczne wymagają osobnych kryteriów odbioru.

## Kandydaci

| Format | Kandydat i potwierdzony interfejs | Ocena dla gatewaya |
| --- | --- | --- |
| DOCX | [knorq-ai/docx-mcp-server](https://github.com/knorq-ai/docx-mcp-server): odczyt, zamiany, edycja akapitów i tabel, komentarze, śledzenie zmian. Akapity można adresować kotwicami. | Dobry kandydat do próby odczyt–edycja–ponowny odczyt. Kotwice ograniczają zależność edycji od zanonimizowanego tekstu. Nie obejmują wszystkich elementów dokumentu. |
| XLSX | [negokaz/excel-mcp-server](https://github.com/negokaz/excel-mcp-server): odczyt zakresów, zapis wartości/formuł, tworzenie i kopiowanie arkuszy, formatowanie. | Dobry kandydat. Komórki mają adresy; poufne nazwy arkuszy i ścieżki wymagają odrębnych uchwytów. Obsługa makroformatu nie oznacza zatwierdzenia makr ani pełnej zgodności Excela. |
| PPTX | [knorq-ai/pptx-mcp-server](https://github.com/knorq-ai/pptx-mcp-server): odczyt slajdów, edycja tekstu istniejących kształtów i komórek tabel, operacje na slajdach. | Dobry kandydat do tekstu. Indeksy slajdu/kształtu/akapitu mogą służyć do wskazywania miejsca zmiany; po zmianach strukturalnych wymagają odświeżenia. |
| TXT, MD, CSV, HTML | [modelcontextprotocol/server-filesystem](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem): odczyt, zapis tekstu, selektywna edycja i podgląd diff. | Wystarczy do operacji tekstowych. CSV nie zyskuje automatycznie semantyki arkusza. Bramka musi ograniczać rozszerzenia, ścieżki i operacje. |
| PDF z tekstem | [AryanBV/pdf-edit-mcp](https://github.com/AryanBV/pdf-edit-mcp): odczyt tekstu/pozycji, zamiany, edycja bloków i formularzy. | Kandydat eksperymentalny. Autor opisuje ograniczenia fontów, brakujących glifów i przepełnienia. Bez testów nie można zapewnić zachowania układu. Skan bez warstwy tekstowej poza zakresem. |
| ODT/ODS/ODP, DOC/XLS/PPT, RTF | [BetterExcel/libreoffice_mcp](https://github.com/BetterExcel/libreoffice_mcp/blob/main/README.md) deklaruje odczyt wielu formatów, konwersję i edycję tekstu Writer. | Nie potwierdza równoważnej edycji każdego formatu. Wariant do późniejszej walidacji, z osobną instalacją LibreOffice. |

Źródła kodu dla najważniejszych operacji:

- [DOCX: rejestracja narzędzi](https://github.com/knorq-ai/docx-mcp-server/blob/main/src/index.ts): `read_document`, `ensure_anchors`, `edit_paragraphs`; tekstowe odpowiedzi oraz błędy.
- [PPTX: rejestracja narzędzi](https://github.com/knorq-ai/pptx-mcp-server/blob/main/src/pptx_mcp_server/server.py): `pptx_edit_text(file_path, slide_index, shape_index, text, paragraph_index, ...)`.

## Dwa pozornie uniwersalne rozwiązania

[gawirable/office-mcp](https://github.com/gawirable/office-mcp) łączy trzy nowoczesne formaty Office. Nie należy jednak utożsamiać liczby narzędzi z pełną edycją: [moduł PPTX](https://github.com/gawirable/office-mcp/blob/main/office_mcp/pptx_tools.py) udostępnia odczyt i dodawanie elementów, ale przejrzana lista nie zawiera odpowiednika edycji tekstu istniejącego kształtu. Dlatego nie jest samodzielnym potwierdzeniem całego wymaganego zakresu.

[Microsoft MarkItDown-MCP](https://github.com/microsoft/markitdown/tree/main/packages/markitdown-mcp) udostępnia `convert_to_markdown(uri)`. Może ułatwić odczyt różnych dokumentów, ale nie zapewnia edycji zwrotnej do formatu oryginału. Nie zastępuje backendów edycyjnych.

## Jak połączyć edycję z ochroną tekstu

To wnioski projektowe wynikające z interfejsów powyżej:

1. Backend odczytuje oryginał lokalnie. Gateway przekazuje klientowi tekst z pseudonimami oraz bezpieczne identyfikatory dokumentu i miejsca edycji.
2. Przy edycji oryginału tokeny muszą być lokalnie odtwarzane wyłącznie w zatwierdzonych polach narzędzia i w uprawnionym kontekście. Inaczej wyszukiwanie tokenu nie znajdzie oryginału albo pseudonim zostanie wpisany do dokumentu. Nieznane tokeny powinny blokować operację.
3. Zapis wymaga identyfikacji wersji źródła, kontroli konfliktów, kopii roboczej i ponownego odczytu wyniku. Nie należy ponawiać automatycznie operacji zapisu po niejednoznacznym błędzie.
4. Anonimizować trzeba wszystkie dopuszczone odpowiedzi: treść, dane strukturalne, komentarze, historię zmian, nazwy, komunikaty i metadane. Nie wolno traktować dowolnego pola string jako treści: ścieżki, kotwice i nazwy arkuszy mają znaczenie sterujące.
5. Przy zakresie tekstowym blokować obrazy, podglądy, audio, binarne zasoby i bezpośrednie odnośniki omijające gateway. Oryginał może zawierać grafikę, lecz klient otrzymuje wyłącznie zatwierdzoną reprezentację tekstową.
6. Tekstowy PESEL/NIP może być zapisany w komórce liczbowej. Polityka musi świadomie uwzględniać takie wartości i nie zamieniać automatycznie wszystkich liczb na tekst. Formuły mogą także zawierać poufne literały, ścieżki i nazwy.
7. Serwery z dostępem do oryginałów powinny pracować lokalnie, w izolacji. Gateway nie zabezpiecza danych, które backend sam wyśle do zewnętrznej usługi. Klient nie może mieć równoległego dostępu do backendu ani oryginałów.

Ochrona odpowiedzi MCP nie jest anonimizacją całego pliku na dysku. Oryginały, eksporty natywne i historia zmian mogą celowo nadal zawierać dane firmowe; ich udostępnianie wymaga osobnej polityki.

## Zakres rekomendowany do próby integracyjnej

Najpierw DOCX + XLSX + PPTX + TXT/MD/CSV, odczyt i kontrolowana edycja tekstu. PDF: odczyt oraz ograniczone, testowane zamiany. Formaty historyczne i ODF: później. Własne parsery można zastąpić backendami, lecz nadal potrzebne będą małe polityki i mapowania na poziomie narzędzi MCP.

Przed uznaniem rozwiązania za funkcjonalne trzeba wykonać próby na syntetycznych danych:

- odczyt → pseudonimizacja → edycja → zapis → ponowny odczyt;
- identyczne tokeny w kilku plikach i zachowanie danych przy edycji oryginału;
- polskie znaki, dane rozdzielone między fragmentami tekstu, tabele, komentarze i rewizje;
- poufne nazwy plików/arkuszy, metadane i błędy; brak wycieku w każdym dopuszczonym kanale;
- brak zmian w nietkniętych komórkach, formułach, akapitach i elementach prezentacji;
- konflikty wersji, awaria zapisu, niejednoznaczne wyniki i próby dostępu poza katalogiem;
- dla PDF dodatkowo fonty, szerokość zamian, odczyt po zapisie i kontrola wizualna lokalnie.

Status: wykonalność architektoniczna potwierdzona na poziomie publicznych interfejsów i wybranego kodu. Gotowość eksploatacyjna pozostaje niepotwierdzona do czasu tych prób.
