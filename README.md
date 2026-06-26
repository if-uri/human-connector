# `human://` — konektor zadań dla ludzi (Human Task Provider)

Przykładowy **serwis usługowy dla ludzi** dla `urirun`: czyni z „poproś człowieka, żeby
coś zrobił / potwierdził / autoryzował i poczekaj na wynik" **adresowalny krok URI**.
Dzięki temu thin-driver flow dispatch'uje `human://…` dokładnie tak samo jak `cdp://`,
`robot://` czy `inventory://` — człowiek staje się pierwszoklasowym węzłem w tej samej
przestrzeni URI co maszyny i roboty. To jest spoiwo dla świata fizycznego, gdzie potrzebna
jest współpraca **ludzi, maszyn i robotów**.

> Granica dostawy: całość ląduje przez lokalny Claude Code. Tu dostajesz **runnable
> scaffolds** — działający pakiet konektora, serwis WWW dla pracownika oraz demo, które
> opowiada historię bez podłączania prawdziwego robota.

---

## Po co to istnieje (luka, którą domyka)

Mapy projektu mówią wprost: recovery potrafi dziś zwrócić doraźną „akcję człowieka"
(np. `provide-node-url`), ale **nie jest ona pierwszoklasowa** — nie ma jej w przestrzeni
URI, nie da się jej zaplanować, zbramkować ani odwrócić jak każdego innego kroku. Z drugiej
strony plany (Faza 6) chcą, by „zdegradowane-ale-naprawialne" stało się
`acquire → prove → retry`, wystawione jako jedno-tapowy item gotowości, gdzie **człowiek
jest pytany raz na środowisko**.

`human://` realizuje dokładnie to: czyni interakcję z człowiekiem zwykłym connectorem z
route'em `satisfy`, który dołącza po URI — bez specjalnego przypadku w silniku.

---

## Powierzchnia URI (autorytet `{node}` = fizyczna komórka, np. `cell-a`)

| URI | rodzaj | co robi |
|---|---|---|
| `human://{node}/task/command/request` | command | zgłasza zadanie człowiekowi; zwraca `pending` + `next:{kind:"await", on:"human", poll, payload:{taskId}}` |
| `human://{node}/task/query/poll` | query | `pending` \| `done` \| `declined`; krok terminalny niesie artefakt-dowód + `inverse` |
| `human://{node}/task/command/resolve` | command | strona pracownika — serwis WWW POST'uje tutaj wynik (Done / Decline + zdjęcie/nota) |
| `human://{node}/task/command/cancel` | command | odwracalność / sprzątanie (anuluje otwarte zadanie) |
| `human://{node}/precondition/command/satisfy` | command | **provider Fazy 6**: grant per-env; jeśli pamięć ma pasujący fingerprint środowiska (brak dryfu) → `status:"satisfied", recalled:true` (człowiek **pominięty**); inaczej zgłasza zadanie grantu + `next:{kind:"acquire", provider, poll, payload}` |

Wszystkie pięć route'ów to jedno źródło prawdy: `ROUTES` w `connector.py` (template → handler).
Czytają je dwaj konsumenci: prawdziwy `urirun.Connector` (gdy pakiet jest zainstalowany)
oraz lokalny shim w demo.

---

## Uczciwa granica bezpieczeństwa: `per-env` vs `per-instance`

To jest serce projektu. Nie wolno cache'ować fizycznej pracy.

- **`per-env`** — grant / login / kalibracja / stały osąd: to **fakt o środowisku**.
  Pytany **raz na env**, odtwarzany na pasującym twinie, pytany ponownie **tylko przy
  dryfie**. To jest recall pod nazwą (Faza 1/6).
  _Przykład w demo:_ `operator-grant` — „autoryzuj komórkę robota na tę zmianę".

- **`per-instance`** — konkretna akcja fizyczna: „zapieczętuj **TĘ** paczkę", „potwierdź,
  że **TEN** obszar jest wolny". **Nigdy** nie jest recall'owana — zawsze pytana od nowa,
  cache służy wyłącznie do audytu. Cache'owanie tego byłoby **niebezpieczne**.
  _Przykłady w demo:_ `safety-clear` (potwierdzenie bezpieczeństwa) oraz `inspect-seal`
  (akcja: obejrzyj i zapieczętuj).

Zakodowane jako pole zadania `scope: "per-env" | "per-instance"`. Handler `satisfy`
**odmawia** obsługi potrzeby `per-instance` — fizyczna praca nie jest preconditionem do
„spełnienia z pamięci".

Demo udowadnia to wprost: przy biegu #2 (ten sam intent, ten sam env) `operator-grant`
jest **RECALLED** (człowiek nie pytany), a `safety-clear` i `inspect-seal` są pytane
**oba razy** — poprawnie i bezpiecznie.

---

## Co jest w pakiecie

```
human-connector/
├── urirun_connector_human/          # pakiet konektora (czysta biblioteka standardowa)
│   ├── connector.py                 # ROUTES (źródło prawdy), local_dispatch, build_connector()
│   ├── handlers.py                  # czyste handlery fn(payload, store, memory) -> koperta
│   ├── store.py                     # TaskStore (sqlite, plikowy) — zadania + zdarzenia
│   ├── episode.py                   # TwinMemory — known-good epizody + dowody per-env, dryf
│   ├── _envelope.py                 # ok()/fail()/verification() w kształcie kopert urirun
│   ├── bindings.json                # deklaratywne bindings (adapter local-function)
│   ├── surface.py                   # ⭐ SERWIS WWW dla pracownika (telefon, duże przyciski, foto)
│   └── __init__.py
├── flows/
│   └── fulfillment_cell.flow.json   # zapisany plan flow (homoikoniczny atom — Faza 2)
└── demo/
    ├── demo.py                      # uruchamialne demo (3 tryby)
    └── _shim.py                     # runtime stand-in: mockuje inventory/robot/dispatch + szyna zdarzeń
```

