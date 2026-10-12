from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APITestCase

from .models import ConsultationSession, User, Wallet, WalletTransaction


class AuthenticationFlowTests(APITestCase):
    def test_registration_then_login_returns_jwt_tokens(self):
        registration = self.client.post(
            reverse("register"),
            {
                "username": "new_customer",
                "password": "securepass123",
                "role": User.Role.USER,
            },
            format="json",
        )
        self.assertEqual(registration.status_code, 201, registration.data)
        self.assertNotIn("password", registration.data)

        login = self.client.post(
            reverse("token_obtain_pair"),
            {"username": "new_customer", "password": "securepass123"},
            format="json",
        )

        self.assertEqual(login.status_code, 200, login.data)
        self.assertIn("access", login.data)
        self.assertIn("refresh", login.data)

    def test_browsable_api_session_login_can_open_protected_api(self):
        user = User.objects.create_user(
            username="browser_user",
            password="securepass123",
            role=User.Role.USER,
        )
        self.assertTrue(self.client.login(username="browser_user", password="securepass123"))

        response = self.client.get(
            reverse("sessions"),
            HTTP_ACCEPT="text/html",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/html"))
        self.assertContains(response, "Session List Create")


class WalletBillingAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="customer", password="password123", role="USER")
        self.provider = User.objects.create_user(username="expert", password="password123", role="PROVIDER")
        self.client.force_authenticate(self.user)

    def recharge(self, amount):
        response = self.client.post(reverse("recharges"), {"amount": str(amount)}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        reference = response.data["reference"]
        callback = self.client.post(
            reverse("recharge-callback", args=[reference]),
            {"status": "success"},
            format="json",
        )
        self.assertEqual(callback.status_code, 200, callback.data)
        return reference

    def create_accepted_session(self, rate="2.00"):
        response = self.client.post(
            reverse("sessions"),
            {"provider_id": self.provider.pk, "cost_per_minute": rate},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        session_id = response.data["id"]
        self.client.force_authenticate(self.provider)
        accepted = self.client.post(
            reverse("session-action", args=[session_id, "accept"]),
            {},
            format="json",
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.client.force_authenticate(self.user)
        return session_id

    def start(self, session_id):
        return self.client.post(
            reverse("session-action", args=[session_id, "start"]),
            {},
            format="json",
        )

    def test_start_blocked_below_five_times_rate(self):
        self.recharge("9.99")
        session_id = self.create_accepted_session()

        response = self.start(session_id)

        self.assertEqual(response.status_code, 400)
        self.assertIn("5x", str(response.data))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("9.99"))

    def test_happy_path_recharge_billing_end_and_idempotent_callback(self):
        reference = self.recharge("20.00")
        self.client.post(
            reverse("recharge-callback", args=[reference]),
            {"status": "success"},
            format="json",
        )
        self.user.wallet.refresh_from_db()
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("20.00"))

        session_id = self.create_accepted_session()
        response = self.start(session_id)
        self.assertEqual(response.status_code, 200, response.data)
        session = ConsultationSession.objects.get(pk=session_id)
        session.started_at = timezone.now() - timedelta(seconds=125)
        session.save(update_fields=["started_at"])

        billed = self.client.post(
            reverse("session-action", args=[session_id, "bill"]),
            {},
            format="json",
        )
        self.assertEqual(billed.status_code, 200, billed.data)
        self.assertEqual(billed.data["billed_minutes"], 2)
        self.user.wallet.refresh_from_db()
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("16.00"))

        ended = self.client.post(
            reverse("session-action", args=[session_id, "end"]),
            {},
            format="json",
        )
        self.assertEqual(ended.status_code, 200, ended.data)
        self.assertEqual(ended.data["status"], ConsultationSession.Status.COMPLETED)

    def test_balance_depletion_completes_session_without_negative_wallet(self):
        self.recharge("10.00")
        session_id = self.create_accepted_session()
        self.assertEqual(self.start(session_id).status_code, 200)
        session = ConsultationSession.objects.get(pk=session_id)
        session.started_at = timezone.now() - timedelta(seconds=365)
        session.save(update_fields=["started_at"])

        response = self.client.post(
            reverse("session-action", args=[session_id, "bill"]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], ConsultationSession.Status.COMPLETED)
        self.assertEqual(response.data["end_reason"], "system: Insufficient Balance")
        started_at = parse_datetime(response.data["started_at"])
        ended_at = parse_datetime(response.data["ended_at"])
        self.assertEqual(
            response.data["talk_time_seconds"],
            int((ended_at - started_at).total_seconds()),
        )
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0.00"))

    def test_invalid_end_before_start_returns_400(self):
        session_id = self.create_accepted_session()

        response = self.client.post(
            reverse("session-action", args=[session_id, "end"]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_unassigned_session_can_be_assigned_and_accepted(self):
        self.recharge("10.00")
        created = self.client.post(
            reverse("sessions"),
            {"cost_per_minute": "2.00"},
            format="json",
        )
        session_id = created.data["id"]
        self.assertEqual(created.data["status"], ConsultationSession.Status.UNASSIGNED)

        assigned = self.client.post(
            reverse("session-action", args=[session_id, "assign"]),
            {"provider_id": self.provider.pk},
            format="json",
        )
        self.assertEqual(assigned.status_code, 200, assigned.data)
        self.assertEqual(assigned.data["status"], ConsultationSession.Status.ASSIGNED)
        self.client.force_authenticate(self.provider)
        accepted = self.client.post(
            reverse("session-action", args=[session_id, "accept"]),
            {},
            format="json",
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(accepted.data["status"], ConsultationSession.Status.ACCEPTED)

    def test_user_cannot_start_two_sessions_at_once(self):
        self.recharge("20.00")
        first_id = self.create_accepted_session()
        self.assertEqual(self.start(first_id).status_code, 200)
        second_id = self.create_accepted_session()

        response = self.start(second_id)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            ConsultationSession.objects.get(pk=first_id).status,
            ConsultationSession.Status.IN_PROGRESS,
        )
        self.assertEqual(
            ConsultationSession.objects.get(pk=second_id).status,
            ConsultationSession.Status.ACCEPTED,
        )

    def test_duplicate_billing_trigger_does_not_double_charge(self):
        self.recharge("20.00")
        session_id = self.create_accepted_session()
        self.start(session_id)
        session = ConsultationSession.objects.get(pk=session_id)
        session.started_at = timezone.now() - timedelta(seconds=65)
        session.save(update_fields=["started_at"])
        endpoint = reverse("session-action", args=[session_id, "bill"])

        first = self.client.post(endpoint, {}, format="json")
        second = self.client.post(endpoint, {}, format="json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["billed_minutes"], 1)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("18.00"))
        self.assertEqual(
            WalletTransaction.objects.filter(
                session_id=session_id,
                transaction_type=WalletTransaction.Type.DEBIT,
            ).count(),
            1,
        )

    def test_failed_recharge_does_not_credit_or_later_change(self):
        created = self.client.post(reverse("recharges"), {"amount": "12.00"}, format="json")
        reference = created.data["reference"]
        callback_url = reverse("recharge-callback", args=[reference])

        failed = self.client.post(callback_url, {"status": "failed"}, format="json")
        repeated = self.client.post(callback_url, {"status": "failed"}, format="json")
        changed = self.client.post(callback_url, {"status": "success"}, format="json")

        self.assertEqual(failed.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(changed.status_code, 400)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0.00"))
