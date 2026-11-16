# Bulgarian Localization Review — MediCore Clinical Platform

**Review date:** 14 November 2026  
**Dictionary:** `frontend/assets/js/i18n-bg.js`  
**Engine:** `frontend/assets/js/i18n.js`  
**Scope:** All 1,445 machine-generated Bulgarian entries, plus previously missing HTML/JS strings.

---

## 1. Summary

| Metric | Result |
|---|---|
| Original dictionary entries | **1,445** |
| Final dictionary entries | **1,595** |
| Existing values corrected | **147** |
| Missing strings added | **99** |
| Entries removed | **0** |
| Placeholder-integrity failures | **0** |
| Missing HTML text nodes after review | **0** |
| Core terminology regressions in tests | **0** |

The review focused on the highest-risk language: navigation, clinical workflows, appointment terminology, laboratory result flags, staff/account administration, audit logging, error messages, and the legal pages. All entries were checked mechanically for empty values and placeholder preservation; 147 existing translations were manually corrected and 99 missing strings were added. General UI strings outside the high-risk set were checked for consistency but have not all been verified word-by-word against an external dictionary.

No clinical or legal approval is claimed.

---

## 2. Terminology glossary

The following glossary is now applied consistently across the dictionary.

| English | Bulgarian | Notes |
|---|---|---|
| Patient | Пациент | |
| Patients | Пациенти | |
| Patient record | Медицинска карта на пациента | Print/PDF title and record context |
| Doctor | Лекар | |
| Nurse | Медицинска сестра | |
| Administrator / Admin | Администратор | |
| Staff | Персонал | Collective noun |
| Staff member | Служител | Individual person |
| Staff account | Профил на служител | Changed from “акаунт” |
| Account | Профил | User-facing account terminology |
| Appointment | Среща | Scheduling entity |
| Appointments | Срещи | |
| Appointment type | Вид среща | |
| Encounter | Посещение | Clinical encounter |
| Visit | Посещение | |
| Visit type | Вид посещение | |
| Clinical encounter | Клинично посещение | |
| Follow-up visit | Контролно посещение | Standard clinical Bulgarian |
| Routine check-up | Профилактичен преглед | |
| Preventive care visit | Посещение за профилактика | |
| General consultation | Обща консултация | |
| Specialist consultation | Специализирана консултация | |
| Urgent consultation | Спешна консултация | |
| Annual physical examination | Годишен профилактичен преглед | |
| Laboratory results | Лабораторни резултати | |
| Reference range | Референтен диапазон | |
| Normal | В норма | Lab flag |
| Abnormal | Извън нормата | Lab flag; replaced “абнормен” |
| High | Над нормата | Lab flag |
| Low | Под нормата | Lab flag |
| Prescription | Рецепта | |
| Prescriptions | Рецепти | |
| Medication | Лекарство | Replaced “медикамент” |
| Active medications | Активни лекарства | |
| Diagnosis | Диагноза | |
| Diagnoses and conditions | Диагнози и състояния | |
| Medical history | Медицинска история | |
| Messages | Съобщения | |
| Inbox | Входящи | |
| AI Assistant | AI асистент | “AI” retained as the product term |
| AI Patient | AI пациент | |
| Voice scheduling | Гласово планиране | |
| Transcription | Транскрипция | |
| Appointment proposal | Предложение за среща | |
| Availability (doctor schedule) | Работно време | Replaced “наличност” in scheduling context |
| Time off | Отсъствие | |
| Audit log | Одитен журнал | |
| Audit events | Одитни събития | |
| System logs | Логове | Preferred short term for the System Logs page |
| Staff management | Управление на персонала | |
| Permissions / access control | Права за достъп / контрол на достъпа | |
| Backend / server | Сървър | Replaced “бекенд” in user-facing text |
| Data controller | Администратор на лични данни | GDPR term |
| Data subject | Субект на данни | Legal text |
| Supervisory authority | Надзорен орган | Legal text |
| Legal bases | Правни основания | Legal text |
| International transfers | Международни трансфери | Legal text |
| Warranty | Гаранция | Legal text |
| Liability | Отговорност | Legal text |

---

## 3. Main corrections

### 3.1 Appointment / encounter terminology

The machine-generated dictionary used three different concepts for the same scheduling object:

- `Appointment` → “Записване”
- `Appointments` → “Записвания”
- narrative text → “среща”

This was inconsistent and confusing: “записване” can mean “recording” or “booking”, while the application means a scheduled clinical appointment.

