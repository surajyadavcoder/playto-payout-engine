"""
Tests for the two hardest properties: concurrency correctness and idempotency.

Run with: python manage.py test payout_engine.tests
"""
import uuid
import threading
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from datetime import timedelta

from .models import Merchant, BankAccount, LedgerEntry, PayoutRequest
from .services import get_or_create_payout, transition_payout_status
from .exceptions import InsufficientFundsError, InvalidStateTransitionError


def make_merchant(name='Test Merchant', email=None):
    email = email or f'{uuid.uuid4()}@test.com'
    merchant = Merchant.objects.create(
        name=name,
        email=email,
        business_name=name,
    )
    bank = BankAccount.objects.create(
        merchant=merchant,
        account_number='1234567890123456',
        ifsc_code='HDFC0001234',
        account_holder_name=name,
        is_primary=True,
    )
    return merchant, bank


def add_credit(merchant, amount_paise, description='Test credit'):
    return LedgerEntry.objects.create(
        merchant=merchant,
        entry_type=LedgerEntry.CREDIT,
        amount_paise=amount_paise,
        description=description,
        reference_id=f'TEST-{uuid.uuid4()}',
    )


class ConcurrencyTest(TransactionTestCase):
    """
    Uses TransactionTestCase (not TestCase) because we need real DB transactions
    to test concurrency. TestCase wraps everything in a single transaction,
    which would make SELECT FOR UPDATE a no-op across threads.
    """

    def test_concurrent_overdraft_prevention(self):
        """
        A merchant with 10000p tries two simultaneous 6000p payouts.
        Exactly one must succeed, the other must be rejected.

        This is the core invariant: sum of completed+pending+processing payouts
        must never exceed total credits minus total debits.
        """
        merchant, bank = make_merchant('Concurrent Test')
        add_credit(merchant, 10000)  # ₹100

        results = []
        errors = []

        def attempt_payout(key):
            try:
                payout, created = get_or_create_payout(
                    merchant_id=str(merchant.id),
                    amount_paise=6000,  # ₹60 each — both can't fit in ₹100
                    bank_account_id=str(bank.id),
                    idempotency_key=key,
                )
                results.append(('success', payout.id))
            except InsufficientFundsError as e:
                results.append(('rejected', str(e)))
            except Exception as e:
                errors.append(str(e))

        key1 = str(uuid.uuid4())
        key2 = str(uuid.uuid4())

        t1 = threading.Thread(target=attempt_payout, args=(key1,))
        t2 = threading.Thread(target=attempt_payout, args=(key2,))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")
        self.assertEqual(len(results), 2, "Both threads should have a result")

        successes = [r for r in results if r[0] == 'success']
        rejections = [r for r in results if r[0] == 'rejected']

        self.assertEqual(len(successes), 1, f"Exactly 1 payout should succeed, got: {results}")
        self.assertEqual(len(rejections), 1, f"Exactly 1 payout should be rejected, got: {results}")

        # Verify balance integrity: held + available = net
        summary = merchant.get_balance_summary()
        self.assertEqual(
            summary['net_paise'],
            summary['available_paise'] + summary['held_paise'],
            "Balance integrity violated: net != available + held"
        )

        # held should be 6000 (the successful one is pending)
        self.assertEqual(summary['held_paise'], 6000)
        self.assertEqual(summary['available_paise'], 4000)

    def test_balance_integrity_after_completed_payout(self):
        """After a payout completes, the debit entry must reduce the balance correctly."""
        merchant, bank = make_merchant('Balance Integrity Test')
        add_credit(merchant, 50000)  # ₹500

        payout, _ = get_or_create_payout(
            merchant_id=str(merchant.id),
            amount_paise=20000,
            bank_account_id=str(bank.id),
            idempotency_key=str(uuid.uuid4()),
        )

        # Simulate completion
        transition_payout_status(str(payout.id), PayoutRequest.PROCESSING)
        transition_payout_status(str(payout.id), PayoutRequest.COMPLETED)

        summary = merchant.get_balance_summary()
        self.assertEqual(summary['total_credits_paise'], 50000)
        self.assertEqual(summary['total_debits_paise'], 20000)
        self.assertEqual(summary['net_paise'], 30000)
        self.assertEqual(summary['held_paise'], 0)
        self.assertEqual(summary['available_paise'], 30000)

    def test_failed_payout_returns_funds(self):
        """On failure, held funds must return to available. No duplicate debit."""
        merchant, bank = make_merchant('Fund Return Test')
        add_credit(merchant, 50000)

        payout, _ = get_or_create_payout(
            merchant_id=str(merchant.id),
            amount_paise=30000,
            bank_account_id=str(bank.id),
            idempotency_key=str(uuid.uuid4()),
        )

        # While pending, 30000 is held
        summary = merchant.get_balance_summary()
        self.assertEqual(summary['held_paise'], 30000)
        self.assertEqual(summary['available_paise'], 20000)

        # Fail the payout
        transition_payout_status(str(payout.id), PayoutRequest.PROCESSING)
        transition_payout_status(str(payout.id), PayoutRequest.FAILED, 'Bank rejected')

        # Funds should be fully available again, no debit entry created
        summary = merchant.get_balance_summary()
        self.assertEqual(summary['held_paise'], 0)
        self.assertEqual(summary['available_paise'], 50000)
        self.assertEqual(summary['total_debits_paise'], 0)

        # Verify no debit ledger entry was created for the failed payout
        debit_count = merchant.ledger_entries.filter(entry_type=LedgerEntry.DEBIT).count()
        self.assertEqual(debit_count, 0)


