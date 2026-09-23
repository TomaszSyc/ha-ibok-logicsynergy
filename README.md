# <img src="custom_components/ibok/brand/icon.png" alt="" width="36" align="top"> iBOK (LogicSynergy) — integracja Home Assistant

[![Wydanie](https://img.shields.io/github/v/release/TomaszSyc/ha-ibok-logicsynergy?display_name=tag&sort=semver)](https://github.com/TomaszSyc/ha-ibok-logicsynergy/releases)
[![Quality](https://github.com/TomaszSyc/ha-ibok-logicsynergy/actions/workflows/quality.yml/badge.svg)](https://github.com/TomaszSyc/ha-ibok-logicsynergy/actions/workflows/quality.yml)
[![HACS: repozytorium własne](https://img.shields.io/badge/HACS-repozytorium%20w%C5%82asne-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)

Integracja z portalami **iBOK** firmy LogicSynergy, z których korzysta wiele polskich
przedsiębiorstw wodociągowo-kanalizacyjnych. Pobiera saldo, faktury, stany wodomierzy
i historię odczytów, a także pozwala **podać odczyt** bez wchodzenia na stronę.

Adres portalu podaje się przy konfiguracji, więc integracja nie jest związana z jednym
przedsiębiorstwem.

## Status

Wersja wczesna, do testów. Portal nie ma publicznego API — integracja korzysta z tych
samych zapytań, co przeglądarka. Zmiana po stronie dostawcy oprogramowania może ją
zepsuć.

## Instalacja

[![Otwórz repozytorium w HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=TomaszSyc&repository=ha-ibok-logicsynergy&category=integration)

Przycisk otwiera to repozytorium bezpośrednio w HACS w Twojej instancji Home
Assistanta. Po zainstalowaniu zrestartuj HA i dodaj integrację:

[![Dodaj integrację.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ibok)

Ręcznie: HACS → Integracje → ⋮ → Własne repozytoria → dodaj to repo jako typ
*Integration*, a potem Ustawienia → Urządzenia i usługi → Dodaj integrację → **iBOK**.

## Konfiguracja

| Pole | Znaczenie |
|---|---|
| Adres portalu | np. `https://ibok.przyklad.pl` |
| Login, hasło | te same, co na stronie |

W opcjach ustawia się częstotliwość odpytywania (domyślnie 6 h) oraz — **osobno dla
każdego wodomierza** — encję ze stanem licznika. Pola noszą nazwy złożone z numeru
fabrycznego, na przykład `source_entity_12345678`. Wskazanie encji jest opcjonalne
i włącza przycisk dla tego jednego wodomierza; kto spisuje stan z tarczy, korzysta
z usługi i nie traci nic poza przyciskiem.

### Kilka wodomierzy

Typowy przypadek to **wodomierz ogrodowy** obok domowego, żeby nie płacić **za ścieki**
od wody zużytej na podlewanie. Integracja obsługuje to wprost:

- każdy wodomierz dostaje **własne urządzenie** z własnymi encjami stanu i zużycia
- encję źródłową przypisuje się **do konkretnego wodomierza**, więc stan domowego nie
  zostanie wysłany jako odczyt ogrodowego
- przycisk pojawia się tylko przy tych wodomierzach, którym przypisano encję
- w usłudze `ibok.submit_reading` trzeba wtedy podać `meter_id` — bez niego integracja
  odmawia wysłania i wypisuje dostępne identyfikatory, zamiast zgadywać

## Encje

- **Saldo** — z modułu rozliczeń
- **Ostatnia faktura** — kwota brutto, z kwotą netto i VAT w atrybutach
- **Wodomierz N — stan** — ostatni odczyt zarejestrowany przez przedsiębiorstwo
- **Wodomierz N — zużycie** — zużycie w ostatnim okresie rozliczeniowym
- **Podaj odczyt** (przycisk) — tylko dla wodomierzy z przypisaną encją źródłową

## Podanie odczytu

```yaml
action: ibok.submit_reading
data:
  reading: 48
```

`meter_id` jest potrzebne tylko wtedy, gdy odczyt można podać dla więcej niż jednego
wodomierza. Opcjonalnie przyjmuje też `reading_date` i `note`.

**Wysłanie nigdy nie następuje samo.** Błędny odczyt trafia na fakturę i trzeba go potem
prostować z przedsiębiorstwem, więc wywołanie jest zawsze świadome — usługą albo
przyciskiem. Przed wysłaniem wartość jest sprawdzana względem zakresu i liczby cyfr,
które portal sam podaje, więc literówka nie dojdzie do przedsiębiorstwa.

Przycisk dodatkowo **ucina wartość encji źródłowej do dokładności tarczy**. Nakładka
radiowa podaje litry, a przedsiębiorstwo zapisuje to, co widać na liczydle; ile cyfr ono
pokazuje, mówi sam portal. Ucięcie, nie zaokrąglenie — przy 48,6 tarcza nadal pokazuje
48. Usługa wywołana wprost wysyła to, co jej podasz.

## Odpytywanie

Domyślnie co 6 godzin. Odczyty i faktury zmieniają się najwyżej raz dziennie, więc
częściej nie ma czego pobierać, a regulaminy iBOK zakazują działań mogących zakłócić
pracę serwisu. Nie skracaj tego bez powodu.

## Uwagi

Integracja nieoficjalna, niezwiązana z LogicSynergy ani z żadnym przedsiębiorstwem.
Hasło trafia wyłącznie do konfiguracji Home Assistanta i jest wysyłane tylko do
wskazanego portalu.

## Rozwój

```bash
pip install -r requirements-dev.txt
pre-commit install
```

`pre-commit` wykonuje tylko szybkie kontrole: formatowanie, ruff, wykrycie klucza
prywatnego. Wszystko, co wymaga zainstalowanego Home Assistanta, chodzi w CI — commit
nie ma czekać na rozwiązywanie zależności.

```bash
ruff check . && ruff format --check .
pytest tests/ -q
```

Testy importują integrację **wobec przypiętej wersji Home Assistanta**. To ten test, który
wyłapuje zniknięcie helpera po aktualizacji rdzenia — inaczej pierwszym sygnałem jest
instancja, która odmawia załadowania integracji.

Wersja w `manifest.json` rośnie z każdym commitem. Przy HACS to jedyny tani dowód, co
komu faktycznie siedzi na dysku.

### Wydania

HACS instaluje z **ostatniego wydania**, a nie z bieżącego stanu gałęzi. Tag musi więc
być tym samym numerem, co `version` w `manifest.json` — jeśli się rozjadą, HACS pokaże
jedną wersję, a `manifest.json` na dysku będzie mówił co innego i nie da się ustalić,
co instancja naprawdę uruchamia.

```bash
git tag v0.1.0 && git push origin v0.1.0
```

`test_manifest.py` pilnuje, żeby numer w manifeście był poprawnym semverem; zgodność
z tagiem sprawdza workflow wydania.

## Licencja

MIT — patrz [LICENSE](LICENSE).
