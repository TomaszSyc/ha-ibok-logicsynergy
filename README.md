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

Portal nie ma publicznego API — integracja korzysta z tych samych zapytań, co
przeglądarka. Zmiana po stronie dostawcy oprogramowania może ją zepsuć.

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
| Adres portalu | np. `ibok.przyklad.pl`, wyłącznie przez HTTPS |
| Login, hasło | te same, co na stronie |

W opcjach ustawia się częstotliwość odpytywania (domyślnie 6 h) oraz — **osobno dla
każdego wodomierza** — encję ze stanem licznika. Pola noszą nazwy złożone z numeru
fabrycznego, na przykład `source_entity_12345678`. Wskazanie encji jest opcjonalne:
bez niej wodomierz dostaje pole **Odczyt do wysłania**, do którego wpisuje się stan
z tarczy. To pole i jego przycisk są na początku ukryte, bo większość osób podaje odczyt
w portalu: znajdziesz je na stronie urządzenia wśród ukrytych encji i odkryjesz jednym
przełącznikiem albo dodasz do panelu.

### Kilka wodomierzy

Typowy przypadek to **wodomierz ogrodowy** obok domowego, żeby nie płacić **za ścieki**
od wody zużytej na podlewanie. Integracja obsługuje to wprost:

- każdy wodomierz dostaje **własne urządzenie** z własnymi encjami stanu i zużycia
- encję źródłową przypisuje się **do konkretnego wodomierza**, więc stan domowego nie
  zostanie wysłany jako odczyt ogrodowego
- każdy wodomierz ma własny przycisk: z przypisaną encją wysyła jej wartość, bez niej
  to, co wpisano w jego polu **Odczyt do wysłania**
- w usłudze `ibok.submit_reading` trzeba wtedy podać `meter_id` — bez niego integracja
  odmawia wysłania i wypisuje dostępne identyfikatory razem z numerami fabrycznymi,
  zamiast zgadywać
- przy **dwóch kontach** każdy przycisk wysyła przez swoje konto; usługa szuka
  wodomierza we wszystkich i pyta o `config_entry_id` tylko wtedy, gdy oba konta mają
  wodomierz o tym samym identyfikatorze. Tak samo, gdy akurat jeden portal nie odpowie
  na zapytanie: usługa nie zgaduje wtedy, które konto to jest, tylko odmawia jako
  niejednoznaczne i prosi o `config_entry_id`
- drugie konto na tym samym portalu dostaje nazwę z numerem, np. `ibok.przyklad.pl (2)`,
  bo inaczej oba nazywałyby tak samo każde urządzenie; nazwę wpisu można potem zmienić

## Encje

- **Saldo** — z modułu rozliczeń
- **Ostatnia faktura** — kwota brutto ostatnio wystawionej faktury, z kwotą netto i VAT
  w atrybutach
- **Termin płatności** — do kiedy zapłacić ostatnią fakturę
- **Wodomierz N — stan** — najwyższy odczyt, jaki portal kiedykolwiek zarejestrował dla
  tego wodomierza; nie spada, nawet gdy portal pokaże korektę niższą od poprzedniego
  odczytu. Ostatni odczyt portalu i to, czy jest korektą, są w atrybutach
  `portal_reading` i `corrected`
- **Wodomierz N — zużycie** — zużycie w ostatnim okresie rozliczeniowym
- **Wodomierz N — cena za m³** — woda i ścieki brutto z ostatniej faktury, w takiej
  części, w jakiej przedsiębiorstwo rozlicza ten wodomierz
- **Wodomierz N — legalizacja do** — do kiedy licznik jest legalny; najpóźniej wtedy
  przedsiębiorstwo go wymieni
- **Odczyt do wysłania** (pole) — tylko przy wodomierzach bez encji źródłowej; tu wpisuje
  się stan z tarczy; domyślnie ukryte
- **Podaj odczyt** (przycisk) — wysyła wartość encji źródłowej albo wpisaną w polu; bez
  encji źródłowej domyślnie ukryty

Każda encja pochodzi z jednego modułu portalu. Gdy któryś nie odpowie, niedostępne są
tylko jego encje. Przycisk **wodomierza, który już go ma**, działa dalej, bo przed
wysłaniem i tak pyta portal od nowa. Działa nawet wtedy, gdy akurat ten moduł zawiedzie
przy zwykłym odpytywaniu. Nowy wodomierz dostaje swój przycisk dopiero, gdy ten moduł
odpowie choć raz; wodomierz, który pojawi się w portalu później, na przykład po wymianie,
dostaje encje przy najbliższym odpytaniu, bez restartu.

### Koszt wody w panelu Energii

W konfiguracji panelu Energii, w części o zużyciu wody, dodaj jako źródło encję ze stanem
licznika (nakładkę radiową albo **Wodomierz N — stan**), a jako cenę wskaż **Wodomierz N
— cena za m³**. Cena pochodzi z pozycji faktury rozliczanych za m³, więc nowa taryfa
pojawi się z pierwszą fakturą według niej. Opłaty stałe, naliczane za miesiąc, do niej
nie wchodzą. Jeśli przedsiębiorstwo nie nalicza ścieków za wodomierz ogrodowy, jego cena
obejmuje samą wodę.

