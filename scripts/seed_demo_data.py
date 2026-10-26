"""Seed synthetic demo data for the Clinical AI Platform.

Every record is fictional and obviously demo-labeled (MRN prefix 'DEMO-',
provenance 'synthetic', demo display names). No real patient data, ever.

Idempotent: rows are created only when missing, keyed by natural keys (role
key, staff email, patient MRN, medication code) or deterministic demo UUIDs.
Running the script twice creates no duplicates and prints the same counts.

Usage:
    cd backend && uv run python ../scripts/seed_demo_data.py

Staff accounts come from DEMO_* environment variables (see .env.example). A
missing DEMO_*_PASSWORD generates a random password printed ONCE to stdout so
it can be copied into .env; it is never written to disk or logs.
"""
from __future__ import annotations

import asyncio
import random
import secrets
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.authorization.policy import ROLE_PERMISSIONS
from app.config import load_settings
from app.models import Encounter, LabResult, Medication, Patient, Prescription, Role, StaffUser, UserRole

DEMO_NAMESPACE = uuid.UUID("2f1b6d1e-0a3c-4c6a-9a4f-8d5b3e7c2a10")
RNG_SEED = 20261026

ROLE_DESCRIPTIONS = {
    "doctor": "Clinical user: views synthetic patient records and clinical data.",
    "nurse": "Clinical user: views synthetic patient information and staff directory.",
    "clinical_admin": "Manages staff accounts and roles; no clinical-data access.",
    "tech_admin": "Manages technical settings and user access; no clinical-data access.",
}


@dataclass(frozen=True)
class StaffSpec:
    role_key: str
    display_name: str
    default_email: str
    email_attr: str
    password_attr: str
    env_password_name: str


STAFF_SPECS: tuple[StaffSpec, ...] = (
    StaffSpec("clinical_admin", "Demo Clinical Administrator", "admin@example.com",
              "demo_admin_email", "demo_admin_password", "DEMO_ADMIN_PASSWORD"),
    StaffSpec("doctor", "Demo Doctor", "doctor@example.com",
              "demo_doctor_email", "demo_doctor_password", "DEMO_DOCTOR_PASSWORD"),
    StaffSpec("nurse", "Demo Nurse", "nurse@example.com",
              "demo_nurse_email", "demo_nurse_password", "DEMO_NURSE_PASSWORD"),
    StaffSpec("tech_admin", "Demo Technical Administrator", "techadmin@example.com",
              "demo_tech_admin_email", "demo_tech_admin_password", "DEMO_TECH_ADMIN_PASSWORD"),
)

# (mrn, given_name, family_name, display_name, date_of_birth, sex)
# Names deliberately include apostrophes, accents, RTL text, and emoji to
# exercise UTF-8 name handling end to end.
PATIENT_SPECS: tuple[tuple[str, str, str, str, date | None, str], ...] = (
    ("DEMO-0001", "Demo", "Patient One", "Demo Patient One", date(1978, 3, 12), "female"),
    ("DEMO-0002", "Demo", "Patient Two", "Demo Patient Two", date(1965, 11, 2), "male"),
    ("DEMO-0003", "Demo", "Patient Three", "Demo Patient Three", date(1990, 7, 24), "female"),
    ("DEMO-0004", "Demo", "Patient Four", "Demo Patient Four", date(1952, 1, 30), "male"),
    ("DEMO-0005", "Demo", "Patient Five", "Demo Patient Five", date(1988, 9, 15), "other"),
    ("DEMO-0006", "Demo", "Patient Six", "Demo Patient Six", date(2001, 5, 6), "female"),
    ("DEMO-0007", "Séamus", "O'Connor", "Séamus O'Connor (Demo)", date(1974, 2, 18), "male"),
    ("DEMO-0008", "José", "García-López", "José García-López (Demo)", date(1983, 12, 1), "male"),
    ("DEMO-0009", "ليلى", "منصور", "ليلى منصور (Demo)", date(1996, 4, 9), "female"),
    ("DEMO-0010", "Zoë", "O'Connor ⭐", "Zoë O'Connor ⭐ (Demo)", date(1999, 8, 23), "unknown"),
)

