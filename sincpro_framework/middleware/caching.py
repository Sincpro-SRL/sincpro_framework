import json
import hashlib
from typing import Any, Optional, Dict, List, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from .base import BaseMiddleware, MiddlewareContext


@dataclass
class CacheConfig:
    """Configuration for caching behavior"""
    ttl_seconds: int = 300  # 5 minutes default
    cache_key_generator: Optional[Callable[[Any], str]] = None
    invalidation_tags: List[str] = None
    cache_condition: Optional[Callable[[MiddlewareContext], bool]] = None
    
    def __post_init__(self):
        if self.invalidation_tags is None:
            self.invalidation_tags = []


class CacheProvider:
    """Abstract cache provider interface"""
    
    def get(self, key: str) -> Optional[Any]:
        raise NotImplementedError
    
    def set(self, key: str, value: Any, ttl_seconds: int):
        raise NotImplementedError
    
    def delete(self, key: str):
        raise NotImplementedError
    
    def delete_by_tag(self, tag: str):
        raise NotImplementedError


class InMemoryCacheProvider(CacheProvider):
    """Simple in-memory cache implementation"""
    
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._tags: Dict[str, List[str]] = {}
    
    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            entry = self._cache[key]
            if datetime.now() < entry["expires"]:
                return entry["value"]
            else:
                self.delete(key)
        return None
    
    def set(self, key: str, value: Any, ttl_seconds: int):
        expires = datetime.now() + timedelta(seconds=ttl_seconds)
        self._cache[key] = {
            "value": value,
            "expires": expires,
            "created": datetime.now()
        }
    
    def delete(self, key: str):
        self._cache.pop(key, None)
    
    def delete_by_tag(self, tag: str):
        if tag in self._tags:
            for key in self._tags[tag]:
                self.delete(key)
            del self._tags[tag]
    
    def add_tag(self, tag: str, cache_key: str):
        """Add cache key to tag for invalidation"""
        if tag not in self._tags:
            self._tags[tag] = []
        if cache_key not in self._tags[tag]:
            self._tags[tag].append(cache_key)


class CachingMiddleware(BaseMiddleware):
    """Intelligent caching middleware"""
    
    def __init__(self, cache_provider: CacheProvider, name: str = "caching"):
        super().__init__(name, priority=30)  # After auth
        self.cache_provider = cache_provider
        self.cache_configs: Dict[str, CacheConfig] = {}
    
    def configure_caching(self, dto_type: str, config: CacheConfig):
        """Configure caching for specific DTO type"""
        self.cache_configs[dto_type] = config
    
    def pre_execute(self, context: MiddlewareContext) -> MiddlewareContext:
        """Check cache before execution"""
        dto_type_name = type(context.dto).__name__
        config = self.cache_configs.get(dto_type_name)
        
        if not config:
            return context
        
        # Check cache condition
        if config.cache_condition and not config.cache_condition(context):
            return context
        
        # Generate cache key
        cache_key = self._generate_cache_key(context.dto, config)
        context.add_metadata("cache_key", cache_key)
        
        # Try to get from cache
        cached_result = self.cache_provider.get(cache_key)
        if cached_result is not None:
            context.add_metadata("cache_hit", True)
            context.add_metadata("cached_result", cached_result)
        else:
            context.add_metadata("cache_hit", False)
        
        return context
    
    def post_execute(self, context: MiddlewareContext, result: Any) -> Any:
        """Cache result after execution"""
        if context.get_metadata("cache_hit"):
            return context.get_metadata("cached_result")
        
        cache_key = context.get_metadata("cache_key")
        if cache_key:
            dto_type_name = type(context.dto).__name__
            config = self.cache_configs.get(dto_type_name)
            
            if config:
                # Cache the result
                self.cache_provider.set(cache_key, result, config.ttl_seconds)
                
                # Add tags if configured
                if config.invalidation_tags and hasattr(self.cache_provider, 'add_tag'):
                    for tag in config.invalidation_tags:
                        self.cache_provider.add_tag(tag, cache_key)
        
        return result
    
    def _generate_cache_key(self, dto: Any, config: CacheConfig) -> str:
        """Generate cache key for DTO"""
        if config.cache_key_generator:
            return config.cache_key_generator(dto)
        
        # Default: hash of DTO serialization
        try:
            dto_dict = dto.model_dump() if hasattr(dto, 'model_dump') else dto.__dict__
            dto_json = json.dumps(dto_dict, sort_keys=True)
            hash_key = hashlib.md5(dto_json.encode()).hexdigest()
            return f"{type(dto).__name__}:{hash_key}"
        except Exception:
            # Fallback to string representation
            return f"{type(dto).__name__}:{str(dto)}"
    
    def invalidate_cache(self, tag: str):
        """Invalidate cache by tag"""
        self.cache_provider.delete_by_tag(tag)