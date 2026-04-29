from django.urls import path
from . import views

urlpatterns = [
    path('merchants/', views.list_merchants, name='list_merchants'),
    path('merchants/<uuid:merchant_id>/', views.merchant_dashboard, name='merchant_dashboard'),
    path('merchants/<uuid:merchant_id>/payouts/', views.create_payout, name='create_payout'),
    path('merchants/<uuid:merchant_id>/payouts/list/', views.list_payouts, name='list_payouts'),
    path('merchants/<uuid:merchant_id>/payouts/<uuid:payout_id>/', views.get_payout, name='get_payout'),
    path('admin/retry-stuck/', views.retry_stuck_payouts_view, name='retry_stuck'),
]