**Corrected to:** `Среща` / `Срещи` / `Вид среща`, with `Encounter` and `Visit` kept as “Посещение”.

Examples:

| English | Before | After |
|---|---|---|
| Appointment | Записване | Среща |
| Appointments | Записвания | Срещи |
| Appointment type | Тип на срещата | Вид среща |
| Appointment details updated | Данните на срещата са актуализирани | Данните за срещата са актуализирани |
| That time conflicts with an existing appointment. | Това време конфликтира със съществуваща среща. | Това време се припокрива със съществуваща среща. |

### 3.2 Laboratory result terminology

“Абнормен” is understandable in specialist literature but is not the natural patient-facing or general clinical-interface term. The interface now uses “извън нормата”, and the flags use “над нормата” / “под нормата” / “в норма”.

Examples:

| English | Before | After |
|---|---|---|
| Abnormal | Абнормен | Извън нормата |
| Abnormal results | Абнормни резултати | Резултати извън нормата |
| Most frequent abnormal tests | Най-чести абнормни изследвания | Най-чести изследвания с отклонения |
| Normal | Нормално | В норма |
| High | Високо | Над нормата |
| Low | Нисък | Под нормата |
| Normal + high + low + unclassified | Нормални + високи + ниски + некласифицирани | В норма + над нормата + под нормата + некласифицирани |

Print/PDF flags were corrected from English `HIGH ↑` / `LOW ↓` to `НАД НОРМАТА ↑` / `ПОД НОРМАТА ↓`, and `Resulted …` to `Резултат …`.

### 3.3 “Backend” → “server”

The user-facing interface should not expose implementation jargon. “Бекенд” was replaced with “сървър” throughout the UI and error messages.

Examples:

| English | Before | After |
|---|---|---|
| Backend unreachable | Backend недостъпен | Сървърът е недостъпен |
| Access is enforced by the backend. | Достъпът се налага от бекенда. | Достъпът се контролира от сървъра. |
| The backend stores only a hash… | Бекендът съхранява само хеш… | Сървърът съхранява само хеш… |

### 3.4 Account / staff terminology

The dictionary mixed “акаунт” and “профил”, and “персонал” and “служител”. The interface now uses:

- **профил** for a user account;
- **служител** for an individual staff member;
- **персонал** for the collective staff group.

Examples:

| English | Before | After |
|---|---|---|
| Staff accounts | Акаунти на служители | Профили на служители |
| Active staff accounts | Активни профили на персонала | Активни профили на служители |
| Create a staff account | Създаване на акаунт за служител | Създаване на профил на служител |
| Account status | Статус на акаунта | Статус на профила |
| Add Staff | Добавяне на персонал | Добави служител |

### 3.5 Availability / slots

“Наличност” is a literal translation of “availability” and is unnatural for a doctor’s schedule. In the appointments context it is now “работно време”; “слот” is now “час”.

Examples:

| English | Before | After |
|---|---|---|
| Availability | Наличност | Работно време |
| Save availability | Запази наличност | Запази работното време |
| No slots for this date… | Няма свободни слотове… | Няма свободни часове… |
| Slot increment | Стъпката на слота | Стъпката на часовете |

The legal phrase “Availability and changes” remains “Наличност и промени” because it refers to service availability, not the doctor’s schedule.

### 3.6 Audit terminology

“Одиторски” means “of an auditor” (a person), not “audit”. The interface now uses “одитен/одитни”.

Examples:

| English | Before | After |
|---|---|---|
| Raw audit metadata | сурови одиторски метаданни | сурови одитни метаданни |
| Loading audit events… | Зареждане на одиторски събития… | Зареждане на одитни събития… |

### 3.7 Medication terminology

“Медикамент” was replaced with the more natural “лекарство”.

Examples:

| English | Before | After |
|---|---|---|
| Active medications | Активни медикаменти | Активни лекарства |
| Medication Review | Преглед на медикаментите | Преглед на лекарствата |
| Current medications | текущи медикаменти | текущи лекарства |

### 3.8 Page titles and brand consistency

Page titles mixed translated and untranslated brand text, and the System Logs page used both “Системни журнали” and “Системни дневници”. The preferred short Bulgarian term is now **Логове**.

Corrected to:

