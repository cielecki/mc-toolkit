# Voice Memos — auto-title, bramka jakości i routing (design)

**Data:** 2026-07-04
**Skill:** `mc-toolkit:voicememos` (`plugins/mc-toolkit/skills/voicememos`)
**Status:** design zatwierdzony w brainstormie; czeka na review przed planem implementacji.

---

## Problem

- **69 folderów memos** (od 2026-06-09), `meta.json` nie ma żadnego pola statusu/decyzji — brak śladu „co zrobiono z tym memo".
- Przegląd dzieje się ad-hoc: pojedyncze czaty domenowe wyciągają sobie jedno memo, nie ma jednego miejsca ani polityki.
- **Nazwa folderu = ręczny tytuł z apki Voice Memos** (`ZENCRYPTEDTITLE`), np. „Nagranie 1", „notatka-15" — bezużyteczna jako podstawa czegokolwiek.
- **Silnik transkrypcji jest zahardkodowany na lokalny whisper** — zero eskalacji jakości; silniki cloud to osobne skrypty odpalane ręcznie.

Dwa cele: (1) **przemleć backlog** z decyzją per memo; (2) **routing na przyszłość**, docelowo automatyczny, startowo z zatwierdzaniem każdego kroku.

## Zasady (decyzje nośne)

1. **Treść, nie nazwa.** Każda decyzja (tytuł, klasyfikacja, routing) wychodzi z **całego transkryptu**, nigdy z nazwy folderu.
2. **Stan rozproszony, zero shared file.** Wszystko o danym memo siedzi w jego katalogu (`meta.json`). Widok zbiorczy generowany **on-demand** przez skan folderów — nigdy nie zapisywany centralnie.
3. **Trajektoria zaufania.** v1 pyta przed każdą akcją; reguły awansują z `ZAPYTAJ` na `NIE pytaj` przez edycję pliku tekstowego. Cel = pełna automatyzacja, zapracowana.
4. **Polityka jako czysty tekst.** Routing = ludzko-edytowalna tabela reguł `kryterium → akcja`. LLM jest silnikiem dopasowania. Bez enumów, bez tagów, bez kodu w polityce.
5. **Najpierw lokalnie, eskalacja świadomie.** Lokalny whisper zawsze leci pierwszy (darmowy, prywatny). Eskalacja silnika to **drugi krok, decydowany Z lokalnego transkryptu**.
6. **Prywatność jest per-silnik.** Drabina eskalacji rankowana profilem prywatności; jak wysoko wolno wejść zależy od wrażliwości treści × profilu silnika.

## Pipeline (etapy)

```
sync/transkrypcja (local) → bramka jakości (+ ew. eskalacja silnika) → auto-title (rename folderu) → routing (tabela reguł) → zapis routing_note per-folder
```

### 1. Sync / transkrypcja (istnieje)
`sync.py` → lokalny whisper large-v3 + diaryzacja Sortformer. **Nowe:** utrwalić sygnały jakości do `meta.json` — `speech_seconds` / `vad_ratio` (silero-VAD i tak liczy segmenty) oraz per-word `confidence` (shared engine już je zwraca).

### 2. Bramka jakości (nowe)
Obiektywne sygnały (nie treść) klasyfikują zdrowie transkryptu:
- **healthy** — jest tekst → normalny routing.
- **empty** — VAD ≈ cisza + brak tekstu → **kandydat na archiwum** (naprawdę puste).
- **suspect** — VAD wykrył mowę, ale tekst pusty / zapętlony (znany failure mode whispera) / bardzo niska pewność → **eskalacja silnika**.

Rozróżnienie empty vs suspect jest kluczowe: pusty transkrypt jest dwuznaczny (naprawdę cisza vs. STT zawiódł), a **po samym tekście się tego nie rozróżni** — decyduje VAD.

### 3. Eskalacja silnika (nowe — dziś nie istnieje)
Tylko dla `suspect`. Lokalny przebieg już był (darmowy), więc eskalacja jest świadomym drugim krokiem, decydowanym z lokalnego transkryptu (daje jednocześnie sygnał jakości **i** treść do oceny wrażliwości).

Drabina rankowana prywatnością:

| rung | silnik | profil prywatności |
|---|---|---|
| 0 | **local whisper** (default, zawsze pierwszy) | w 100% na Macu |
| 1 | **OpenAI** gpt-4o-transcribe | **nie trenuje**, auto-delete ≤30 dni; najlepsza cloud-prywatność; eskalacja domyślnie używa wariantu `gpt-4o-transcribe-diarize`, więc mówcy pochodzą z OpenAI (samo `gpt-4o-transcribe` to tylko tekst) |
| 2 | **AssemblyAI** | auto-kasuje transkrypt po pobraniu; **trenuje** domyślnie (jednorazowy opt-out mailem) |
| 3 | **ElevenLabs** | **nie kasuje**, **trenuje**, retencja domyślna; lider jakości na audio telefonicznym |

