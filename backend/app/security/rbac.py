"""Role → permission mapping. Checked server-side on every mutating endpoint."""

from enum import StrEnum

from app.models.enums import Role


class Permission(StrEnum):
    READ = "read"
    INGEST = "ingest"
    SIMULATE = "simulate"
    TRIAGE = "triage"  # acknowledge, diagnose, close incidents
    PROPOSE_REPAIR = "propose_repair"
    APPROVE_REPAIR = "approve_repair"
    EXECUTE_RECOVERY = "execute_recovery"
    MANAGE_RULES = "manage_rules"
    MANAGE_SOURCES = "manage_sources"
    READ_AUDIT = "read_audit"


_ENGINEER = {
    Permission.READ,
    Permission.INGEST,
    Permission.SIMULATE,
    Permission.TRIAGE,
    Permission.PROPOSE_REPAIR,
    Permission.EXECUTE_RECOVERY,
}

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.ANALYST: {Permission.READ},
    Role.ENGINEER: _ENGINEER,
    Role.STEWARD: {
        Permission.READ,
        Permission.TRIAGE,
        Permission.PROPOSE_REPAIR,
        Permission.APPROVE_REPAIR,
        Permission.EXECUTE_RECOVERY,
    },
    Role.ADMIN: set(Permission),
}


def has_permission(role: str, permission: Permission) -> bool:
    try:
        return permission in ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return False
