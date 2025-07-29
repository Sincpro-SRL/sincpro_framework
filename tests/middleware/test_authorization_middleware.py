import pytest
from sincpro_framework.middleware.authorization import (
    AuthorizationMiddleware, 
    AuthorizationPolicy, 
    UserContext, 
    PermissionAction,
    AuthorizationError,
    has_role,
    has_permission,
    owns_resource,
    same_organization,
    attribute_matches
)
from sincpro_framework.middleware.base import MiddlewareContext
from sincpro_framework import DataTransferObject


class SecureDTO(DataTransferObject):
    """Test DTO for authorization testing"""
    user_id: str
    organization_id: str
    action: str


def create_user_context(user_id: str, roles: list = None, permissions: list = None, 
                       organization_id: str = None, attributes: dict = None):
    """Helper to create user context"""
    return UserContext(
        user_id=user_id,
        roles=roles or [],
        permissions=permissions or [],
        attributes=attributes or {},
        organization_id=organization_id
    )


def test_authorization_middleware_no_policies():
    """Test authorization middleware with no policies configured"""
    middleware = AuthorizationMiddleware()
    
    def user_provider(dto):
        return create_user_context("user123")
    
    middleware.set_user_context_provider(user_provider)
    
    test_dto = SecureDTO(user_id="user123", organization_id="org1", action="read")
    context = MiddlewareContext(dto=test_dto)
    
    result_context = middleware.pre_execute(context)
    
    assert result_context == context
    assert context.get_metadata("authorization_passed") is True


def test_authorization_middleware_missing_provider():
    """Test authorization middleware without user context provider"""
    middleware = AuthorizationMiddleware()
    
    test_dto = SecureDTO(user_id="user123", organization_id="org1", action="read")
    context = MiddlewareContext(dto=test_dto)
    
    with pytest.raises(RuntimeError, match="User context provider not configured"):
        middleware.pre_execute(context)


def test_authorization_middleware_role_based():
    """Test role-based authorization"""
    middleware = AuthorizationMiddleware()
    
    def user_provider(dto):
        return create_user_context(
            user_id=dto.user_id,
            roles=["user", "editor"]
        )
    
    middleware.set_user_context_provider(user_provider)
    
    # Add policy requiring admin role
    admin_policy = AuthorizationPolicy(
        name="admin_only",
        resource="secure_resource",
        action=PermissionAction.UPDATE,
        conditions=[has_role("admin")]
    )
    
    middleware.add_policy("SecureDTO", admin_policy)
    
    test_dto = SecureDTO(user_id="user123", organization_id="org1", action="update")
    context = MiddlewareContext(dto=test_dto)
    
    # Should fail - user doesn't have admin role
    with pytest.raises(AuthorizationError, match="Access denied.*admin_only"):
        middleware.pre_execute(context)


def test_authorization_middleware_role_success():
    """Test successful role-based authorization"""
    middleware = AuthorizationMiddleware()
    
    def user_provider(dto):
        return create_user_context(
            user_id=dto.user_id,
            roles=["user", "admin"]  # User has admin role
        )
    
    middleware.set_user_context_provider(user_provider)
    
    admin_policy = AuthorizationPolicy(
        name="admin_only",
        resource="secure_resource",
        action=PermissionAction.UPDATE,
        conditions=[has_role("admin")]
    )
    
    middleware.add_policy("SecureDTO", admin_policy)
    
    test_dto = SecureDTO(user_id="user123", organization_id="org1", action="update")
    context = MiddlewareContext(dto=test_dto)
    
    result_context = middleware.pre_execute(context)
    
    assert result_context == context
    assert context.get_metadata("authorization_passed") is True


def test_authorization_middleware_permission_based():
    """Test permission-based authorization"""
    middleware = AuthorizationMiddleware()
    
    def user_provider(dto):
        return create_user_context(
            user_id=dto.user_id,
            permissions=["read_documents", "write_documents"]
        )
    
    middleware.set_user_context_provider(user_provider)
    
    # Policy requiring delete permission
    delete_policy = AuthorizationPolicy(
        name="delete_permission",
        resource="document",
        action=PermissionAction.DELETE,
        conditions=[has_permission("delete_documents")]
    )
    
    middleware.add_policy("SecureDTO", delete_policy)
    
    test_dto = SecureDTO(user_id="user123", organization_id="org1", action="delete")
    context = MiddlewareContext(dto=test_dto)
    
    # Should fail - user doesn't have delete permission
    with pytest.raises(AuthorizationError, match="Access denied.*delete_permission"):
        middleware.pre_execute(context)


