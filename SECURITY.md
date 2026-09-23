# Zgłaszanie problemów bezpieczeństwa

## Jak zgłosić

**Nie zakładaj publicznego zgłoszenia.** Użyj
[prywatnego zgłoszenia podatności](https://github.com/TomaszSyc/ha-ibok-logicsynergy/security/advisories/new)
— trafi wyłącznie do opiekuna repozytorium.

To projekt prowadzony po godzinach, więc nie obiecuję terminu odpowiedzi. Postaram się
odpisać w ciągu tygodnia.

## Czego dotyczy

Integracja trzyma **login i hasło do konta w portalu wodociągowym** i wysyła je wyłącznie
pod adres podany przy konfiguracji. Zgłoszeń wartych uwagi szukałbym w:

- wycieku poświadczeń do logów, atrybutów encji, diagnostyki lub zgłoszeń błędów
- wysłaniu ich pod inny adres niż skonfigurowany portal
- podaniu odczytu bez świadomego działania użytkownika albo dla cudzego wodomierza
- obejściu sprawdzenia zakresu, które chroni przed wysłaniem błędnej wartości

## Czego nie dotyczy

Portale iBOK należą do poszczególnych przedsiębiorstw wodociągowych i do firmy
LogicSynergy. **Podatności w samym portalu zgłaszaj im, nie tutaj** — ten projekt tylko
korzysta z tych samych zapytań, co przeglądarka.

## Wspierane wersje

Poprawki wchodzą do najnowszego wydania. Starszych nie łatam.
