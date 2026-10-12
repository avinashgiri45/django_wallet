from rest_framework import serializers

from .models import ConsultationSession, Recharge, User, Wallet, WalletTransaction


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["id", "username", "password", "role"]

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ["balance", "updated_at"]


class RechargeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Recharge
        fields = ["id", "amount", "reference", "status", "created_at"]
        read_only_fields = ["id", "reference", "status", "created_at"]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Recharge amount must be greater than zero.")
        return value


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = ["id", "transaction_type", "amount", "recharge_id", "session_id", "minute_number", "created_at"]


class SessionCreateSerializer(serializers.ModelSerializer):
    provider_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = ConsultationSession
        fields = ["id", "provider_id", "cost_per_minute", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]

    def validate_cost_per_minute(self, value):
        if value <= 0:
            raise serializers.ValidationError("Per-minute cost must be greater than zero.")
        return value

    def validate_provider_id(self, value):
        if value is None:
            return value
        if not User.objects.filter(pk=value, role=User.Role.PROVIDER).exists():
            raise serializers.ValidationError("A valid provider account is required.")
        return value

    def create(self, validated_data):
        provider_id = validated_data.pop("provider_id", None)
        return ConsultationSession.objects.create(
            user=self.context["request"].user,
            provider_id=provider_id,
            status=(
                ConsultationSession.Status.ASSIGNED
                if provider_id
                else ConsultationSession.Status.UNASSIGNED
            ),
            **validated_data,
        )


class SessionSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(read_only=True)
    provider_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = ConsultationSession
        fields = [
            "id",
            "user_id",
            "provider_id",
            "status",
            "cost_per_minute",
            "created_at",
            "started_at",
            "ended_at",
            "billed_minutes",
            "talk_time_seconds",
            "end_reason",
        ]