ENCOUNTER_TYPES = (
    "outpatient visit",
    "telehealth check-in",
    "annual exam",
    "lab draw",
    "urgent care visit",
)

# (code, name, unit, ref_low, ref_high)
LAB_TESTS: tuple[tuple[str, str, str, float, float], ...] = (
    ("GLU", "Glucose, fasting", "mg/dL", 70.0, 99.0),
    ("HBA1C", "Hemoglobin A1c", "%", 4.0, 5.6),
    ("CREA", "Creatinine", "mg/dL", 0.6, 1.2),
    ("K", "Potassium", "mmol/L", 3.5, 5.1),
    ("HGB", "Hemoglobin", "g/dL", 12.0, 17.5),
    ("TSH", "TSH", "mIU/L", 0.4, 4.0),
    ("LDL", "LDL cholesterol", "mg/dL", 0.0, 100.0),
    ("WBC", "White blood cell count", "10^3/uL", 4.0, 11.0),
)

# (code, display_name, form)
MEDICATION_CATALOG: tuple[tuple[str, str, str], ...] = (
    ("DEMO-MED-001", "Metformin", "tablet"),
    ("DEMO-MED-002", "Lisinopril", "tablet"),
    ("DEMO-MED-003", "Atorvastatin", "tablet"),
    ("DEMO-MED-004", "Salbutamol", "inhaler"),
    ("DEMO-MED-005", "Amoxicillin", "capsule"),
    ("DEMO-MED-006", "Levothyroxine", "tablet"),
)

DOSE_UNITS = ("mg", "mg", "mg", "mcg", "puff")
ROUTES = ("oral", "oral", "oral", "inhaled")
FREQUENCIES = ("once daily", "twice daily", "every morning", "as needed", "at bedtime")
PRESCRIPTION_STATUSES = ("active", "active", "completed", "stopped", "cancelled")


@dataclass
class Summary:
    created: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    existing: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    generated_passwords: dict[str, str] = field(default_factory=dict)

    def bump(self, entity: str, *, created: bool) -> None:
        if created:
            self.created[entity] += 1
        else:
            self.existing[entity] += 1


def demo_id(*parts: str) -> uuid.UUID:
    """Deterministic demo UUID so re-runs can detect existing rows."""
    return uuid.uuid5(DEMO_NAMESPACE, "|".join(parts))


async def seed_roles(session: AsyncSession, summary: Summary) -> dict[str, uuid.UUID]:
    role_ids: dict[str, uuid.UUID] = {}
    for key in sorted(ROLE_PERMISSIONS):
        role = await session.scalar(select(Role).where(Role.key == key))
        if role is None:
            role = Role(id=demo_id("role", key), key=key, description=ROLE_DESCRIPTIONS.get(key, ""))
            session.add(role)
            summary.bump("roles", created=True)
        else:
            summary.bump("roles", created=False)
        role_ids[key] = role.id
    return role_ids


async def seed_staff(
    session: AsyncSession,
    summary: Summary,
    settings: object,
    role_ids: dict[str, uuid.UUID],
    hasher: PasswordHasher,
) -> dict[str, uuid.UUID]:
    staff_ids: dict[str, uuid.UUID] = {}
    for spec in STAFF_SPECS:
        configured_email = getattr(settings, spec.email_attr)
        email = (configured_email or spec.default_email).strip().lower()
        configured_password = getattr(settings, spec.password_attr)
        plain_password = configured_password.get_secret_value() if configured_password is not None else None
        if plain_password is not None and not plain_password.strip():
            plain_password = None

        user = await session.scalar(select(StaffUser).where(StaffUser.email == email))
        if user is None:
            if plain_password is None:
                plain_password = secrets.token_urlsafe(12)
                summary.generated_passwords[spec.env_password_name] = plain_password
            user = StaffUser(
                id=demo_id("staff", email),
                email=email,
                display_name=spec.display_name,
                password_hash=hasher.hash(plain_password),
                is_active=True,
            )
            session.add(user)
            summary.bump("staff_users", created=True)
        else:
            summary.bump("staff_users", created=False)
        staff_ids[spec.role_key] = user.id

        role_id = role_ids[spec.role_key]
        grant = await session.get(UserRole, {"user_id": user.id, "role_id": role_id})
        if grant is None:
            session.add(UserRole(user_id=user.id, role_id=role_id))
            summary.bump("role_grants", created=True)
        else:
            summary.bump("role_grants", created=False)
    return staff_ids