class IdempotencyTest(TestCase):

    def test_same_key_returns_same_payout(self):
        """
        Two requests with the same idempotency key must return the identical payout object.
        No duplicate payout must be created.
        """
        merchant, bank = make_merchant('Idempotency Test')
        add_credit(merchant, 100000)

        key = str(uuid.uuid4())

        payout1, created1 = get_or_create_payout(
            merchant_id=str(merchant.id),
            amount_paise=50000,
            bank_account_id=str(bank.id),
            idempotency_key=key,
        )

        payout2, created2 = get_or_create_payout(
            merchant_id=str(merchant.id),
            amount_paise=50000,
            bank_account_id=str(bank.id),
            idempotency_key=key,
        )

        self.assertTrue(created1, "First call should create the payout")
        self.assertFalse(created2, "Second call with same key should NOT create a new payout")
        self.assertEqual(str(payout1.id), str(payout2.id), "Both calls must return the same payout ID")

        # Only one payout exists for this merchant
        total_payouts = PayoutRequest.objects.filter(merchant=merchant).count()
        self.assertEqual(total_payouts, 1)

        # Only 50000 is held, not 100000
        summary = merchant.get_balance_summary()
        self.assertEqual(summary['held_paise'], 50000)

    def test_different_keys_create_different_payouts(self):
        """Different keys must create separate payouts."""
        merchant, bank = make_merchant('Multi Key Test')
        add_credit(merchant, 200000)

        key1 = str(uuid.uuid4())
        key2 = str(uuid.uuid4())

        payout1, created1 = get_or_create_payout(
            merchant_id=str(merchant.id),
            amount_paise=50000,
            bank_account_id=str(bank.id),
            idempotency_key=key1,
        )

        payout2, created2 = get_or_create_payout(
            merchant_id=str(merchant.id),
            amount_paise=50000,
            bank_account_id=str(bank.id),
            idempotency_key=key2,
        )

        self.assertTrue(created1)
        self.assertTrue(created2)
        self.assertNotEqual(str(payout1.id), str(payout2.id))
        self.assertEqual(PayoutRequest.objects.filter(merchant=merchant).count(), 2)

    def test_expired_key_allows_new_payout(self):
        """An expired idempotency key should not block a new payout with the same key string."""
        merchant, bank = make_merchant('Expiry Test')
        add_credit(merchant, 200000)

        key = str(uuid.uuid4())

        # Create payout with artificially expired key
        payout1, _ = get_or_create_payout(
            merchant_id=str(merchant.id),
            amount_paise=50000,
            bank_account_id=str(bank.id),
            idempotency_key=key,
        )

        # Expire the key
        PayoutRequest.objects.filter(id=payout1.id).update(
            idempotency_key_expires_at=timezone.now() - timedelta(hours=1)
        )

        # New request with the same key (now expired) should create a new payout
        payout2, created2 = get_or_create_payout(
            merchant_id=str(merchant.id),
            amount_paise=50000,
            bank_account_id=str(bank.id),
            idempotency_key=key,
        )

        self.assertTrue(created2, "Expired key should allow a new payout")
        self.assertNotEqual(str(payout1.id), str(payout2.id))

    def test_key_scoped_per_merchant(self):
        """The same idempotency key used by two different merchants creates two different payouts."""
        merchant1, bank1 = make_merchant('Merchant One', 'one@test.com')
        merchant2, bank2 = make_merchant('Merchant Two', 'two@test.com')
        add_credit(merchant1, 100000)
        add_credit(merchant2, 100000)

        shared_key = str(uuid.uuid4())

        payout1, created1 = get_or_create_payout(
            merchant_id=str(merchant1.id),
            amount_paise=50000,
            bank_account_id=str(bank1.id),
            idempotency_key=shared_key,
        )

        payout2, created2 = get_or_create_payout(
            merchant_id=str(merchant2.id),
            amount_paise=50000,
            bank_account_id=str(bank2.id),
            idempotency_key=shared_key,
        )

        self.assertTrue(created1)
        self.assertTrue(created2)
        self.assertNotEqual(str(payout1.id), str(payout2.id))


