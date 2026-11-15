"""ORM models. Importing this package registers every table on Base.metadata."""

from app.models.admin import (
    AuditEvent,
    IdempotencyRecord,
    Message,
    MessageAttachment,
    Permission,
    SystemEvent,
    Role,
    StaffUser,
    UserRole,
    UserSession,
)
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
from app.models.scheduling import (
    Appointment,
    AppointmentEvent,
    DoctorSchedule,
    TimeOff,
    WorkingHours,
)

__all__ = [
    "AiConversation",
    "Appointment",
    "AppointmentEvent",
    "AuditEvent",
    "ClinicalNote",
    "Diagnosis",
    "DoctorSchedule",
    "Encounter",
    "IdempotencyRecord",
    "LabOrder",
    "LabResult",
    "Medication",
    "Message",
    "MessageAttachment",
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
    "SystemEvent",
    "TestCatalog",
    "TimeOff",
    "UserRole",
    "UserSession",
    "WorkingHours",
]