async def seed_medications(session: AsyncSession, summary: Summary) -> dict[str, uuid.UUID]:
    med_ids: dict[str, uuid.UUID] = {}
    for code, display_name, form in MEDICATION_CATALOG:
        med = await session.scalar(select(Medication).where(Medication.code == code))
        if med is None:
            med = Medication(id=demo_id("medication", code), code=code, display_name=display_name, form=form)
            session.add(med)
            summary.bump("medications", created=True)
        else:
            summary.bump("medications", created=False)
        med_ids[code] = med.id
    return med_ids


def _synthetic_encounter_summary(rng: random.Random, encounter_type: str) -> str:
    topics = (
        "blood pressure review",
        "medication review",
        "lab result follow-up",
        "general wellbeing check",
        "synthetic care-plan discussion",
    )
    return f"Synthetic demo {encounter_type}: {rng.choice(topics)}. Fictional data, not for clinical use."


async def seed_patient_details(
    session: AsyncSession,
    summary: Summary,
    patient: Patient,
    rng: random.Random,
    med_ids: dict[str, uuid.UUID],
    prescriber_ids: list[uuid.UUID],
) -> None:
    today = datetime.now(timezone.utc).date()

    # Every random draw happens unconditionally so re-runs consume an identical
    # sequence and cannot create rows with different ids than the first run.
    for index in range(rng.randint(2, 4)):
        encounter_id = demo_id("encounter", patient.mrn, str(index))
        occurred_day = today - timedelta(days=120 * index + rng.randint(5, 110))
        occurred_hour = rng.randint(8, 16)
        encounter_type = rng.choice(ENCOUNTER_TYPES)
        summary_text = _synthetic_encounter_summary(rng, encounter_type)

        encounter = await session.get(Encounter, encounter_id)
        if encounter is None:
            encounter = Encounter(
                id=encounter_id,
                patient_id=patient.id,
                occurred_at=datetime.combine(occurred_day, time(hour=occurred_hour, tzinfo=timezone.utc)),
                encounter_type=encounter_type,
                summary=summary_text,
            )
            session.add(encounter)
            summary.bump("encounters", created=True)
        else:
            summary.bump("encounters", created=False)

        lab_specs = rng.sample(LAB_TESTS, rng.randint(2, 4))
        for lab_index, (code, name, unit, ref_low, ref_high) in enumerate(lab_specs):
            midpoint = (ref_low + ref_high) / 2
            spread = (ref_high - ref_low) or 1.0
            value = round(midpoint + rng.uniform(-0.85, 0.85) * spread, 2)
            collected_hour = rng.randint(7, 11)
            resulted_hour = rng.randint(12, 18)

            lab_id = demo_id("lab", patient.mrn, str(index), str(lab_index))
            if await session.get(LabResult, lab_id) is not None:
                summary.bump("lab_results", created=False)
                continue
            session.add(
                LabResult(
                    id=lab_id,
                    patient_id=patient.id,
                    encounter_id=encounter.id,
                    test_code=code,
                    test_name=name,
                    value_num=Decimal(str(value)),
                    unit=unit,
                    ref_low=Decimal(str(ref_low)),
                    ref_high=Decimal(str(ref_high)),
                    status="final",
                    collected_at=datetime.combine(occurred_day, time(hour=collected_hour, tzinfo=timezone.utc)),
                    resulted_at=datetime.combine(occurred_day, time(hour=resulted_hour, tzinfo=timezone.utc)),
                )
            )
            summary.bump("lab_results", created=True)

    for index in range(rng.randint(1, 3)):
        prescription_id = demo_id("prescription", patient.mrn, str(index))
        code, med_name, _form = MEDICATION_CATALOG[rng.randrange(len(MEDICATION_CATALOG))]
        status = rng.choice(PRESCRIPTION_STATUSES)
        start_day = today - timedelta(days=rng.randint(30, 500))
        end_day = start_day + timedelta(days=rng.randint(20, 180)) if status in ("completed", "stopped") else None
        dose_amount = Decimal(str(rng.choice([2.5, 5.0, 10.0, 20.0, 50.0, 100.0, 500.0])))
        dose_unit = rng.choice(DOSE_UNITS)
        route = rng.choice(ROUTES)
        frequency = rng.choice(FREQUENCIES)
        prescriber_id = rng.choice(prescriber_ids)

        if await session.get(Prescription, prescription_id) is not None:
            summary.bump("prescriptions", created=False)
            continue
        session.add(
            Prescription(
                id=prescription_id,
                patient_id=patient.id,
                encounter_id=None,
                medication_id=med_ids[code],
                medication_name=med_name,
                dose_amount=dose_amount,
                dose_unit=dose_unit,
                route=route,
                frequency=frequency,
                status=status,
                start_date=start_day,
                end_date=end_day,
                prescriber_id=prescriber_id,
            )
        )
        summary.bump("prescriptions", created=True)


