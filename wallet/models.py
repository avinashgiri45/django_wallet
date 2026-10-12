import uuid
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class User(AbstractUser):
    class Role(models.TextChoices):
        USER = "USER", "User"
        PROVIDER = "PROVIDER", "Provider"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.USER)


class Wallet(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=Q(balance__gte=0), name="wallet_balance_nonnegative"),
        ]

    def __str__(self):
        return f"{self.user.username}: {self.balance}"


class Recharge(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recharges")
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.reference}: {self.status}"


class ConsultationSession(models.Model):
    class Status(models.TextChoices):
        UNASSIGNED = "unassigned", "Unassigned"
        ASSIGNED = "assigned", "Assigned"
        ACCEPTED = "accepted_by_provider", "Accepted by provider"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="consultation_sessions")
    provider = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="provider_sessions",
        null=True,
        blank=True,
        limit_choices_to={"role": User.Role.PROVIDER},
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.UNASSIGNED)
    cost_per_minute = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    billed_minutes = models.PositiveIntegerField(default=0)
    talk_time_seconds = models.PositiveIntegerField(default=0)
    end_reason = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status="in_progress"),
                name="one_active_session_per_user",
            ),
            models.CheckConstraint(check=Q(cost_per_minute__gt=0), name="session_rate_positive"),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Session {self.pk} ({self.status})"


class WalletTransaction(models.Model):
    class Type(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="transactions")
    transaction_type = models.CharField(max_length=6, choices=Type.choices)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    recharge = models.OneToOneField(
        Recharge,
        on_delete=models.PROTECT,
        related_name="wallet_transaction",
        null=True,
        blank=True,
    )
    session = models.ForeignKey(
        ConsultationSession,
        on_delete=models.PROTECT,
        related_name="wallet_transactions",
        null=True,
        blank=True,
    )
    minute_number = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "minute_number"],
                condition=Q(session__isnull=False),
                name="unique_session_billed_minute",
            ),
            models.CheckConstraint(check=Q(amount__gt=0), name="wallet_transaction_amount_positive"),
        ]
        ordering = ["created_at"]
