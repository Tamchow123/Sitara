from django.urls import re_path

from . import views

# Slash-OPTIONAL routes: the Next.js rewrite normalises /api/v1/auth/csrf/
# to /api/v1/auth/csrf before proxying, and an APPEND_SLASH 301 would loop
# through the proxy (and would drop POST bodies). Both spellings resolve
# directly; the canonical documented form keeps the trailing slash.
urlpatterns = [
    re_path(r"^csrf/?$", views.CsrfView.as_view(), name="auth-csrf"),
    re_path(r"^register/?$", views.RegisterView.as_view(), name="auth-register"),
    re_path(r"^login/?$", views.LoginView.as_view(), name="auth-login"),
    re_path(r"^logout/?$", views.LogoutView.as_view(), name="auth-logout"),
    re_path(r"^me/?$", views.MeView.as_view(), name="auth-me"),
]
