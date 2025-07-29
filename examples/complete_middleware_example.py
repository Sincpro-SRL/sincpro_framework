#!/usr/bin/env python3
"""
Comprehensive example demonstrating the complete middleware system in Sincpro Framework.

This example shows:
1. Validation middleware with business rules
2. Authorization middleware with ABAC policies
3. Caching middleware with intelligent caching
4. Complete middleware pipeline working together
"""

from sincpro_framework import UseFramework, Feature, DataTransferObject
from sincpro_framework.middleware import (
    ValidationMiddleware, ValidationRule,
    AuthorizationMiddleware, AuthorizationPolicy, UserContext, PermissionAction,
    CachingMiddleware, CacheConfig, InMemoryCacheProvider,
    has_role, has_permission, owns_resource
)


# DTOs
class UserProfileRequest(DataTransferObject):
    user_id: str
    requesting_user_id: str
    organization_id: str
    include_sensitive_data: bool = False


class UserProfileResponse(DataTransferObject):
    user_id: str
    name: str
    email: str
    organization_id: str
    role: str
    sensitive_data: dict = None


# Simulated user database
USERS_DB = {
    "user123": {
        "name": "John Doe",
        "email": "john.doe@company.com",
        "organization_id": "org1",
        "role": "user",
        "roles": ["user"],
        "permissions": ["read_profile"],
        "sensitive_data": {"salary": 75000, "ssn": "***-**-1234"}
    },
    "admin456": {
        "name": "Admin User",
        "email": "admin@company.com",
        "organization_id": "org1",
        "role": "admin",
        "roles": ["admin", "user"],
        "permissions": ["read_profile", "read_sensitive_data", "admin_access"],
        "sensitive_data": {"clearance_level": "TOP_SECRET"}
    },
    "user789": {
        "name": "Jane Smith",
        "email": "jane.smith@othercompany.com",
        "organization_id": "org2",
        "role": "user",
        "roles": ["user"],
        "permissions": ["read_profile"],
        "sensitive_data": {"department": "finance"}
    }
}


# Create framework instance
profile_framework = UseFramework("profile_system", log_after_execution=False)

# 1. Setup Validation Middleware
validation_middleware = ValidationMiddleware(strict_mode=True)

def validate_user_exists(dto: UserProfileRequest) -> bool:
    """Business rule: Requested user must exist"""
    return dto.user_id in USERS_DB

def validate_requesting_user_exists(dto: UserProfileRequest) -> bool:
    """Business rule: Requesting user must exist"""
    return dto.requesting_user_id in USERS_DB

validation_middleware.add_validation_rule(
    "UserProfileRequest",
    ValidationRule(
        name="user_exists",
        validator=validate_user_exists,
        error_message="Requested user does not exist"
    )
)

validation_middleware.add_validation_rule(
    "UserProfileRequest",
    ValidationRule(
        name="requesting_user_exists",
        validator=validate_requesting_user_exists,
        error_message="Requesting user does not exist"
    )
)

profile_framework.add_middleware(validation_middleware)

# 2. Setup Authorization Middleware
authorization_middleware = AuthorizationMiddleware()

def user_context_provider(dto: UserProfileRequest) -> UserContext:
    """Extract user context from DTO"""
    user_data = USERS_DB[dto.requesting_user_id]
    return UserContext(
        user_id=dto.requesting_user_id,
        roles=user_data["roles"],
        permissions=user_data["permissions"],
        attributes={"department": user_data.get("department", "general")},
        organization_id=user_data["organization_id"]
    )

authorization_middleware.set_user_context_provider(user_context_provider)

# Policy 1: Basic profile access - user can read profiles in same organization
basic_profile_policy = AuthorizationPolicy(
    name="basic_profile_access",
    resource="user_profile",
    action=PermissionAction.READ,
    conditions=[
        has_permission("read_profile"),
        lambda ctx: (
            # Can access own profile OR profiles in same organization
            ctx["dto"].user_id == ctx["user"].user_id or
            (USERS_DB[ctx["dto"].user_id]["organization_id"] == ctx["user"].organization_id)
        )
    ]
)

# Policy 2: Sensitive data access - requires special permission and same organization
def sensitive_data_condition(ctx):
    """Only apply this policy if sensitive data is actually being requested"""
    return ctx["dto"].include_sensitive_data

sensitive_data_policy = AuthorizationPolicy(
    name="sensitive_data_access",
    resource="sensitive_user_data",
    action=PermissionAction.READ,
    conditions=[
        lambda ctx: not ctx["dto"].include_sensitive_data or has_permission("read_sensitive_data")(ctx),
        lambda ctx: not ctx["dto"].include_sensitive_data or (USERS_DB[ctx["dto"].user_id]["organization_id"] == ctx["user"].organization_id)
    ]
)

authorization_middleware.add_policy("UserProfileRequest", basic_profile_policy)
authorization_middleware.add_policy("UserProfileRequest", sensitive_data_policy)

profile_framework.add_middleware(authorization_middleware)

# 3. Setup Caching Middleware
cache_provider = InMemoryCacheProvider()
caching_middleware = CachingMiddleware(cache_provider)

def profile_cache_key(dto: UserProfileRequest) -> str:
    """Generate cache key for profile requests"""
    sensitive_flag = "sensitive" if dto.include_sensitive_data else "basic"
    return f"profile:{dto.user_id}:{sensitive_flag}"

