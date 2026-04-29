from rest_framework import serializers
from .models import Merchant, BankAccount, LedgerEntry, PayoutRequest


class BankAccountSerializer(serializers.ModelSerializer):
    masked_account = serializers.SerializerMethodField()

    class Meta:
        model = BankAccount
        fields = ['id', 'account_holder_name', 'ifsc_code', 'masked_account', 'is_primary', 'created_at']

    def get_masked_account(self, obj):
        return f"****{obj.account_number[-4:]}"


class LedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerEntry
        fields = ['id', 'entry_type', 'amount_paise', 'description', 'reference_id', 'created_at']


class PayoutRequestSerializer(serializers.ModelSerializer):
    bank_account = BankAccountSerializer(read_only=True)

    class Meta:
        model = PayoutRequest
        fields = [
            'id', 'amount_paise', 'status', 'bank_account',
            'idempotency_key', 'failure_reason', 'attempt_count',
            'created_at', 'updated_at', 'processing_started_at', 'completed_at',
        ]


class CreatePayoutSerializer(serializers.Serializer):
    amount_paise = serializers.IntegerField(min_value=1)
    bank_account_id = serializers.UUIDField()

    def validate_amount_paise(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be positive")
        # Minimum payout: 100 rupees = 10000 paise
        if value < 10000:
            raise serializers.ValidationError("Minimum payout is ₹100 (10000 paise)")
        return value


class MerchantDashboardSerializer(serializers.ModelSerializer):
    balance = serializers.SerializerMethodField()
    bank_accounts = BankAccountSerializer(many=True, read_only=True)
    recent_transactions = serializers.SerializerMethodField()
    recent_payouts = serializers.SerializerMethodField()

    class Meta:
        model = Merchant
        fields = [
            'id', 'name', 'email', 'business_name',
            'balance', 'bank_accounts', 'recent_transactions', 'recent_payouts',
        ]

    def get_balance(self, obj):
        return obj.get_balance_summary()

    def get_recent_transactions(self, obj):
        entries = obj.ledger_entries.all()[:20]
        return LedgerEntrySerializer(entries, many=True).data

    def get_recent_payouts(self, obj):
        payouts = obj.payout_requests.select_related('bank_account').order_by('-created_at')[:20]
        return PayoutRequestSerializer(payouts, many=True).data
