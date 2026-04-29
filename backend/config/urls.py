from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse

def health(request):
    return JsonResponse({"status": "ok", "service": "playto-payout-engine"})

urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', health),
    path('api/v1/', include('payout_engine.urls')),
]