def should_cache_profile(context) -> bool:
    """Only cache if not requesting sensitive data or if user is admin"""
    dto = context.dto
    user_data = USERS_DB[dto.requesting_user_id]
    return not dto.include_sensitive_data or "admin" in user_data["roles"]

profile_cache_config = CacheConfig(
    ttl_seconds=300,  # 5 minutes
    cache_key_generator=profile_cache_key,
    invalidation_tags=["user_profiles"],
    cache_condition=should_cache_profile
)

caching_middleware.configure_caching("UserProfileRequest", profile_cache_config)
profile_framework.add_middleware(caching_middleware)

# 4. Define the Feature
@profile_framework.feature(UserProfileRequest)
class GetUserProfileFeature(Feature):
    def execute(self, dto: UserProfileRequest) -> UserProfileResponse:
        print(f"    🔄 Executing profile lookup for user {dto.user_id}")
        
        user_data = USERS_DB[dto.user_id]
        
        response = UserProfileResponse(
            user_id=dto.user_id,
            name=user_data["name"],
            email=user_data["email"],
            organization_id=user_data["organization_id"],
            role=user_data["role"]
        )
        
        # Include sensitive data if requested and authorized
        if dto.include_sensitive_data:
            response.sensitive_data = user_data.get("sensitive_data", {})
        
        return response


def main():
    print("=== Sincpro Framework Complete Middleware Example ===\n")
    
    # Test 1: Valid request - user accessing own profile
    print("Test 1: User accessing own profile")
    try:
        request = UserProfileRequest(
            user_id="user123",
            requesting_user_id="user123",
            organization_id="org1"
        )
        
        result = profile_framework(request)
        print(f"✅ Success: Retrieved profile for {result.name} ({result.email})")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print()
    
    # Test 2: Valid request - same organization access
    print("Test 2: User accessing colleague's profile in same organization")
    try:
        request = UserProfileRequest(
            user_id="admin456",
            requesting_user_id="user123",
            organization_id="org1"
        )
        
        result = profile_framework(request)
        print(f"✅ Success: Retrieved profile for {result.name} ({result.email})")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print()
    
    # Test 3: Authorization failure - cross organization access
    print("Test 3: User trying to access profile from different organization")
    try:
        request = UserProfileRequest(
            user_id="user789",  # From org2
            requesting_user_id="user123",  # From org1
            organization_id="org2"
        )
        
        result = profile_framework(request)
        print(f"✅ Success: {result.name}")
    except Exception as e:
        print(f"❌ Authorization Error: {e}")
    
    print()
    
    # Test 4: Sensitive data access - unauthorized
    print("Test 4: Regular user trying to access sensitive data")
    try:
        request = UserProfileRequest(
            user_id="user123",
            requesting_user_id="user123",
            organization_id="org1",
            include_sensitive_data=True
        )
        
        result = profile_framework(request)
        print(f"✅ Success: Retrieved sensitive data: {result.sensitive_data}")
    except Exception as e:
        print(f"❌ Authorization Error: {e}")
    
    print()
    
    # Test 5: Sensitive data access - authorized admin
    print("Test 5: Admin accessing sensitive data")
    try:
        request = UserProfileRequest(
            user_id="user123",
            requesting_user_id="admin456",  # Admin user
            organization_id="org1",
            include_sensitive_data=True
        )
        
        result = profile_framework(request)
        print(f"✅ Success: Retrieved sensitive data: {result.sensitive_data}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print()
    
    # Test 6: Validation failure - nonexistent user
    print("Test 6: Requesting nonexistent user profile")
    try:
        request = UserProfileRequest(
            user_id="nonexistent",
            requesting_user_id="user123",
            organization_id="org1"
        )
        
        result = profile_framework(request)
        print(f"✅ Success: {result.name}")
    except Exception as e:
        print(f"❌ Validation Error: {e}")
    
    print()
    
    # Test 7: Cache demonstration
    print("Test 7: Cache demonstration - same request twice")
    try:
        request = UserProfileRequest(
            user_id="admin456",
            requesting_user_id="admin456",
            organization_id="org1"
        )
        
        print("  First request (cache miss):")
        result1 = profile_framework(request)
        print(f"    ✅ Success: {result1.name}")
        
        print("  Second request (cache hit):")
        result2 = profile_framework(request)
        print(f"    ✅ Success: {result2.name}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print()
    
    # Test 8: Middleware disabled
    print("Test 8: Disable middleware and access cross-organization profile")
    profile_framework.disable_middleware()
    
    try:
        request = UserProfileRequest(
            user_id="user789",  # From org2
            requesting_user_id="user123",  # From org1
            organization_id="org2"
        )
        
        result = profile_framework(request)
        print(f"✅ Success (middleware disabled): Retrieved {result.name} from {result.organization_id}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    profile_framework.enable_middleware()
    print("    Middleware re-enabled")
    
    print("\n=== Middleware Pipeline Summary ===")
    print("1. ✅ Validation Middleware: Business rule validation")
    print("2. ✅ Authorization Middleware: ABAC policy enforcement")
    print("3. ✅ Caching Middleware: Intelligent caching with TTL")
    print("4. ✅ Complete Integration: All middleware working together")


if __name__ == "__main__":
    main()