def test_authorization_middleware_resource_ownership():
    """Test resource ownership authorization"""
    middleware = AuthorizationMiddleware()
    
    def user_provider(dto):
        return create_user_context(user_id="user123")
    
    middleware.set_user_context_provider(user_provider)
    
    # Policy requiring resource ownership
    ownership_policy = AuthorizationPolicy(
        name="owns_resource",
        resource="user_data",
        action=PermissionAction.READ,
        conditions=[owns_resource()]
    )
    
    middleware.add_policy("SecureDTO", ownership_policy)
    
    # Test with different user ID (should fail)
    test_dto = SecureDTO(user_id="other_user", organization_id="org1", action="read")
    context = MiddlewareContext(dto=test_dto)
    
    with pytest.raises(AuthorizationError, match="Access denied.*owns_resource"):
        middleware.pre_execute(context)
    
    # Test with same user ID (should succeed)
    test_dto = SecureDTO(user_id="user123", organization_id="org1", action="read")
    context = MiddlewareContext(dto=test_dto)
    
    result_context = middleware.pre_execute(context)
    assert context.get_metadata("authorization_passed") is True


def test_authorization_middleware_organization_based():
    """Test organization-based authorization"""
    middleware = AuthorizationMiddleware()
    
    def user_provider(dto):
        return create_user_context(
            user_id=dto.user_id,
            organization_id="org1"
        )
    
    middleware.set_user_context_provider(user_provider)
    
    # Policy requiring same organization
    org_policy = AuthorizationPolicy(
        name="same_org",
        resource="org_data",
        action=PermissionAction.READ,
        conditions=[same_organization()]
    )
    
    middleware.add_policy("SecureDTO", org_policy)
    
    # Test with different organization (should fail)
    test_dto = SecureDTO(user_id="user123", organization_id="org2", action="read")
    context = MiddlewareContext(dto=test_dto)
    
    with pytest.raises(AuthorizationError, match="Access denied.*same_org"):
        middleware.pre_execute(context)
    
    # Test with same organization (should succeed)
    test_dto = SecureDTO(user_id="user123", organization_id="org1", action="read")
    context = MiddlewareContext(dto=test_dto)
    
    result_context = middleware.pre_execute(context)
    assert context.get_metadata("authorization_passed") is True


def test_authorization_middleware_attribute_based():
    """Test attribute-based authorization"""
    middleware = AuthorizationMiddleware()
    
    def user_provider(dto):
        return create_user_context(
            user_id=dto.user_id,
            attributes={"department": "finance", "level": "senior"}
        )
    
    middleware.set_user_context_provider(user_provider)
    
    # Policy requiring specific department
    attr_policy = AuthorizationPolicy(
        name="department_access",
        resource="financial_data",
        action=PermissionAction.READ,
        conditions=[attribute_matches("department", "finance")]
    )
    
    middleware.add_policy("SecureDTO", attr_policy)
    
    test_dto = SecureDTO(user_id="user123", organization_id="org1", action="read")
    context = MiddlewareContext(dto=test_dto)
    
    result_context = middleware.pre_execute(context)
    assert context.get_metadata("authorization_passed") is True


def test_authorization_middleware_multiple_conditions():
    """Test policy with multiple conditions (all must pass)"""
    middleware = AuthorizationMiddleware()
    
    def user_provider(dto):
        return create_user_context(
            user_id=dto.user_id,
            roles=["manager"],
            permissions=["read_reports"],
            organization_id="org1",
            attributes={"department": "finance"}
        )
    
    middleware.set_user_context_provider(user_provider)
    
    # Policy with multiple conditions
    complex_policy = AuthorizationPolicy(
        name="complex_access",
        resource="sensitive_reports",
        action=PermissionAction.READ,
        conditions=[
            has_role("manager"),
            has_permission("read_reports"),
            same_organization(),
            attribute_matches("department", "finance")
        ]
    )
    
    middleware.add_policy("SecureDTO", complex_policy)
    
    test_dto = SecureDTO(user_id="user123", organization_id="org1", action="read")
    context = MiddlewareContext(dto=test_dto)
    
    result_context = middleware.pre_execute(context)
    assert context.get_metadata("authorization_passed") is True


def test_authorization_middleware_multiple_policies():
    """Test multiple policies for same DTO type"""
    middleware = AuthorizationMiddleware()
    
    def user_provider(dto):
        return create_user_context(
            user_id=dto.user_id,
            roles=["user"],
            permissions=["read_data"]
        )
    
    middleware.set_user_context_provider(user_provider)
    
    # Add multiple policies
    policy1 = AuthorizationPolicy(
        name="role_check",
        resource="data",
        action=PermissionAction.READ,
        conditions=[has_role("user")]
    )
    
    policy2 = AuthorizationPolicy(
        name="permission_check",
        resource="data",
        action=PermissionAction.READ,
        conditions=[has_permission("read_data")]
    )
    
    middleware.add_policy("SecureDTO", policy1)
    middleware.add_policy("SecureDTO", policy2)
    
    test_dto = SecureDTO(user_id="user123", organization_id="org1", action="read")
    context = MiddlewareContext(dto=test_dto)
    
    result_context = middleware.pre_execute(context)
    assert context.get_metadata("authorization_passed") is True


def test_authorization_middleware_post_execute():
    """Test post_execute method (should pass through result)"""
    middleware = AuthorizationMiddleware()
    test_dto = SecureDTO(user_id="user123", organization_id="org1", action="read")
    context = MiddlewareContext(dto=test_dto)
    
    result = {"status": "success"}
    post_result = middleware.post_execute(context, result)
    
    assert post_result == result