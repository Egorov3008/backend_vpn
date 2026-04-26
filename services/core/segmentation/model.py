from enum import Enum


class UserSegment(Enum):
    """Сегменты пользователей"""

    NEW_USER = "new_user"
    ACTIVE_TRIAL = "active_trial"
    ACTIVE_PAID = "active_paid"
    EXPIRING_SOON = "expiring_soon"
    EXPIRED_PAID = "expired_paid"
    INACTIVE = "inactive"
    INACTIVE_TRIAL = "inactive_trial"
    CHURN_RISK = "churn_risk"
    COLD_LEAD = "cold_lead"
    BLOCKED = "blocked"