## Podanie odczytu

```yaml
action: ibok.submit_reading
data:
  reading: 48
```

`meter_id` jest potrzebne tylko wtedy, gdy odczyt można podać dla więcej niż jednego
wodomierza. Opcjonalnie przyjmuje też `note` oraz `reading_date`, która nie może być
z przyszłości ani sprzed poprzedniego odczytu.

**Wysłanie nigdy nie następuje samo.** Błędny odczyt trafia na fakturę i trzeba go potem
prostować z przedsiębiorstwem, więc wywołanie jest zawsze świadome — usługą albo
przyciskiem. Tuż przed wysłaniem integracja pyta portal o aktualny zakres, liczbę cyfr
i poprzedni odczyt — nie ufa danym sprzed kilku godzin — więc literówka nie dojdzie do
przedsiębiorstwa.

Wysłany odczyt widać w portalu w zakładce „Zgłoszenie odczytu”, razem ze statusem. Nie ma go
w „Odczytach” ani w aplikacji mobilnej, dopóki przedsiębiorstwo go nie zatwierdzi. Po
wysłaniu integracja sprawdza w portalu, czy zgłoszenie tam jest, i potwierdza to
powiadomieniem. Jeśli go nie widzi, postępuje jak przy braku odpowiedzi portalu (niżej).

Integracja **odmawia powtórnego wysłania tego samego odczytu tego samego dnia**. Odmawia,
gdy portal już ma taką wartość na ten dzień. Odmawia też, gdy sama go już dziś wysłała.
To drugie pamięta tylko do restartu lub przeładowania integracji, a przeładowuje ją
też każdy zapis opcji.
Odmawia także każdego nowego odczytu, dopóki poprzednie zgłoszenie w portalu oczekuje albo
jest w trakcie realizacji — poprawić albo usunąć je można w portalu, w zakładce
„Zgłoszenie odczytu”. Inna wartość tego samego dnia przechodzi dopiero wtedy, gdy
poprzednie zgłoszenie zostało zatwierdzone albo odrzucone.

Jeśli odczyt wyszedł, a odpowiedź portalu nie dotarła, integracja **nie ponawia wysłania
samodzielnie**: portal mógł go już zapisać, a drugi raz trafiłby na fakturę. Zamiast tego
zakłada zgłoszenie w Ustawienia → Urządzenia i usługi → Naprawy i blokuje dalszą wysyłkę na
ten wodomierz. Blokada zniknie sama, gdy portal przy kolejnym odpytaniu pokaże ten odczyt
albo pokaże, że go odrzucił — odrzucony nie trafia na fakturę i można go wysłać ponownie.
Inaczej sprawdź w portalu, czy odczyt tam jest, otwórz zgłoszenie i potwierdź: to też
zdejmuje blokadę. „Zignoruj” w Naprawach tego nie robi. Zgłoszenie chowa się wtedy pod
„pokaż zignorowane”, ale blokada zostaje, dopóki go faktycznie nie otworzysz i nie
potwierdzisz, albo dopóki portal sam nie pokaże tego odczytu.

Wartość jest ucinana **do dokładności tarczy** tuż przed wysłaniem, tak samo w usłudze,
jak w przycisku. Nakładka radiowa podaje litry, a przedsiębiorstwo zapisuje to, co widać
na liczydle; ile cyfr ono pokazuje, mówi sam portal, sprawdzany tuż przed wysyłką.
Ucięcie, nie zaokrąglenie: przy 48,6 tarcza nadal pokazuje 48, więc i wywołanie usługi
z `reading: 48.6` wyśle 48.

### Przycisk pyta o potwierdzenie

Pierwsze naciśnięcie **nic nie wysyła**. Odpowiada komunikatem z wartością i wodomierzem,
do którego by poszła, a wysyła dopiero drugie naciśnięcie w ciągu minuty:

> Nic nie wysłano. Naciśnij ponownie w ciągu 60 s, aby zgłosić 48 m³ dla wodomierza
> 12345678 (ID iBOK 10001).

Potwierdzenie siedzi w encji, nie na karcie panelu, bo okno potwierdzenia w Lovelace jest
własnością karty — nie zadziałałoby na stronie urządzenia, w skrypcie ani w sterowaniu
głosem. Jego treść jest przy tym statyczna, więc nie pokazałaby wartości.

Pytanie wraca tylko wtedy, gdy zmieni się **liczba, którą widzisz** — bo porównywana jest
wartość już ucięta do dokładności tarczy. Przy wodomierzu odczytywanym w pełnych m³ ruch z
48,002 na 48,004 nadal potwierdza; dopiero przeskok na 49 pyta od nowa.

