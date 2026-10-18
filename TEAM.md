# Zespół Codex

Ten dokument opisuje oszczędny tryb pracy nad projektem.

## Role i modele

- **Architekt**: główny agent prowadzący rozmowę. Analizuje problem, ustala priorytety i przedstawia użytkownikowi zakres zmian. Gdy trzeba oddelegować samą analizę, domyślnie używa `gpt-6-sol`.
- **Developer**: `gpt-6-sol`. Wdraża wyłącznie zatwierdzony zakres, dodaje testy regresyjne i raportuje pliki oraz wyniki.
- **Reviewer**: `gpt-6-sol`. Niezależnie sprawdza diff, współbieżność, cykl życia, obsługę błędów i testy; zgłasza konkretne problemy z lokalizacją i scenariuszem.

`gpt-6-astra` jest modelem eskalacyjnym dla trudnego projektu architektury, błędu trudnego do odtworzenia lub sytuacji, w której analiza modelem Sol nie rozstrzyga ryzyka. Nie uruchamiaj Astry rutynowo ani równolegle z innymi agentami. Zmianę modelu architekta w bieżącej rozmowie wykonuje użytkownik w selektorze modeli.

## Kiedy uruchamiać agentów

- Pytania, przeglądy i małe poprawki obsługuje główny agent bez subagentów.
- Przy istotnej zmianie kodu uruchom developera. Reviewer sprawdza gotowy diff.
- Architekta pomocniczego uruchom tylko wtedy, gdy wymagana jest niezależna decyzja projektowa. Nie powielaj tej samej analizy przez kilka agentów.
- Prowadź najwyżej dwa subagenty naraz. Przydzielaj im odrębne, krótkie zadania i nie dawaj kilku agentom edycji tych samych plików.

## Przepływ pracy

1. Architekt sprawdza kod i proponuje mały, konkretny zakres z kryteriami akceptacji.
2. Przed zmianami wymagającymi decyzji produktowej architekt uzyskuje akceptację użytkownika.
3. Developer wdraża zatwierdzony zakres i uruchamia testy adekwatne do zmiany.
4. Reviewer ocenia gotowy diff niezależnie od implementacji. Developer poprawia usterki w ramach zatwierdzonego zakresu.
5. Główny agent weryfikuje końcowy stan, podsumowuje zmiany i uczciwie podaje wyniki testów.

## Oszczędzanie limitu

- Używaj Sol jako ustawienia domyślnego; zachowuj Astro na rzeczy, które wymagają jej dodatkowej zdolności rozumowania.
- Ograniczaj kontekst agentów do plików, celu, ograniczeń i oczekiwanego wyniku. Korzystaj z krótkich odpowiedzi i nie wklejaj całych historii rozmowy.
- Deleguj równoległą pracę tylko wtedy, gdy zmniejsza czas lub poprawia niezależność kontroli.
- Uruchamiaj testy celowane po zmianach, a pełny zestaw raz przed zakończeniem. Nie powtarzaj testów, które przeszły, bez nowej zmiany lub konkretnego powodu.
- Nie zwiększaj poziomu rozumowania ani liczby agentów automatycznie. Zrób to dopiero, gdy wynik ujawnia konkretną lukę.
- Przed długą pracą użytkownik może sprawdzić bieżące limity i czas resetu w **Settings → Usage**; limity zależą od planu, modelu i rodzaju zadania.
