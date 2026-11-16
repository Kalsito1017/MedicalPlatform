# ICD-10 Diagnosis Coding (МКБ-10)

> **Status:** implemented for the synthetic-data demo. **Not clinically or legally
> validated.** The printed report is a **MediCore patient-record PDF with ICD-10
> diagnosis information**; it is **not** an officially approved Bulgarian
> ambulatory examination sheet or a legally compliant medical document.

## 1. What this feature does

Every diagnosis in the patient record can carry its own ICD-10 (МКБ-10) code.
The patient **Print / Save as PDF** report shows:

- the diagnosis name as recorded;
- the stored ICD-10 code;
- the configured catalogue's Bulgarian description (and English description
  where available);
- the diagnosis status (active/resolved/ruled-out).

One diagnosis → its own code. There is **no patient-level global code**, and the
system never infers a code from a patient name, symptom, lab result, medication,
AI answer, or a similar-sounding disease name.

## 2. Source and version

The local catalogue is based on the official Bulgarian classification page:

- **National Center of Public Health and Analyses (НЦОЗА)**
  <https://ncpha.government.bg/index/301-mkb-10-mejdunarodna-statisticheska-klasifikacia-na-bolestite-i-problemite-svurzani-sus-zdraveto.html>
- **Current combined XLSX:** `ICD10_08062026.xlsx` — three- and four-character
  rubrics, dated 2026-08-06.
- **2025 update:** `New_code_MKB10 2025_2 за сайта.docx` (new/modified codes;
  the document references an amendment published in Държавен вестник, бр. 114
  от 24.12.2025).
- **2026 update:** `НОВИ КОДОВЕ ЗА ДИАГНОЗИ по МКБ-10 през 2026.docx`.
- **WHO ICD-10** as a supplementary international reference:
  <https://www.who.int/standards/classifications/classification-of-diseases/>
- **Regulatory reference:** Наредба № 42 от 2004 г. за въвеждане на
  класификационни статистически системи за кодиране на болестите и проблемите,
  свързани със здравето, и на медицинските процедури. Verify the latest
  amendments and effective dates in Държавен вестник before production use.

The shipped seed metadata records:

```text
version:        NCPHA-2026-08-06 + 2025/2026 updates
retrieved_at:   2026-11-16
source:         NCPHA (НЦОЗА)
scope:          curated demo subset, not the complete classification
```

The seed file is `backend/app/data/icd10_bg_seed.json`.

## 3. Licensing and the full catalogue

The NCPHA website's **Условия за ползване** state that content is subject to
copyright and may not be reproduced, translated, modified, or used without prior
written consent, except where it constitutes public information. This repository
therefore **does not redistribute the complete NCPHA XLSX**.

- The committed seed is a **small, attributed subset** covering the synthetic
  diagnosis seed and a few 2025/2026 update examples.
- A deployment that needs the complete classification must obtain the necessary
  permission, download the official XLSX locally, and run:

  ```bash
  python scripts/import_icd10.py /path/to/ICD10_08062026.xlsx \
      --version "NCPHA-2026-08-06" \
      --retrieved 2026-11-16 \
      --update /path/to/New_code_MKB10_2025.docx \
      --update /path/to/New_codes_2026.docx
  ```

- The generated `backend/app/data/icd10_bg.json` is **gitignored** and must not
  be redistributed without permission. The app loads it automatically when
  present and merges it over the seed.
- The importer uses only the Python standard library; it does not add an Excel
  dependency to the application.
- The full import must be spot-checked by a clinical owner. The 2025/2026 DOCX
  updates are prose, so the importer's best-effort extraction must be verified.

## 4. Data model

The existing `patients.diagnosis.code` column is reused as the ICD-10 code.
No duplicate column was added.

- The column is now **nullable** (migration `a1b2c3d4e5f6`): an unclassified or
  pending diagnosis is legitimate.
- A database CHECK enforces the **format** (`^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$`)
  after normalizing empty strings to NULL.
- The database CHECK is a format gate, **not proof of validity**. Membership and
  active status are validated against the local catalogue by the service/API.
- The recorded diagnosis `name`, `status`, `onset_date`, patient and encounter
  relationships are preserved.
- Catalogue-derived fields are read-model attributes, not stored duplicates:
  `code_description`, `code_description_en`, `code_status`, `code_version`.

`code_status` values:

| Value | Meaning |
|---|---|
| `confirmed` | The code exists and is active in the configured catalogue. |
| `missing` | No code has been assigned. |
| `review_required` | The stored code exists but is superseded/restricted/withdrawn in the catalogue. |
| `unknown` | The stored code is not in the configured catalogue (for example, a legacy/imported code). |

The seed's `R53.83`, `E11.65`, `R73.03`, and `R07.9` are **not** in the Bulgarian
NCPHA base XLSX (they are ICD-10-CM-style codes). They are therefore flagged
`unknown`/review-required rather than silently remapped. A doctor can replace
them with an active Bulgarian code through the assignment workflow.

## 5. Assignment workflow

Only accounts with `patient:write` (Doctor and Clinical Admin) can assign codes.
Nurses remain read-only and receive `403` if they call the write endpoint.

1. Open a patient → **Diagnoses and ICD-10** block.
2. Select **Assign ICD-10 code** (or **Change ICD-10 code**).
3. Search by code, Bulgarian name, or English name.
4. The server searches the local catalogue (`GET /api/v1/icd10/search`).
5. Select an active entry; the official description is shown.
6. Press **Confirm code**. The frontend sends
   `PATCH /api/v1/patients/{patient_id}/diagnoses/{diagnosis_id}` with
   `{ "code": "I10" }`.
