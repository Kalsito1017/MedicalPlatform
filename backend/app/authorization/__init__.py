"""Authorization package: role/permission policy (source of truth for docs/API.md).

Deny by default everywhere. Roles never imply patient-level access beyond the
demo policy; the `nurse` role is least-privilege clinical read (no staff or
audit access) and only the `clinical_admin` (Admin) role holds administration
permissions.
"""
