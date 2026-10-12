from django.urls import path

from .views import (
    MockRechargeCallbackView,
    RechargeListCreateView,
    RegisterView,
    SessionActionView,
    SessionListCreateView,
    WalletTransactionListView,
    WalletView,
)


urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("wallet/", WalletView.as_view(), name="wallet"),
    path("wallet/transactions/", WalletTransactionListView.as_view(), name="wallet-transactions"),
    path("recharges/", RechargeListCreateView.as_view(), name="recharges"),
    path(
        "recharges/<uuid:reference>/callback/",
        MockRechargeCallbackView.as_view(),
        name="recharge-callback",
    ),
    path("sessions/", SessionListCreateView.as_view(), name="sessions"),
    path(
        "sessions/<int:pk>/<str:action>/",
        SessionActionView.as_view(),
        name="session-action",
    ),
]
