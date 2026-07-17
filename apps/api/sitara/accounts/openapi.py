"""Schema serializers for the session-authentication endpoints.

Documentation-only: the views build their JSON directly (see views.py).
Password fields are ``write_only`` so they appear only in request bodies,
never in any response component. The user representation is exactly the
public ``{id, email}`` pair — never staff flags, permissions, password
state or session details.
"""

from rest_framework import serializers

# Matches settings.AUTH_PASSWORD_MAX_LENGTH — documents the cheap upfront cap.
PASSWORD_MAX_LENGTH = 128


class AuthUserSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True, help_text="The user's UUID.")
    email = serializers.EmailField(read_only=True, help_text="Canonical (lower-cased) email.")


class CsrfResponseSerializer(serializers.Serializer):
    csrf_token = serializers.CharField(
        help_text="Send this back as the X-CSRFToken header on unsafe requests."
    )


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True, max_length=PASSWORD_MAX_LENGTH, style={"input_type": "password"}
    )
    password_confirm = serializers.CharField(
        write_only=True, max_length=PASSWORD_MAX_LENGTH, style={"input_type": "password"}
    )


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True, max_length=PASSWORD_MAX_LENGTH, style={"input_type": "password"}
    )


class LogoutSerializer(serializers.Serializer):
    """Logout takes an empty JSON object; only the CSRF header matters."""


class AuthSuccessResponseSerializer(serializers.Serializer):
    authenticated = serializers.BooleanField(help_text="True on a successful login/registration.")
    user = AuthUserSerializer()
    csrf_token = serializers.CharField(help_text="Freshly rotated token to use going forward.")


class LogoutResponseSerializer(serializers.Serializer):
    authenticated = serializers.BooleanField(help_text="Always false after logout.")
    user = AuthUserSerializer(allow_null=True, help_text="Always null after logout.")
    csrf_token = serializers.CharField(help_text="Fresh anonymous token.")


class MeResponseSerializer(serializers.Serializer):
    authenticated = serializers.BooleanField()
    user = AuthUserSerializer(allow_null=True, help_text="The user when authenticated, else null.")
