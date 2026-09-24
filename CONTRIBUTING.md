# Współtworzenie

Integrację sprawdzałem na jednym portalu iBOK, a każde przedsiębiorstwo może mieć włączone
inne moduły. Najbardziej pomaga więc raport z innego portalu, także wtedy, gdy wszystko
działa.

## Zgłoszenia

- **Inne przedsiębiorstwo**: jak integracja działa na portalu innym niż testowany
- **Błąd**: coś nie działa; dołącz wersje integracji i Home Assistanta oraz logi
- **Propozycja**: pomysł na rozszerzenie

Problemu bezpieczeństwa nie opisuj publicznie. Jak go zgłosić, mówi
[SECURITY.md](SECURITY.md).

## Zanim coś wkleisz

Zgłoszenia są publiczne, a ich treść od razu trafia do powiadomień, więc późniejsza edycja
jej nie cofnie. Nie wklejaj loginu, hasła, adresu nieruchomości, numerów wodomierzy ani ich
identyfikatorów w iBOK, numeru klienta ani umowy, stanów liczników ani kwot z faktur.
W logach zamień je na zmyślone wartości, np. `12345678`. Adres portalu jest potrzebny
i może zostać.

## Zmiany w kodzie

Przy większej zmianie najpierw załóż propozycję, żeby nie pisać czegoś, czego nie przyjmę.

Testy importują integrację wobec przypiętej wersji Home Assistanta, która wymaga Pythona
3.14:

```bash
pip install -r requirements-dev.txt
pre-commit install
ruff check . && ruff format --check .
pytest tests/ -q
```

Testy nie łączą się z żadnym prawdziwym portalem. Logowanie i wysyłkę odczytu sprawdza
podstawiony portal z `tests/fake_portal.py`, uruchamiany na `127.0.0.1`. Jeśli zmiana
opiera się na jakimś zachowaniu portalu, najpierw odtwórz je tam. Każda zmiana zachowania
potrzebuje testu, który bez niej nie przechodzi.

- Podnieś wersję w `custom_components/ibok/manifest.json`, rośnie z każdym commitem.
- Teksty interfejsu są w `strings.json`, `translations/en.json` i `translations/pl.json`
  i muszą się zgadzać. Nie mogą zawierać adresów URL ani nawiasów ostrych, co sprawdza
  `tests/test_translations.py`.
- Opis commita to jedna linia po angielsku z prefiksem, np. `fix:`, `feat:`, `docs:`.

## Założenia, których nie zmieniam

- Integracja nigdy nie wysyła odczytu sama, tylko po wywołaniu usługi albo po drugim
  naciśnięciu przycisku. Nie przyjmę wysyłania według harmonogramu ani po zmianie encji
  źródłowej, bo błędny odczyt trafia na czyjąś fakturę.
- Login i hasło podaje się tylko w konfiguracji integracji. Trafiają wyłącznie do
  wskazanego portalu, zawsze przez HTTPS, i nie mogą pojawić się w logach, atrybutach
  encji ani diagnostyce.
- Portal jest odpytywany domyślnie co 6 godzin, a w opcjach najczęściej co godzinę.
  Krótszych wartości nie przyjmę, bo regulaminy iBOK zakazują działań, które mogą
  zakłócić pracę serwisu.

## Kodeks postępowania

Obowiązuje [Contributor Covenant 2.1](CODE_OF_CONDUCT.md) w angielskim oryginale.
Oficjalnego tłumaczenia na polski nie ma.
