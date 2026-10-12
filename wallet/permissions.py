from rest_framework.permissions import BasePermission

from .models import User


class IsUserRole(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == User.Role.USER


class IsProviderRole(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == User.Role.PROVIDER