Konektor **nie** posiada dashboardu ani bazy danych hosta — zwraca koperty i deskryptory
artefaktów, dokładnie jak każdy inny connector urirun. `surface.py` to mały, samodzielny
serwis pracownika (jak `phone scanner` w hoście), a nie część rdzenia.

---

## Demo flow: realizacja zamówienia w komórce fizycznej

`flows/fulfillment_cell.flow.json` przeplata maszyny, roboty i ludzi w jednym planie URI:

1. `locate-stock` → `inventory://host/stock/query/locate` — maszyna (znajdź SKU, bin C-3)
2. `operator-grant` → `human://cell-a/precondition/command/satisfy` — **człowiek, per-env** (recall-cacheable)
3. `safety-clear` → `human://cell-a/task/command/request` — **człowiek, per-instance** (bezpieczeństwo)
4. `robot-transfer` → `robot://cell-a/arm/command/transfer` — robot (bramkowany, deklaruje `inverse`)
5. `inspect-seal` → `human://cell-a/task/command/request` — **człowiek, per-instance** (akcja; deklaruje ludzki inverse „rozpieczętuj/zwróć")
6. `mark-shipped` → `dispatch://host/orders/command/mark-shipped` — maszyna

### Uruchomienie

```bash
cd human-connector

# 1) Historia recall: dwa biegi na tym samym twinie.
#    Bieg #1 — człowiek autoryzuje; biec #2 — grant per-env jest RECALLED, praca fizyczna pytana od nowa.
python demo/demo.py

# 2) Odwracalność: ostatni krok (carrier API) odrzuca przesyłkę → rollback inverse'ów (newest-first).
#    Robot cofa transfer, człowiek jest proszony o „UNDO: rozpieczętuj & zwróć".
python demo/demo.py --fail-last

# 3) Tryb na żywo: prawdziwy człowiek rozwiązuje zadania przez telefon.
python demo/demo.py --serve --port 8797
#    → otwórz http://localhost:8797/?node=cell-a na telefonie pracownika
```

### Sam serwis pracownika (bez demo)

```bash
python -m urirun_connector_human.surface          # nasłuch na :8797
# otwórz http://localhost:8797/?node=cell-a
```

Ciemny UI, duże cele dotykowe, przechwycenie zdjęcia (→ zapis dowodu jako artefakt),
nota, przyciski **Done / Decline**. To jest most między koperty URI a realne dłonie.

---

## Mapowanie na roadmapę

- **Faza 1 (domknięcie pętli reuse)** — `TwinMemory` keyuje known-good epizody i dowody
  `intent × env`, unieważniane dryfem. Drugi bieg per-env grantu jest natychmiastowy.
- **Faza 2 (`flow://` jako artefakt)** — `fulfillment_cell.flow.json` to zapisany,
  referowalny atom planu; thin-driver go wykonuje. Krok `human://` jest węzłem obok faktów
  i działań (homoikoniczność).
- **Faza 3 (sesja / promote-to-skill)** — bieg jest nagrywany jako epizod w Experience;
  „skill" = odtwarzany konkretny epizod kluczowany `intent × env`, re-planowany przy dryfie
  (nie parametryzowana generalizacja — uczciwie).
- **Faza 6 (preconditions/providery first-class)** — route `satisfy` zamienia
  „zdegradowane-ale-naprawialne" w `acquire → prove → retry`; człowiek pytany **raz na env**,
  wystawiony jako jedno-tapowy item gotowości.

---

## Wpięcie do prawdziwego drzewa urirun

1. **Skopiuj** `urirun_connector_human/` do swoich connectorów (albo zainstaluj jako pakiet).
2. **Zamień import shimu na prawdziwy runtime.** W `connector.py` funkcja `build_connector()`
   już próbuje użyć prawdziwego `urirun.Connector`, gdy jest importowalny; w przeciwnym razie
   działa lokalny `local_dispatch`. W produkcji rejestrujesz przez `build_connector()` **albo**
   przez `bindings.json`.
3. **Zarejestruj konektor.** Albo dekoratorami (`@connector.command(...)` / `.handler(...)` —
   dokładne nazwy zależą od wersji `urirun`, dostosuj), albo deklaratywnie przez
   `bindings.json` z adapterem `local-function` (ref:
   `urirun_connector_human.connector:local_dispatch`) i `urirun host deploy --bindings`.
4. **Zweryfikuj.** Uruchom `urirun connectors lint` oraz `sync-manifest`, żeby manifest
   nie dryfował od kodu (rejestr Fazy 0 jako strażnik).
5. **Podmień mocki w `demo/_shim.py`** (`inventory://`, `robot://`, `dispatch://`) na
   prawdziwe konektory swoich maszyn/robotów. Tylko `human://` pochodzi z tego pakietu —
   reszta to twoja siatka.

### Kontrakt koperty (dla zgodności z silnikiem)

Handlery zwracają przenośne koperty w kształcie urirun:
`{ok, connector:"human", status, verification, recovery, next, inverse, artifact, error}`,
ze statusami `done | blocked | retryable | failed`. Pole `next.kind`
(`await` / `acquire`) jest czytane przez thin-driver (`flow_thin._next_kind`); `inverse`
zasila odwracalność (`_extract_inverse` / `_resolve_inverse_uri`). Dzięki temu krok
`human://` jest dla silnika nieodróżnialny od dowolnego innego kroku.
