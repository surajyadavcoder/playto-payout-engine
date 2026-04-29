from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status


class InsufficientFundsError(Exception):
    pass


class InvalidStateTransitionError(Exception):
    pass


class IdempotencyConflictError(Exception):
    pass


class PayoutNotFoundError(Exception):
    pass


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if isinstance(exc, InsufficientFundsError):
        return Response(
            {"error": "insufficient_funds", "detail": str(exc)},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    if isinstance(exc, InvalidStateTransitionError):
        return Response(
            {"error": "invalid_state_transition", "detail": str(exc)},
            status=status.HTTP_409_CONFLICT,
        )

    if isinstance(exc, ValueError):
        return Response(
            {"error": "validation_error", "detail": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return response
