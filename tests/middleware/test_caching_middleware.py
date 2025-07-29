import pytest
import time
from sincpro_framework.middleware.caching import (
    CachingMiddleware,
    CacheConfig,
    InMemoryCacheProvider
)
from sincpro_framework.middleware.base import MiddlewareContext
from sincpro_framework import DataTransferObject


class CacheTestDTO(DataTransferObject):
    """Test DTO for caching"""
    query_id: str
    user_id: str
    filters: dict = {}


def test_in_memory_cache_provider():
    """Test in-memory cache provider functionality"""
    cache = InMemoryCacheProvider()
    
    # Test set and get
    cache.set("test_key", {"data": "value"}, 1)
    result = cache.get("test_key")
    assert result == {"data": "value"}
    
    # Test expiration
    time.sleep(1.1)  # Wait for expiration
    result = cache.get("test_key")
    assert result is None
    
    # Test delete
    cache.set("test_key", {"data": "value"}, 60)
    cache.delete("test_key")
    result = cache.get("test_key")
    assert result is None


def test_in_memory_cache_provider_tags():
    """Test cache provider tag functionality"""
    cache = InMemoryCacheProvider()
    
    # Set cache entries with tags
    cache.set("key1", "value1", 60)
    cache.set("key2", "value2", 60)
    cache.add_tag("users", "key1")
    cache.add_tag("users", "key2")
    
    # Verify cache entries exist
    assert cache.get("key1") == "value1"
    assert cache.get("key2") == "value2"
    
    # Delete by tag
    cache.delete_by_tag("users")
    
    # Verify cache entries are gone
    assert cache.get("key1") is None
    assert cache.get("key2") is None


def test_caching_middleware_no_config():
    """Test caching middleware with no configuration"""
    cache_provider = InMemoryCacheProvider()
    middleware = CachingMiddleware(cache_provider)
    
    test_dto = CacheTestDTO(query_id="q1", user_id="user123")
    context = MiddlewareContext(dto=test_dto)
    
    result_context = middleware.pre_execute(context)
    
    assert result_context == context
    assert context.get_metadata("cache_key") is None
    assert context.get_metadata("cache_hit") is None


def test_caching_middleware_cache_miss():
    """Test caching middleware cache miss scenario"""
    cache_provider = InMemoryCacheProvider()
    middleware = CachingMiddleware(cache_provider)
    
    # Configure caching for DTO type
    config = CacheConfig(ttl_seconds=60)
    middleware.configure_caching("CacheTestDTO", config)
    
    test_dto = CacheTestDTO(query_id="q1", user_id="user123")
    context = MiddlewareContext(dto=test_dto)
    
    # Pre-execute (cache miss)
    result_context = middleware.pre_execute(context)
    
    assert context.get_metadata("cache_hit") is False
    assert context.get_metadata("cache_key") is not None
    assert context.get_metadata("cached_result") is None
    
    # Post-execute (cache the result)
    test_result = {"data": "query_result"}
    post_result = middleware.post_execute(context, test_result)
    
    assert post_result == test_result
    
    # Verify result was cached
    cache_key = context.get_metadata("cache_key")
    cached_value = cache_provider.get(cache_key)
    assert cached_value == test_result


def test_caching_middleware_cache_hit():
    """Test caching middleware cache hit scenario"""
    cache_provider = InMemoryCacheProvider()
    middleware = CachingMiddleware(cache_provider)
    
    # Configure caching
    config = CacheConfig(ttl_seconds=60)
    middleware.configure_caching("CacheTestDTO", config)
    
    test_dto = CacheTestDTO(query_id="q1", user_id="user123")
    
    # First execution (cache miss)
    context1 = MiddlewareContext(dto=test_dto)
    middleware.pre_execute(context1)
    original_result = {"data": "original_query_result"}
    middleware.post_execute(context1, original_result)
    
    # Second execution (cache hit)
    context2 = MiddlewareContext(dto=test_dto)
    middleware.pre_execute(context2)
    
    assert context2.get_metadata("cache_hit") is True
    assert context2.get_metadata("cached_result") == original_result
    
    # Post-execute should return cached result
    new_result = {"data": "new_query_result"}  # This should be ignored
    post_result = middleware.post_execute(context2, new_result)
    
    assert post_result == original_result  # Should return cached result


def test_caching_middleware_custom_key_generator():
    """Test caching middleware with custom key generator"""
    cache_provider = InMemoryCacheProvider()
    middleware = CachingMiddleware(cache_provider)
    
    def custom_key_generator(dto):
        return f"custom:{dto.user_id}:{dto.query_id}"
    
    config = CacheConfig(
        ttl_seconds=60,
        cache_key_generator=custom_key_generator
    )
    middleware.configure_caching("CacheTestDTO", config)
    
    test_dto = CacheTestDTO(query_id="q1", user_id="user123")
    context = MiddlewareContext(dto=test_dto)
    
    middleware.pre_execute(context)
    
    cache_key = context.get_metadata("cache_key")
    assert cache_key == "custom:user123:q1"