- **Bramka wrażliwości:** treści wrażliwe (zdrowie / terapia / intymność / finanse / rodzina) capują na rung 0–1 (max OpenAI); ElevenLabs tylko nie-wrażliwe quality-critical.
- **v1 = ZAWSZE ZAPYTAJ** przed jakimkolwiek wysłaniem poza Maca. Pytanie jest świadome: „słaby transkrypt, temat wygląda na X, proponuję OpenAI [nie trenuje, auto-delete] — ok?".

### 4. Auto-title (nowe)
Jeden przebieg LLM po całym transkrypcie → **opisowy tytuł**. Folder zmienia nazwę na `YYYY-MM-DD-<wygenerowany-slug>`. Oryginalny tytuł z apki zostaje w `meta.json` jako `original_title`. (Title + klasyfikacja + propozycja dyspozycji mogą być tym samym przebiegiem — szczegół implementacji.)

### 5. Routing (nowe)
LLM czyta cały transkrypt, dopasowuje do **tabeli reguł z pliku tekstowego** (`kryterium → akcja`). Reguła `NIE pytaj` → wykonanie inline; `ZAPYTAJ` → propozycja + approve/popraw/wykonaj. Proste akcje inline (archiwum / task / krótka notatka); złożone → **przekazanie do skilla domenowego** (np. sesja zdrowotna → skill zdrowotny, sprawy firmowe → skill korporacyjny). Zapis `routing_note` + `status` do `meta.json` tego memo.

## Model danych (dodatki do per-folder `meta.json`)

| pole | wartości | znaczenie |
|---|---|---|
| `original_title` | string | tytuł z apki Voice Memos (zachowany przy rename) |
| `speech_seconds` / `vad_ratio` | number | sygnał „ile było mowy" z VAD |
| `transcript_health` | `healthy` \| `empty` \| `suspect` | wynik bramki jakości |
| `engine` | `whisper-local` \| `openai` \| `assemblyai` \| `elevenlabs` | silnik finalnego transkryptu |
| `status` | `needs-routing` → `routed` \| `archived` \| `needs-attention` | stan przetwarzania (`needs-attention` = suspect, którego nie dało się rozwiązać eskalacją/lokalnie — czeka na Ciebie) |
| `routing_note` | wolny tekst | **„co zrobiono i dokąd"** (dłuższa narracja może spillować do `routing.md` w folderze) |

## Pliki polityki

- **`references/routing.md`** (w pluginie, wersjonowane) — **opis procesu**: jak działa faza routingu, drabina eskalacji + profile prywatności per-silnik. Generyczne, nie zawiera prywatnych ścieżek.
- **`data/voicememos/routing-rules.md`** (lokalnie, gitignore) — **prywatna tabela reguł** z prywatnymi ścieżkami domen.

Format reguły:
```markdown
- Kryterium: <opis, oceniany względem CAŁEGO transkryptu>
  Akcja: <co zrobić, dokąd; ZAPYTAJ | NIE pytaj>
```

Przykład (seed — rozwijany w czasie):
```markdown
- Kryterium: przypadkowe/ambientowe nagranie bez treści (VAD potwierdza ciszę)
  Akcja: status → archived, routing_note „śmieć/przypadkowe"; ZAPYTAJ (dopóki detektor pustego nie zapracuje na zaufanie).

- Kryterium: robocza rozmowa o sprawach firmowych / finansowych
  Akcja: wyciągnij action items → Todoist; podsumowanie → work/…; ZAPYTAJ.

- Kryterium: sesja zdrowotna/terapeutyczna (np. wizyta u specjalisty)
  Akcja: transkrypt → wellbeing/<domena>/materialy/transkrypty + krótka refleksja; ZAPYTAJ. NIGDY nie eskaluj do clouda poza OpenAI.
```

## Sterowanie / UX

- **Jeden flow:** odpalasz skill → sync (transkrypcja) → bramka jakości → auto-title → routing, w tej samej sesji.
- **v1 interaktywne:** proponuje per memo, Ty approve/popraw/wykonaj. Reguły `NIE pytaj` (na start tylko oczywiste) lecą cicho.
- `sync.py` sam ustawia `needs-routing`; faza routingu (LLM) domyka resztę.

## Backlog

69 istniejących folderów = **pierwsza partia przez ten sam pipeline** (auto-title + reguły po kolei), nie osobny mechanizm. Jeden przebieg bulk, drenowany z zatwierdzaniem.

## Poza zakresem (YAGNI)

- Pełna automatyzacja od dnia 1 (świadomie odroczona — zaufanie zapracowane).
- Centralny index/dashboard jako **zapisany** stan (tylko generowany on-demand).
- Przebudowa diaryzacji.
- Osobna sesja pull `/memos` (zwinięta w flow syncu; do rozważenia później, jeśli interaktywność w trakcie syncu okaże się męcząca).

## Do wypracowania później

- Docelowy początkowy zestaw reguł (seed kilku, rośnie z czasem).
- Heurystyka wykrywania pętli powtórzeń i progi words-per-minute dla `suspect`.
- Ostrożność przy rename folderu w trakcie pipeline'u (Read-tracking harnessa, ew. referencje do starej ścieżki).