async def seed_patients(
    session: AsyncSession,
    summary: Summary,
    med_ids: dict[str, uuid.UUID],
    prescriber_ids: list[uuid.UUID],
) -> None:
    rng = random.Random(RNG_SEED)
    for mrn, given, family, display, dob, sex in PATIENT_SPECS:
        patient = await session.scalar(select(Patient).where(Patient.mrn == mrn))
        if patient is None:
            patient = Patient(
                id=demo_id("patient", mrn),
                mrn=mrn,
                display_name=display,
                given_name=given,
                family_name=family,
                date_of_birth=dob,
                sex=sex,
                provenance="synthetic",
            )
            session.add(patient)
            await session.flush()
            summary.bump("patients", created=True)
        else:
            summary.bump("patients", created=False)
        await seed_patient_details(session, summary, patient, rng, med_ids, prescriber_ids)


async def run_seed() -> Summary:
    settings = load_settings()
    engine = create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    hasher = PasswordHasher()
    summary = Summary()
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            role_ids = await seed_roles(session, summary)
            staff_ids = await seed_staff(session, summary, settings, role_ids, hasher)
            med_ids = await seed_medications(session, summary)
            prescriber_ids = [staff_ids["doctor"], staff_ids["clinical_admin"]]
            await seed_patients(session, summary, med_ids, prescriber_ids)
            await session.commit()
    finally:
        await engine.dispose()
    return summary


def main() -> int:
    try:
        summary = asyncio.run(run_seed())
    except ProgrammingError:
        print(
            "Seed failed: database schema is missing or incomplete.\n"
            "Run migrations first: cd backend && uv run alembic upgrade head",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"Seed failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("Seed summary (synthetic demo data only):")
    for entity in sorted(set(summary.created) | set(summary.existing)):
        print(f"  {entity}: {summary.created.get(entity, 0)} created, {summary.existing.get(entity, 0)} already present")

    if summary.generated_passwords:
        print()
        print("Generated demo passwords (shown ONCE, never written to disk or logs).")
        print("Set these in your .env so they stay stable across re-seeds:")
        for name, value in summary.generated_passwords.items():
            print(f"  {name}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
