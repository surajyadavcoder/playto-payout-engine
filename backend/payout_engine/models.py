"""
MODELS - The heart of the payout engine.

Design decisions:
- Balance is NEVER stored as a column. It is always derived from SUM(credits) - SUM(debits).
  This eliminates a whole class of consistency bugs where the balance column drifts from reality.
- All amounts in PAISE (integer). No FloatField, no DecimalField. Integer arithmetic is exact.
- LedgerEntry is append-only. We never update or delete entries. This gives us a full audit trail.
- PayoutRequest has a `held_paise` field so the balance formula is:
    available = credits - debits - held
  This prevents double-spending while a payout is in-flight.
"""

import uuid
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class Merchant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    business_name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.business_name} ({self.email})"

    def get_balance_summary(self):
        """
        Single source of truth for balance.
        Uses DB aggregation — no Python arithmetic on fetched rows.
        The invariant: available + held = credits - debits (always).
        """
        from django.db.models import Sum, Value
        from django.db.models.functions import Coalesce

        entries = self.ledger_entries.aggregate(
            total_credits=Coalesce(
                Sum('amount_paise', filter=models.Q(entry_type=LedgerEntry.CREDIT)),
                Value(0)
            ),
            total_debits=Coalesce(
                Sum('amount_paise', filter=models.Q(entry_type=LedgerEntry.DEBIT)),
                Value(0)
            ),
        )

        total_credits = entries['total_credits']
        total_debits = entries['total_debits']

        # Held = sum of paise held by pending/processing payouts
        held = self.payout_requests.filter(
            status__in=[PayoutRequest.PENDING, PayoutRequest.PROCESSING]
        ).aggregate(
            total=Coalesce(Sum('amount_paise'), Value(0))
        )['total']

        net = total_credits - total_debits
        available = net - held

        return {
            'total_credits_paise': total_credits,
            'total_debits_paise': total_debits,
            'net_paise': net,
            'held_paise': held,
            'available_paise': available,
        }


class BankAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='bank_accounts')
    account_number = models.CharField(max_length=20)
    ifsc_code = models.CharField(max_length=11)
    account_holder_name = models.CharField(max_length=255)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.account_holder_name} - {self.account_number[-4:].zfill(4)}"


class LedgerEntry(models.Model):
    """
    Append-only ledger. Every money movement creates a row here.
    Credits: customer payments coming in.
    Debits: successful payouts going out.

    NOTE: We do NOT create a debit entry when a payout is requested.
    Instead, payout.amount_paise is counted as "held" until the payout
    is completed (debit entry created) or failed (held is released, no entry).
    """
    CREDIT = 'credit'
    DEBIT = 'debit'
    ENTRY_TYPES = [(CREDIT, 'Credit'), (DEBIT, 'Debit')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name='ledger_entries')
    entry_type = models.CharField(max_length=10, choices=ENTRY_TYPES)
    amount_paise = models.BigIntegerField()  # Always positive
    description = models.CharField(max_length=500)
    reference_id = models.CharField(max_length=255, blank=True)  # e.g., payout UUID or payment ID
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['merchant', 'entry_type']),
            models.Index(fields=['merchant', 'created_at']),
        ]

    def __str__(self):
        return f"{self.entry_type.upper()} {self.amount_paise}p for {self.merchant}"


class PayoutRequest(models.Model):
    """
    State machine: pending -> processing -> completed
                                         -> failed

    Illegal transitions are enforced in the service layer and tested.
    """
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (PROCESSING, 'Processing'),
        (COMPLETED, 'Completed'),
        (FAILED, 'Failed'),
    ]

    # Legal state transitions
    VALID_TRANSITIONS = {
        PENDING: [PROCESSING],
        PROCESSING: [COMPLETED, FAILED],
        COMPLETED: [],   # Terminal
        FAILED: [],      # Terminal
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name='payout_requests')
    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name='payouts')
    amount_paise = models.BigIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    attempt_count = models.IntegerField(default=0)
    failure_reason = models.TextField(blank=True)

    # Idempotency
    idempotency_key = models.CharField(max_length=255, db_index=True)
    idempotency_key_expires_at = models.DateTimeField()

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # One idempotency key per merchant (keys are scoped per merchant)
        unique_together = [('merchant', 'idempotency_key')]
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['merchant', 'status']),
        ]

    def __str__(self):
        return f"Payout {self.id} | {self.merchant} | {self.amount_paise}p | {self.status}"

    def can_transition_to(self, new_status):
        """
        This is where failed->completed and any backward transition is blocked.
        Called before every status change.
        """
        return new_status in self.VALID_TRANSITIONS.get(self.status, [])
