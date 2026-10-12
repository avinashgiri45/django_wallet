from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import ConsultationSession, Wallet, WalletTransaction


class BillingResult:
    def __init__(self, session, insufficient_balance=False):
        self.session = session
        self.insufficient_balance = insufficient_balance


def settle_elapsed_minutes(session_id, at=None, finish_reason=None):
    at = at or timezone.now()
    insufficient_balance = False

    with transaction.atomic():
        session = ConsultationSession.objects.select_for_update().get(pk=session_id)
        if session.status != ConsultationSession.Status.IN_PROGRESS:
            return BillingResult(session)

        wallet = Wallet.objects.select_for_update().get(user=session.user)
        elapsed_seconds = max(0, int((at - session.started_at).total_seconds()))
        due_minutes = elapsed_seconds // 60
        minutes_to_charge = max(0, due_minutes - session.billed_minutes)
        affordable_minutes = int(wallet.balance / session.cost_per_minute)
        charged_minutes = min(minutes_to_charge, affordable_minutes)

        for minute_number in range(session.billed_minutes + 1, session.billed_minutes + charged_minutes + 1):
            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type=WalletTransaction.Type.DEBIT,
                amount=session.cost_per_minute,
                session=session,
                minute_number=minute_number,
            )

        if charged_minutes:
            wallet.balance -= session.cost_per_minute * charged_minutes
            wallet.save(update_fields=["balance", "updated_at"])
            session.billed_minutes += charged_minutes

        if charged_minutes < minutes_to_charge:
            insufficient_balance = True
            session.status = ConsultationSession.Status.COMPLETED
            session.ended_at = at
            session.talk_time_seconds = elapsed_seconds
            session.end_reason = "system: Insufficient Balance"
        elif finish_reason is not None:
            session.status = ConsultationSession.Status.COMPLETED
            session.ended_at = at
            session.talk_time_seconds = elapsed_seconds
            session.end_reason = finish_reason

        session.save(
            update_fields=[
                "billed_minutes",
                "status",
                "ended_at",
                "talk_time_seconds",
                "end_reason",
            ]
        )

    return BillingResult(session, insufficient_balance)


def start_session(session_id, user_id):
    with transaction.atomic():
        session = ConsultationSession.objects.select_for_update().get(pk=session_id)
        wallet = Wallet.objects.select_for_update().get(user_id=user_id)

        if session.status != ConsultationSession.Status.ACCEPTED:
            raise ValueError("Only an accepted session can be started.")
        if session.user_id != user_id:
            raise ValueError("Only the session user can start this session.")
        if ConsultationSession.objects.filter(
            user_id=user_id,
            status=ConsultationSession.Status.IN_PROGRESS,
        ).exists():
            raise ValueError("The user already has an in-progress session.")
        minimum_balance = session.cost_per_minute * Decimal("5")
        if wallet.balance < minimum_balance:
            raise ValueError(
                f"Starting balance must be at least {minimum_balance:.2f} "
                "(5x the per-minute cost)."
            )

        session.status = ConsultationSession.Status.IN_PROGRESS
        session.started_at = timezone.now()
        session.save(update_fields=["status", "started_at"])
        return session