- `Dashboard — Клинична платформа MediCore`
- `Patients — Клинична платформа MediCore`
- `Appointments — MediCore Clinical Platform` → `Срещи — Клинична платформа MediCore`
- `Messages — MediCore Clinical Platform` → `Съобщения — Клинична платформа MediCore`
- `Analytics — MediCore Clinical Platform` → `Анализи — Клинична платформа MediCore`
- `System Logs — MediCore Clinical Platform` → `Логове — Клинична платформа MediCore`
- `Staff Management — MediCore Clinical Platform` → `Управление на персонала — Клинична платформа MediCore`
- `Terms and Conditions — Условия и правила — Клинична платформа MediCore`

### 3.9 Missing strings

The original extraction filter skipped strings containing parentheses or long legal paragraphs. This review added **99 missing entries**, including:

- `Transcript (editable)` → `Транскрипт (редактируем)`
- `Transcript language (auto-detect or manual)` → `Език на транскрипта (автоматично или ръчно)`
- `Password (optional)` → `Парола (по избор)`
- `Role (optional)` → `Роля (по избор)`
- `Start (UTC)` / `End (UTC)` → `Начало (UTC)` / `Край (UTC)`
- `(No subject)` → `(Без тема)`
- `(no question text)` → `(без текст на въпроса)`
- All long legal paragraphs in `privacy.html` and `terms.html`
- The full speech-language restriction message
- Print labels `HIGH ↑`, `LOW ↓`, `Resulted`

After this pass, **0 HTML text nodes remain untranslated**.

---

## 4. Dynamic translations and pluralization

The existing interpolation syntax was preserved. `frontend/assets/js/i18n.js` patterns were corrected for Bulgarian singular/plural forms and tested with **0, 1, 2, 5, 11, 21, 25, and 101**.

Examples:

| English | Bulgarian |
|---|---|
| 0 minutes | 0 минути |
| 1 minute | 1 минута |
| 2 minutes | 2 минути |
| 21 minutes | 21 минути |
| 101 minutes | 101 минути |
| 0 years | 0 години |
| 1 year | 1 година |
| 2 years | 2 години |
| 21 years | 21 години |
| 1 unread | 1 непрочетено |
| 2 unread | 2 непрочетени |
| 1 possible patient — choose one | 1 възможен пациент — изберете един |
| 2 possible patients — choose one | 2 възможни пациенти — изберете един |
| 1 result · complete available record | 1 резултат · пълен наличен запис |
| 2 results · complete available record | 2 резултата · пълен наличен запис |

Status transitions:

`Status: Scheduled → Completed` → `Статус: Планиран → Завършен`

All interpolation variables such as `{seconds}`, `{detail}`, `{code}`, `{date}`, and `{name}` are preserved. The regression test verifies that every placeholder in a dictionary key appears in the corresponding value.

---

## 5. Clinical-data preservation

The translation engine still translates only exact UI phrases and explicit patterns. It does not translate:

- patient names;
- MRNs;
- clinical notes;
- laboratory values or units;
- medication records;
- recorded diagnoses;
- AI-generated answer text;
- voice transcript content.

Regression tests assert that `Елена Георгиева`, `MRN-0001`, and `Glucose 5.5 mg/dL` remain unchanged when switching to Bulgarian.

---

## 6. Print/PDF localization

Verified:

- Patient-record labels follow the selected language.
- `Patient Medical Record` → `Медицинска карта на пациента`.
- `Patient Information` → `Информация за пациента`.
- `Laboratory Results` → `Лабораторни резултати`.
- `HIGH ↑` → `НАД НОРМАТА ↑`.
- `LOW ↓` → `ПОД НОРМАТА ↓`.
- `Resulted …` → `Резултат …`.
- PDF footer/page numbers use the `html[lang='bg']` CSS variables.
- The print container is translated before the browser print dialog opens.
- Clinical values, names, and MRNs remain unchanged.

---

## 7. Legal pages

`privacy.html` and `terms.html` are now fully covered by the Bulgarian dictionary, including the long paragraphs that were previously skipped.

The existing statement that the English text is authoritative is preserved and displayed in Bulgarian:

> Това е български превод за удобство. При несъответствие меродавен е английският текст.

Legal terminology used in the translation:

- controller → **администратор на лични данни**
- data subject → **субект на данни**
- supervisory authority → **надзорен орган**
- legal bases → **правни основания**
- data portability → **преносимост на данните**
- restriction of processing → **ограничаване на обработването**
- international transfers → **международни трансфери**
- warranties → **гаранции**
- liability → **отговорност**

The legal meaning was not intentionally changed. The translation still requires review by qualified Bulgarian legal counsel before production use.