7. The server re-checks patient access, validates the code against the
   catalogue, updates only the code, and writes an audit event.
8. **Clear code** sends `{ "code": null }`; the diagnosis becomes unclassified.

There is no AI-generated code assignment. The doctor must explicitly confirm
every selection.

## 6. Validation rules

- Normalize input by trimming whitespace and uppercasing. Do **not** insert
  decimal points or guess missing characters.
- Format gate: one letter + two digits, optionally `.` + 1–4 alphanumeric
  characters.
- Catalogue membership: the exact normalized code must exist in the configured
  local catalogue.
- Active status: assignment requires `status == "active"`; superseded/restricted
  codes are rejected for new assignments.
- Clearing (`null`/blank) is allowed.
- The diagnosis must belong to the path patient; a mismatched/unknown id returns
  `404` (concealment).
- The frontend is never trusted; the backend repeats every check.

## 7. Print / Save as PDF

The existing print workflow (`frontend/assets/js/print-record.js` +
`frontend/assets/css/print.css`) was extended; no second PDF system was added.

- `loadPrintData` fetches **all** diagnoses through the paginated
  `GET /patients/{id}/diagnoses` endpoint, so the report is not limited to the
  summary's 10-row preview.
- A dedicated **Diagnoses and ICD-10 Codes** table appears after the patient
  summary and before encounters.
- Columns: Diagnosis, ICD-10 Code, ICD-10 Description, Status.
- The original recorded diagnosis name is always shown; the catalogue
  description is supplementary.
- Missing code: **“ICD-10 code: Not assigned/confirmed”** /
  **„МКБ-10 код: Не е въведен/потвърден“**.
- Unknown/superseded code: the stored code is shown with **“Review required”**;
  it is never replaced with a guessed code.
- The table uses the existing `.print-table` rules: repeating `<thead>` on every
  page, no row split across pages, black-and-white-readable borders and bold
  code text.
- Both EN and BG labels are localized; diagnosis names and codes are never
  translated.
- A failure to load diagnoses blocks printing and names the failed section; the
  report never prints a silently partial record.

The report is a **MediCore patient-record PDF with ICD-10 diagnosis information**.
It is not an official Bulgarian ambulatory examination sheet and must not be
presented as one without separate validation.

## 8. Audit and security

- `diagnoses.read` audits successful/denied diagnosis-list reads.
- `diagnosis.code.update` audits code assignment/change/clear attempts.
- Audit rows contain actor, diagnosis resource id, outcome, request id, and
  timestamp. They do **not** contain the diagnosis text or the code value.
- Patient access checks are the same as every other clinical read/write; no
  permission was weakened.
- The catalogue search is limited to `patient:write`; it is bounded and never
  returns the complete catalogue in one response.
- The print workflow is read-only; generating a PDF never modifies a diagnosis.
- No patient data is sent to an external AI service for code assignment.

## 9. Tests

- Catalogue metadata, search by code/BG/EN, normalization, status derivation,
  missing/unknown/superseded handling.
- Doctor/Admin search and assignment; Nurse `403`; invalid/unknown code `400`;
  clear code; wrong-patient `404`; audit events.
- Complete diagnosis list pagination and catalogue decoration.
- Print table: multiple diagnoses, code/name pairing, missing label, unknown
  review flag, no duplicate, EN/BG output, repeating headers, no row split,
  fail-closed section loading, hostile text as text.
- Existing print regressions: demographics, encounters, all lab pages, all
  prescription pages, no partial report on failure.
- Backend suite: **705 passed, 5 skipped** (last full run).
- Frontend: print **10/10**, i18n **13/13**, render **109/109**, smoke
  **256/256**, markdown **21/21**, graphify **4/4**, security check **PASS**.

Run:

```bash
cd backend && uv run pytest -q
node frontend/tests/print-record.test.mjs
node frontend/tests/i18n.test.mjs
node frontend/tests/render-smoke.mjs
node frontend/tests/smoke.mjs
python3 scripts/security_check.py
```

## 10. Limitations and required review

- **Not the complete official catalogue.** The committed seed is a curated demo
  subset; the full NCPHA import requires permission and local execution.
- **Not a clinical coding service.** The doctor is responsible for choosing and
  confirming the code; the software validates membership, not clinical
  appropriateness.
- **Not an official form.** The PDF is a MediCore record, not a legally approved
  Bulgarian ambulatory sheet.
- **Licensing and clinical review required** before production use. Verify the
  current Наредба № 42 amendments, NCPHA terms, and the 2025/2026 update
  documents.
- **Synthetic data only.** Real patient data remains prohibited in this demo.
- **English descriptions** are present for the seed subset; a full NCPHA import
  is Bulgarian-only, so English mode falls back to the Bulgarian description.
- **Search performance:** the local catalogue search is an in-memory scan. The
  full official import contains tens of thousands of rows; benchmark and, if
  needed, add an index/cache before high-traffic production use.
- **No AI assignment.** AI may summarize records, but it cannot assign or save a
  diagnosis code.

## 11. References

- `docs/API.md` — diagnosis list, code assignment, and catalogue search contract.
- `docs/SECURITY.md` — audit and permission rules.
- `docs/FEATURES_EXPLAINED.md` — patient record and print workflow.
- `backend/app/services/icd10.py` — local catalogue service.
- `backend/app/data/icd10_bg_seed.json` — committed demo subset and metadata.
- `scripts/import_icd10.py` — local full-XLSX importer.
- `backend/migrations/versions/a1b2c3d4e5f6_diagnosis_icd10_optional.py` —
  nullable code and format CHECK.
