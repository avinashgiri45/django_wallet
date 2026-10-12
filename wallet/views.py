from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ConsultationSession, Recharge, User, Wallet, WalletTransaction
from .permissions import IsProviderRole, IsUserRole
from .serializers import (
    RechargeSerializer,
    SessionCreateSerializer,
    SessionSerializer,
    UserRegistrationSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)
from .services import settle_elapsed_minutes, start_session


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [AllowAny]


class WalletView(generics.RetrieveAPIView):
    serializer_class = WalletSerializer

    def get_object(self):
        return self.request.user.wallet


class WalletTransactionListView(generics.ListAPIView):
    serializer_class = WalletTransactionSerializer

    def get_queryset(self):
        return WalletTransaction.objects.filter(wallet__user=self.request.user)


class RechargeListCreateView(generics.ListCreateAPIView):
    serializer_class = RechargeSerializer

    def get_queryset(self):
        return Recharge.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class MockRechargeCallbackView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, reference):
        callback_status = request.data.get("status")
        if callback_status not in (Recharge.Status.SUCCESS, Recharge.Status.FAILED):
            raise ValidationError({"status": "Use 'success' or 'failed'."})

        with transaction.atomic():
            recharge = get_object_or_404(
                Recharge.objects.select_for_update(),
                reference=reference,
            )
            if recharge.status == Recharge.Status.SUCCESS:
                if callback_status == Recharge.Status.SUCCESS:
                    return Response({"reference": str(recharge.reference), "status": recharge.status})
                raise ValidationError({"status": "A successful recharge cannot be changed."})
            if recharge.status == Recharge.Status.FAILED:
                if callback_status == Recharge.Status.FAILED:
                    return Response({"reference": str(recharge.reference), "status": recharge.status})
                raise ValidationError({"status": "A failed recharge cannot be changed."})

            recharge.status = callback_status
            recharge.save(update_fields=["status", "updated_at"])
            if callback_status == Recharge.Status.SUCCESS:
                wallet = Wallet.objects.select_for_update().get(user=recharge.user)
                wallet.balance += recharge.amount
                wallet.save(update_fields=["balance", "updated_at"])
                WalletTransaction.objects.create(
                    wallet=wallet,
                    transaction_type=WalletTransaction.Type.CREDIT,
                    amount=recharge.amount,
                    recharge=recharge,
                )

        return Response({"reference": str(recharge.reference), "status": recharge.status})


class SessionListCreateView(generics.ListCreateAPIView):
    serializer_class = SessionSerializer

    def get_queryset(self):
        user = self.request.user
        if user.role == User.Role.PROVIDER:
            return ConsultationSession.objects.filter(provider=user)
        return ConsultationSession.objects.filter(user=user)

    def get_serializer_class(self):
        if self.request.method == "POST":
            return SessionCreateSerializer
        return self.serializer_class

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsUserRole()]
        return super().get_permissions()

    def perform_create(self, serializer):
        serializer.save()


class SessionActionView(APIView):
    def get_session(self, request, pk):
        session = get_object_or_404(ConsultationSession, pk=pk)
        if request.user.pk not in (session.user_id, session.provider_id):
            raise PermissionDenied("You are not a participant in this session.")
        return session

    def post(self, request, pk, action):
        session = self.get_session(request, pk)

        if action == "assign":
            if request.user.role != User.Role.USER or request.user.pk != session.user_id:
                raise PermissionDenied("Only the session user can assign a provider.")
            if session.status != ConsultationSession.Status.UNASSIGNED:
                raise ValidationError("Only an unassigned session can be assigned.")
            provider_id = request.data.get("provider_id")
            if not User.objects.filter(pk=provider_id, role=User.Role.PROVIDER).exists():
                raise ValidationError({"provider_id": "A valid provider account is required."})
            session.provider_id = provider_id
            session.status = ConsultationSession.Status.ASSIGNED
            session.save(update_fields=["provider", "status"])
        elif action == "accept":
            if request.user.role != User.Role.PROVIDER or request.user.pk != session.provider_id:
                raise PermissionDenied("Only the assigned provider can accept this session.")
            if session.status != ConsultationSession.Status.ASSIGNED:
                raise ValidationError("Only an assigned session can be accepted.")
            session.status = ConsultationSession.Status.ACCEPTED
            session.save(update_fields=["status"])
        elif action == "start":
            if request.user.role != User.Role.USER:
                raise PermissionDenied("Only the session user can start this session.")
            try:
                session = start_session(session.pk, request.user.pk)
            except ValueError as error:
                raise ValidationError(str(error)) from error
            except IntegrityError as error:
                raise ValidationError("The user already has an in-progress session.") from error
        elif action == "bill":
            session = settle_elapsed_minutes(session.pk).session
        elif action == "end":
            if session.status != ConsultationSession.Status.IN_PROGRESS:
                raise ValidationError("Only an in-progress session can be ended.")
            session = settle_elapsed_minutes(
                session.pk,
                finish_reason=f"{request.user.role.lower()}: Ended",
            ).session
        else:
            raise ValidationError("Unknown session action.")

        return Response(SessionSerializer(session).data, status=status.HTTP_200_OK)