---

## 8. External sources consulted

The following official/supplementary sources were accessed during the review and used as terminology guidance. They were not bulk-copied into the repository.

| Source | URL | Used for |
|---|---|---|
| БЕРОН — Bulgarian Language Resources Online | https://beron.mon.bg/ | Official spelling and language norms |
| Institute for Bulgarian Language — Language Consultations | https://ibl.bas.bg/ezikovispravki/ | Grammar, punctuation, standard usage |
| Institute for Bulgarian Language — Online Dictionary | https://ibl.bas.bg/rbe/ | Vocabulary and word meaning |
| Official Bulgarian Spelling Dictionary information | https://ibl.bas.bg/ezikovispravki/publicacii/rechnitsi/ofitsialen-pravopisen-rechnik-na-blgarskiya-ezik | Normative spelling reference |
| National Center of Public Health and Analyses (НЦОЗА) | https://ncpha.government.bg/ | Healthcare terminology and classifications |
| ICD-10 page, НЦОЗА | https://ncpha.government.bg/index/301-mkb-10-mejdunarodna-statisticheska-klasifikacia-na-bolestite-i-problemite-svurzani-sus-zdraveto.html | Diagnosis/classification terminology |
| ICD-10 search, НЦОЗА | https://ncpha.government.bg/mk10-search | Diagnosis terminology lookup |
| Bulgarian Drug Agency (ИАЛ) | https://www.bda.bg/bg/ | Medicinal-product and pharmaceutical terminology |
| BDA medicinal-product registers | https://bda.bg/bg/регистри/регистри-на-лекарствени-продукти | Authorized product names and pharmaceutical wording |
| National Health Insurance Fund (НЗОК) | https://www.nhif.bg/ | Medical activities and healthcare administration |
| НЗОК National Framework Contract — medical activities | https://nhif.bg/bg/nrd/2026-2028/medical | Clinical pathways and medical-service terminology |
| MEDICINE.BG Medical Dictionary | https://www.medicine.bg/medicinski-rechnik/ | Supplementary medical terminology |

**Access note:** All listed sites were reachable during the review. The BDA site and some НЦОЗА pages are dynamic; they were used for terminology guidance, not as machine-readable dictionaries. No claim is made that every individual dictionary entry was verified against a specific external entry.

---

## 9. Terms requiring clinical review

The following terms should be confirmed by a Bulgarian clinician before production use:

- **Среща** vs **посещение** for appointment/encounter in the specific clinical workflow.
- **Контролно посещение** for follow-up visit.
- **Профилактичен преглед** for routine check-up.
- **Посещение за профилактика** for preventive care visit.
- **Извън нормата / над нормата / под нормата** for laboratory flags.
- **Оценяеми резултати** for evaluable results.
- **Референтен диапазон** for reference range.
- **Диагностично изследване** for diagnostic evaluation.
- **Преглед на лекарствата** for medication review.
- **Сърдечна честота** and **кръвно налягане** in vitals and trends.
- The role descriptions for Doctor, Nurse, and Admin.

These terms are linguistically natural but their final clinical suitability depends on the deployment’s actual clinical model and local practice.

---

## 10. Terms requiring legal review

The following must be reviewed by qualified Bulgarian legal counsel:

- **Администратор на лични данни** as the translation of “controller”.
- **Правни основания**, **жизненоважни интереси**, **легитимни интереси**, **спазване на законово задължение**.
- **Права на субектите на данни**, **преносимост**, **ограничаване на обработването**, **възражение**.
- **Международни трансфери** and transfer safeguards.
- **Съхранение** and retention periods.
- **Гаранции**, **отговорност**, **непреки/последващи/наказателни вреди**.
- The GDPR/HIPAA references and the statement that the English text is authoritative.
- The Bulgarian translation note on `privacy.html` and `terms.html`.

The translation is a good-faith localization, not legal advice or legal approval.

---

## 11. Decision log — significant before/after examples

