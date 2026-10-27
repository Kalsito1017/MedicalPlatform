"""ORM models. Importing this package registers every table on Base.metadata."""

from app.models.admin import AuditEvent, Permission, Role, StaffUser, UserRole, UserSession
from app.models.ai import AiConversation
from app.models.lab import LabOrder, LabResult, ReferenceRange, Specimen, TestCatalog
from app.models.medication import (
    Medication,
    Prescription,
    PrescriptionChange,
    PrescriptionHistory,
    PrescriptionItem,
)
from app.models.patient import ClinicalNote, Diagnosis, Encounter, Patient, PatientAssignment

__all__ = [
    "AiConversation",
    "AuditEvent",
    "ClinicalNote",
    "Diagnosis",
    "Encounter",
    "LabOrder",
    "LabResult",
    "Medication",
    "Patient",
    "PatientAssignment",
    "Permission",
    "Prescription",
    "PrescriptionChange",
    "PrescriptionHistory",
    "PrescriptionItem",
    "ReferenceRange",
    "Role",
    "Specimen",
    "StaffUser",
    "TestCatalog",
    "UserRole",
    "UserSession",
]