def test_caching_middleware_cache_condition():
    """Test caching middleware with cache condition"""
    cache_provider = InMemoryCacheProvider()
    middleware = CachingMiddleware(cache_provider)
    
    def cache_condition(context):
        # Only cache if user_id starts with "cache_"
        return context.dto.user_id.startswith("cache_")
    
    config = CacheConfig(
        ttl_seconds=60,
        cache_condition=cache_condition
    )
    middleware.configure_caching("CacheTestDTO", config)
    
    # Test with user that should NOT be cached
    test_dto1 = CacheTestDTO(query_id="q1", user_id="user123")
    context1 = MiddlewareContext(dto=test_dto1)
    
    middleware.pre_execute(context1)
    
    assert context1.get_metadata("cache_key") is None
    assert context1.get_metadata("cache_hit") is None
    
    # Test with user that SHOULD be cached
    test_dto2 = CacheTestDTO(query_id="q1", user_id="cache_user123")
    context2 = MiddlewareContext(dto=test_dto2)
    
    middleware.pre_execute(context2)
    
    assert context2.get_metadata("cache_key") is not None
    assert context2.get_metadata("cache_hit") is False


def test_caching_middleware_ttl_expiration():
    """Test that cached entries expire after TTL"""
    cache_provider = InMemoryCacheProvider()
    middleware = CachingMiddleware(cache_provider)
    
    # Configure with very short TTL
    config = CacheConfig(ttl_seconds=1)
    middleware.configure_caching("CacheTestDTO", config)
    
    test_dto = CacheTestDTO(query_id="q1", user_id="user123")
    
    # First execution (cache miss)
    context1 = MiddlewareContext(dto=test_dto)
    middleware.pre_execute(context1)
    original_result = {"data": "original_result"}
    middleware.post_execute(context1, original_result)
    
    # Immediate second execution (cache hit)
    context2 = MiddlewareContext(dto=test_dto)
    middleware.pre_execute(context2)
    assert context2.get_metadata("cache_hit") is True
    
    # Wait for expiration
    time.sleep(1.1)
    
    # Third execution (cache miss due to expiration)
    context3 = MiddlewareContext(dto=test_dto)
    middleware.pre_execute(context3)
    assert context3.get_metadata("cache_hit") is False


def test_caching_middleware_different_dtos():
    """Test that different DTOs get different cache keys"""
    cache_provider = InMemoryCacheProvider()
    middleware = CachingMiddleware(cache_provider)
    
    config = CacheConfig(ttl_seconds=60)
    middleware.configure_caching("CacheTestDTO", config)
    
    # Two different DTOs
    dto1 = CacheTestDTO(query_id="q1", user_id="user123")
    dto2 = CacheTestDTO(query_id="q2", user_id="user123")
    
    context1 = MiddlewareContext(dto=dto1)
    context2 = MiddlewareContext(dto=dto2)
    
    middleware.pre_execute(context1)
    middleware.pre_execute(context2)
    
    key1 = context1.get_metadata("cache_key")
    key2 = context2.get_metadata("cache_key")
    
    assert key1 != key2
    assert key1 is not None
    assert key2 is not None


def test_caching_middleware_invalidation_tags():
    """Test cache invalidation using tags"""
    cache_provider = InMemoryCacheProvider()
    middleware = CachingMiddleware(cache_provider)
    
    config = CacheConfig(
        ttl_seconds=60,
        invalidation_tags=["users", "queries"]
    )
    middleware.configure_caching("CacheTestDTO", config)
    
    test_dto = CacheTestDTO(query_id="q1", user_id="user123")
    context = MiddlewareContext(dto=test_dto)
    
    # Cache a result
    middleware.pre_execute(context)
    result = {"data": "cached_result"}
    middleware.post_execute(context, result)
    
    # Verify it's cached
    cache_key = context.get_metadata("cache_key")
    assert cache_provider.get(cache_key) == result
    
    # Invalidate by tag
    middleware.invalidate_cache("users")
    
    # Verify it's no longer cached
    assert cache_provider.get(cache_key) is None


def test_caching_middleware_generate_cache_key_fallback():
    """Test cache key generation fallback for non-serializable DTOs"""
    cache_provider = InMemoryCacheProvider()
    middleware = CachingMiddleware(cache_provider)
    
    class NonSerializableDTO:
        def __init__(self):
            self.data = object()  # Non-serializable object
        
        def __str__(self):
            return "NonSerializableDTO"
    
    config = CacheConfig()
    
    # Test fallback key generation
    dto = NonSerializableDTO()
    cache_key = middleware._generate_cache_key(dto, config)
    
    assert "NonSerializableDTO" in cache_key