| English source | Before | After | Reason |
|---|---|---|---|
| Appointment | Записване | Среща | “Записване” means booking/recording; the entity is a scheduled meeting |
| Appointments | Записвания | Срещи | Consistency with “Appointment” |
| Appointment type | Тип на срещата | Вид среща | Natural Bulgarian collocation |
| Availability | Наличност | Работно време | “Наличност” is literal; the screen manages working hours |
| Abnormal | Абнормен | Извън нормата | Natural clinical-interface wording |
| Normal | Нормално | В норма | Lab flag consistency |
| High | Високо | Над нормата | Lab flag consistency |
| Low | Нисък | Под нормата | Lab flag consistency |
| Staff accounts | Акаунти на служители | Профили на служители | “Профил” is natural for a user account |
| Active staff accounts | Активни профили на персонала | Активни профили на служители | Individual staff members |
| Backend unreachable | Backend недостъпен | Сървърът е недостъпен | Avoid implementation jargon |
| Raw audit metadata | сурови одиторски метаданни | сурови одитни метаданни | “Одиторски” means “of an auditor” |
| Active medications | Активни медикаменти | Активни лекарства | Natural Bulgarian |
| Follow-up Visit | Последващо посещение | Контролно посещение | Standard clinical term |
| Routine Check-up | Рутинен преглед | Профилактичен преглед | Standard clinical term |
| Medication Review | Преглед на медикаментите | Преглед на лекарствата | Natural Bulgarian |
| System Logs — MediCore Clinical Platform | Системни дневници — MediCore Clinical Platform | Логове — Клинична платформа MediCore | Terminology and brand consistency; preferred short term is “Логове” |
| Transcript (editable) | (missing) | Транскрипт (редактируем) | Missing HTML label |
| HIGH ↑ | HIGH ↑ | НАД НОРМАТА ↑ | Print/PDF localization |
| LOW ↓ | LOW ↓ | ПОД НОРМАТА ↓ | Print/PDF localization |
| Resulted … | Resulted … | Резултат … | Print/PDF localization |
| 1 year | pattern produced “1 години” | 1 година | Singular/plural correction |
| 1 unread | pattern produced “1 непрочетени” | 1 непрочетено | Singular/plural correction |
| 1 possible patient — choose one | 1 възможни пациенти | 1 възможен пациент — изберете един | Singular/plural correction |
| 2 possible patients — choose one | 2 възможни пациенти | 2 възможни пациенти — изберете един | Verified plural form |

---

## 12. Tests executed

| Test | Result |
|---|---|
| `node frontend/tests/i18n.test.mjs` | **9/9 passed** |
| `node frontend/tests/smoke.mjs` | **256/256 passed** |
| `node frontend/tests/render-smoke.mjs` | **107/107 passed** |
| `node frontend/tests/print-record.test.mjs` | **9/9 passed** |
| `node frontend/tests/graphify.test.mjs` | **4/4 passed** |
| `node frontend/tests/markdown.test.mjs` | **21/21 passed** |
| `python3 scripts/security_check.py` | **PASS — 0 findings, 0 warnings** |
| Last full backend suite | **705 passed, 5 skipped** |

The localization test verifies:

- dictionary integrity and non-empty values;
- placeholder preservation;
- core terminology consistency;
- EN/BG switching and English restoration;
- rapid EN/BG switching stability and final-language correctness;
- dynamic counts, durations, and status transitions;
- Bulgarian pluralization for 0/1/2/5/11/21/25/101;
- clinical identifiers and values are never translated;
- attribute translation, browser-language default, and `medicore-language` persistence.

---

## 13. Remaining limitations

1. **Not every one of the 1,595 entries was manually verified against an external dictionary entry.** High-risk clinical, administrative, and legal terminology received targeted review; general UI text received consistency and integrity checks.
2. **A native Bulgarian clinical reviewer is still required** for the terms listed in section 9.
3. **Qualified Bulgarian legal counsel is still required** for the legal pages. The English text remains authoritative.
4. **Some dynamic templates may still fall back to English** if their rendered text does not match an exact dictionary entry or a defined pattern. The common count/duration/status patterns are covered.
5. **AI-generated answer text and stored clinical data are not translated.** This is intentional.
6. **The Bulgarian dictionary is large (1,595 entries)** and should be proofread as part of a release process.
7. **No claim of regulatory, clinical, GDPR, or HIPAA approval is made.**

---

## 14. Files changed

- `frontend/assets/js/i18n-bg.js` — corrected and expanded Bulgarian dictionary.
- `frontend/assets/js/i18n.js` — pluralization/pattern corrections.
- `frontend/assets/js/print-record.js` — translated print `Resulted` label and language-aware print flags.
- `frontend/tests/i18n.test.mjs` — new localization regression suite.
- `.github/workflows/ci.yml` — runs the localization suite in CI.
- `frontend/tests/print-record.test.mjs` — Bulgarian print assertions.

No clinical database data, API behavior, authentication, authorization, or AI-generated content was changed.
