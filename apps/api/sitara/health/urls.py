from django.urls import path

from . import views

urlpatterns = [
    path("health/live", views.live, name="health-live"),
    path("health/ready", views.ready, name="health-ready"),
    path("config/public", views.public_config, name="config-public"),
]
