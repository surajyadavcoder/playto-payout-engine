import uuid
import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django_q.tasks import async_task

from .models import Merchant, PayoutRequest
from .serializers import (
    MerchantDashboardSerializer,
    CreatePayoutSerializer,
    PayoutRequestSerializer,
)
from .services import get_or_create_payout
from .exceptions import InsufficientFundsError

logger = logging.getLogger(__name__)


@api_view(['GET'])
def list_merchants(request):
    """List all merchants (for the demo dashboard selector)."""
    from .models import Merchant
    merchants = Merchant.objects.all().order_by('business_name')
    data = [{'id': str(m.id), 'name': m.name, 'business_name': m.business_name, 'email': m.email}
            for m in merchants]
    return Response(data)


@api_view(['GET'])
def merchant_dashboard(request, merchant_id):
    """Full dashboard data for a merchant."""
    merchant = get_object_or_404(Merchant, id=merchant_id)
    serializer = MerchantDashboardSerializer(merchant)
    return Response(serializer.data)


@api_view(['POST'])
def create_payout(request, merchant_id):
    """
    POST /api/v1/merchants/{merchant_id}/payouts/

    Required header: Idempotency-Key (UUID)

    Idempotency: If we've seen this key for this merchant before (and it hasn't expired),
    we return the original response with HTTP 200 (not 201).
    """
    merchant = get_object_or_404(Merchant, id=merchant_id)

    # Validate idempotency key
    idempotency_key = request.headers.get('Idempotency-Key', '').strip()
    if not idempotency_key:
        return Response(
            {"error": "missing_idempotency_key", "detail": "Idempotency-Key header is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        uuid.UUID(idempotency_key)
    except ValueError:
        return Response(
            {"error": "invalid_idempotency_key", "detail": "Idempotency-Key must be a valid UUID"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Validate request body
    serializer = CreatePayoutSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"error": "validation_error", "detail": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    validated = serializer.validated_data

    try:
        payout, created = get_or_create_payout(
            merchant_id=str(merchant.id),
            amount_paise=validated['amount_paise'],
            bank_account_id=str(validated['bank_account_id']),
            idempotency_key=idempotency_key,
        )
    except InsufficientFundsError as e:
        return Response(
            {"error": "insufficient_funds", "detail": str(e)},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except Merchant.DoesNotExist:
        return Response({"error": "merchant_not_found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.exception(f"Unexpected error creating payout: {e}")
        return Response(
            {"error": "internal_error", "detail": "An unexpected error occurred"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Enqueue background processing for new payouts
    if created:
        async_task(
            'payout_engine.services.process_payout',
            str(payout.id),
            task_name=f"process_payout_{payout.id}",
        )

    response_data = PayoutRequestSerializer(payout).data
    response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK

    return Response(response_data, status=response_status)


@api_view(['GET'])
def list_payouts(request, merchant_id):
    """List all payouts for a merchant."""
    merchant = get_object_or_404(Merchant, id=merchant_id)
    payouts = merchant.payout_requests.select_related('bank_account').order_by('-created_at')

    # Filter by status if provided
    status_filter = request.query_params.get('status')
    if status_filter:
        payouts = payouts.filter(status=status_filter)

    serializer = PayoutRequestSerializer(payouts[:50], many=True)
    return Response(serializer.data)


@api_view(['GET'])
def get_payout(request, merchant_id, payout_id):
    """Get a specific payout."""
    merchant = get_object_or_404(Merchant, id=merchant_id)
    payout = get_object_or_404(PayoutRequest, id=payout_id, merchant=merchant)
    return Response(PayoutRequestSerializer(payout).data)


@api_view(['POST'])
def retry_stuck_payouts_view(request):
    """Admin endpoint to manually trigger stuck payout detection."""
    from .services import retry_stuck_payouts
    retry_stuck_payouts()
    return Response({"status": "triggered"})
