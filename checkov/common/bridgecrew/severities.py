from dataclasses import dataclass
from typing import Optional


class Severity:
    def __init__(self, name: str, level: int) -> None:
        self.name = name
        self.level = level


@dataclass
class BcSeverities:
    NONE = 'NONE'
    LOW = 'LOW'
    MEDIUM = 'MEDIUM'
    HIGH = 'HIGH'
    CRITICAL = 'CRITICAL'
    MODERATE = 'MODERATE'
    IMPORTANT = 'IMPORTANT'
    OFF = 'OFF'


Severities = {
    BcSeverities.NONE: Severity(BcSeverities.NONE, 0),
    BcSeverities.LOW: Severity(BcSeverities.LOW, 1),
    BcSeverities.MEDIUM: Severity(BcSeverities.MEDIUM, 2),
    BcSeverities.MODERATE: Severity(BcSeverities.MEDIUM, 2),
    BcSeverities.HIGH: Severity(BcSeverities.HIGH, 3),
    BcSeverities.IMPORTANT: Severity(BcSeverities.HIGH, 3),
    BcSeverities.CRITICAL: Severity(BcSeverities.CRITICAL, 4),
    BcSeverities.OFF: Severity(BcSeverities.OFF, 999),
}


def get_severity(severity: Optional[str]) -> Optional[Severity]:
    if not severity:
        return None
    return Severities.get(severity.upper())


def get_highest_severity_below_level(level: int) -> Optional[Severity]:
    last = None
    for severity in sorted(Severities.values(), key=lambda s: s.level):
        if severity.level < level and (not last or severity.level > last.level):
            last = severity
    return last
