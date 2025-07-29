from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass
from enum import Enum
from .base import BaseMiddleware, MiddlewareContext


class PermissionAction(str, Enum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    EXECUTE = "execute"


@dataclass
class AuthorizationPolicy:
    """Single authorization policy"""
    name: str
    resource: str
    action: PermissionAction
    conditions: List[Callable[[Dict[str, Any]], bool]]
    description: Optional[str] = None


@dataclass
class UserContext:
    """User context for authorization"""
    user_id: str
    roles: List[str]
    permissions: List[str]
    attributes: Dict[str, Any]
    organization_id: Optional[str] = None


class AuthorizationError(Exception):
    """Custom exception for authorization failures"""
    pass


class AuthorizationMiddleware(BaseMiddleware):
    """ABAC (Attribute-Based Access Control) middleware"""
    
    def __init__(self, name: str = "authorization"):
        super().__init__(name, priority=20)  # After validation
        self.policies: Dict[str, List[AuthorizationPolicy]] = {}
        self.user_context_provider: Optional[Callable[[Any], UserContext]] = None
    
    def set_user_context_provider(self, provider: Callable[[Any], UserContext]):
        """Set function to extract user context from DTO"""
        self.user_context_provider = provider
    
    def add_policy(self, dto_type: str, policy: AuthorizationPolicy):
        """Add authorization policy for specific DTO type"""
        if dto_type not in self.policies:
            self.policies[dto_type] = []
        self.policies[dto_type].append(policy)
    
    def pre_execute(self, context: MiddlewareContext) -> MiddlewareContext:
        """Authorize request before execution"""
        dto_type_name = type(context.dto).__name__
        
        # Get user context
        if not self.user_context_provider:
            raise RuntimeError("User context provider not configured")
        
        user_context = self.user_context_provider(context.dto)
        context.user_context = user_context.__dict__
        
        # Check policies
        policies = self.policies.get(dto_type_name, [])
        for policy in policies:
            if not self._evaluate_policy(policy, user_context, context):
                raise AuthorizationError(
                    f"Access denied: Policy '{policy.name}' failed for user {user_context.user_id}"
                )
        
        context.add_metadata("authorization_passed", True)
        return context
    
    def _evaluate_policy(self, policy: AuthorizationPolicy, 
                        user_context: UserContext, context: MiddlewareContext) -> bool:
        """Evaluate single authorization policy"""
        
        # Create evaluation context
        eval_context = {
            "user": user_context,
            "dto": context.dto,
            "metadata": context.metadata,
            "resource": policy.resource,
            "action": policy.action
        }
        
        # Evaluate all conditions
        for condition in policy.conditions:
            if not condition(eval_context):
                return False
        
        return True
    
    def post_execute(self, context: MiddlewareContext, result: Any) -> Any:
        """Post-execution authorization if needed"""
        return result


# Authorization condition helpers
def has_role(required_role: str):
    """Check if user has specific role"""
    def condition(ctx: Dict[str, Any]) -> bool:
        return required_role in ctx["user"].roles
    return condition


def has_permission(required_permission: str):
    """Check if user has specific permission"""
    def condition(ctx: Dict[str, Any]) -> bool:
        return required_permission in ctx["user"].permissions
    return condition


def owns_resource():
    """Check if user owns the resource"""
    def condition(ctx: Dict[str, Any]) -> bool:
        dto = ctx["dto"]
        user = ctx["user"]
        return hasattr(dto, 'user_id') and dto.user_id == user.user_id
    return condition


def same_organization():
    """Check if user belongs to same organization as resource"""
    def condition(ctx: Dict[str, Any]) -> bool:
        dto = ctx["dto"]
        user = ctx["user"]
        return (hasattr(dto, 'organization_id') and 
                user.organization_id and
                dto.organization_id == user.organization_id)
    return condition


def attribute_matches(attribute_name: str, expected_value: Any):
    """Check if user attribute matches expected value"""
    def condition(ctx: Dict[str, Any]) -> bool:
        user = ctx["user"]
        return user.attributes.get(attribute_name) == expected_value
    return condition