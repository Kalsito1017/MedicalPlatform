// ---------------------------------------------------------------------------
// visit-types.js — the fixed visit catalogue used by the appointment form.
//
// `duration` is the suggested appointment length in minutes. The backend
// stores `appointment_type` as a bounded free-text label, so "Other" can carry
// a doctor-authored custom label without a schema change.
// ---------------------------------------------------------------------------

export const VISIT_TYPES = [
  { id: 'new_patient', label: 'New Patient Consultation', duration: 45 },
  { id: 'general', label: 'General Consultation', duration: 30 },
  { id: 'follow_up', label: 'Follow-up Visit', duration: 20 },
  { id: 'routine_checkup', label: 'Routine Check-up', duration: 30 },
  { id: 'annual_physical', label: 'Annual Physical Examination', duration: 45 },
  { id: 'urgent', label: 'Urgent Consultation', duration: 20 },
  { id: 'specialist', label: 'Specialist Consultation', duration: 30 },
  { id: 'preventive', label: 'Preventive Care Visit', duration: 30 },
  { id: 'chronic_management', label: 'Chronic Disease Management', duration: 30 },
  { id: 'lab_review', label: 'Laboratory Results Review', duration: 15 },
  { id: 'medication_review', label: 'Medication Review', duration: 20 },
  { id: 'diagnostic', label: 'Diagnostic Evaluation', duration: 30 },
  { id: 'vaccination', label: 'Vaccination', duration: 15 },
  { id: 'procedure', label: 'Medical Procedure', duration: 30 },
  { id: 'preoperative', label: 'Pre-operative Assessment', duration: 30 },
  { id: 'postoperative', label: 'Post-operative Follow-up', duration: 30 },
  { id: 'telemedicine', label: 'Telemedicine Consultation', duration: 20 },
  { id: 'mental_health', label: 'Mental Health Consultation', duration: 45 },
  { id: 'other', label: 'Other', duration: 30 },
];

const ALIASES = {
  consultation: 'general',
  'follow-up': 'follow_up',
  'follow up': 'follow_up',
  checkup: 'routine_checkup',
  'check-up': 'routine_checkup',
  'check up': 'routine_checkup',
  'lab results': 'lab_review',
  'laboratory results': 'lab_review',
};

/** Match a stored/AI-proposed type to a catalogue entry (id or label). */
export function findVisitType(value) {
  const normalized = String(value || '').trim().toLowerCase();
  if (!normalized) return null;
  const alias = ALIASES[normalized];
  if (alias) return VISIT_TYPES.find((type) => type.id === alias) || null;
  return (
    VISIT_TYPES.find(
      (type) => type.id === normalized || type.label.toLowerCase() === normalized
    ) || null
  );
}