Potwierdzić może tylko ta sama osoba, która dostała komunikat, i nie w tej samej chwili —
podwójne kliknięcie niczego nie wyśle. Wartość encji źródłowej jest przeliczana z jej
jednostki na m³, a gdy encja nie odzywa się od ponad doby, przycisk odmawia: martwa
nakładka pokazuje ostatni stan, który wyglądałby jak dzisiejszy.

Encja bez własnego znacznika czasu odczytu ma dodatkową osłonę po restarcie Home
Assistanta. Przez pierwsze 15 minut po starcie przycisk jej ufa, bo nakładka mogła
jeszcze nie zdążyć się zgłosić: liczy się wtedy tylko reguła doby, opisana wyżej. Dopiero
po tych 15 minutach odmawia, jeśli `last_reported` tej encji wciąż wskazuje pierwsze
5 minut po starcie. To wygląda jak stan przywrócony z poprzedniej sesji, nie da się go
odróżnić od czujnika, który od restartu milczy. W pierwszych 15 minutach przywrócony
stan więc przejdzie, tak jak świeży.

Reguła łapie tylko restart samego Home Assistanta. Restart samej nakładki radiowej albo
jej brokera, bez restartu HA, jej nie uruchamia: wartość, którą wtedy zgłoszą ponownie,
przycisk przyjmie jak świeżą, choć to ten sam stary odczyt. Sensor szablonowy jako
źródło ma dodatkową pułapkę. Jego `last_reported` rusza dopiero, gdy zmieni się policzona
wartość, nie przy każdym przeliczeniu. Odczyt, który nie drgnął od ponad doby, wygląda
wtedy dla przycisku jak martwe źródło, choć szablon liczy poprawnie.

Bez encji źródłowej wpisujesz stan z tarczy w polu **Odczyt do wysłania** i naciskasz
przycisk dwa razy, jak wyżej. Wpis jest ważny przez dobę i nie przetrwa restartu, żeby
zapomniany odczyt z zeszłego miesiąca nie poszedł jako dzisiejszy. Po wysłaniu pole się
czyści.

## Odpytywanie

Domyślnie co 6 godzin. Odczyty i faktury zmieniają się najwyżej raz dziennie, więc
częściej nie ma czego pobierać, a regulaminy iBOK zakazują działań mogących zakłócić
pracę serwisu. Nie skracaj tego bez powodu.

## Uwagi

Integracja nieoficjalna, niezwiązana z LogicSynergy ani z żadnym przedsiębiorstwem.
Hasło trafia wyłącznie do konfiguracji Home Assistanta i jest wysyłane tylko do
wskazanego portalu, zawsze przez HTTPS. Integracja nie podąża za przekierowaniem, które
prowadzi poza ten portal.

Z włączonym logowaniem na poziomie debug po każdej wysyłce odczytu w logu ląduje surowa
odpowiedź portalu, nieprzefiltrowana. W przeciwieństwie do diagnostyki integracji, gdzie
dane z portalu są zamaskowane, tego logu nie wklejaj wprost do publicznego zgłoszenia.
Przejrzyj go najpierw.

## Rozwój

Zasady zgłoszeń i zmian są w [CONTRIBUTING.md](CONTRIBUTING.md).

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

Minimalna wspierana wersja to **Home Assistant 2026.1** i CI ma osobne zadanie, które
uruchamia te same testy właśnie na niej. Integracja ma nie zacząć po cichu wymagać
nowszego rdzenia.

Wersja w `manifest.json` rośnie z każdym commitem. Przy HACS to jedyny tani dowód, co
komu faktycznie siedzi na dysku.

### Wydania

HACS instaluje z **ostatniego wydania**, a nie z bieżącego stanu gałęzi. Tag musi więc
być tym samym numerem, co `version` w `manifest.json` — jeśli się rozjadą, HACS pokaże
jedną wersję, a `manifest.json` na dysku będzie mówił co innego i nie da się ustalić,
co instancja naprawdę uruchamia.

Wydań nie tworzy się ręcznie. Po wypchnięciu taga workflow sam odpala ruff, testy i
sprawdzenie zgodności taga z manifestem, i dopiero gdy to przejdzie, publikuje wydanie na
GitHubie z treścią adnotacji taga jako opisem. Tag musi więc mieć adnotację (`git tag -a`,
nie sam `git tag`). Bez niej workflow odmówi publikacji. Tag z częścią przedpremierową,
na przykład `v1.0.0-beta.1`, trafia na GitHuba jako wydanie testowe. HACS pokazuje je tylko
tym, którzy włączyli wersje beta dla tego repozytorium.

```bash
git tag -a v0.2.1 -F notatki.md && git push origin v0.2.1
```

`test_manifest.py` pilnuje, żeby numer w manifeście był poprawnym semverem; zgodność
z tagiem i cały zestaw testów sprawdza ten sam workflow wydania, zanim cokolwiek
opublikuje.

## Licencja

MIT — patrz [LICENSE](LICENSE).