class StateMachineTest(TestCase):

    def setUp(self):
        self.merchant, self.bank = make_merchant('State Machine Test')
        add_credit(self.merchant, 100000)

    def _make_payout(self):
        payout, _ = get_or_create_payout(
            merchant_id=str(self.merchant.id),
            amount_paise=10000,
            bank_account_id=str(self.bank.id),
            idempotency_key=str(uuid.uuid4()),
        )
        return payout

    def test_valid_transition_pending_to_processing(self):
        payout = self._make_payout()
        updated = transition_payout_status(str(payout.id), PayoutRequest.PROCESSING)
        self.assertEqual(updated.status, PayoutRequest.PROCESSING)

    def test_valid_transition_processing_to_completed(self):
        payout = self._make_payout()
        transition_payout_status(str(payout.id), PayoutRequest.PROCESSING)
        updated = transition_payout_status(str(payout.id), PayoutRequest.COMPLETED)
        self.assertEqual(updated.status, PayoutRequest.COMPLETED)

    def test_valid_transition_processing_to_failed(self):
        payout = self._make_payout()
        transition_payout_status(str(payout.id), PayoutRequest.PROCESSING)
        updated = transition_payout_status(str(payout.id), PayoutRequest.FAILED, 'Bank error')
        self.assertEqual(updated.status, PayoutRequest.FAILED)

    def test_illegal_transition_completed_to_pending(self):
        payout = self._make_payout()
        transition_payout_status(str(payout.id), PayoutRequest.PROCESSING)
        transition_payout_status(str(payout.id), PayoutRequest.COMPLETED)
        with self.assertRaises(InvalidStateTransitionError):
            transition_payout_status(str(payout.id), PayoutRequest.PENDING)

    def test_illegal_transition_failed_to_completed(self):
        """This is the critical one — failed payouts must NOT complete."""
        payout = self._make_payout()
        transition_payout_status(str(payout.id), PayoutRequest.PROCESSING)
        transition_payout_status(str(payout.id), PayoutRequest.FAILED, 'error')
        with self.assertRaises(InvalidStateTransitionError):
            transition_payout_status(str(payout.id), PayoutRequest.COMPLETED)

    def test_illegal_transition_pending_to_completed(self):
        """Must go through processing first."""
        payout = self._make_payout()
        with self.assertRaises(InvalidStateTransitionError):
            transition_payout_status(str(payout.id), PayoutRequest.COMPLETED)
