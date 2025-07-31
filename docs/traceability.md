# Traceability Feature

This document describes the new traceability feature added to the Sincpro Framework, which provides OpenTelemetry integration for distributed tracing and observability.

## Overview

The traceability feature allows you to instrument your framework operations with distributed tracing, enabling better observability and debugging capabilities. This integrates with OpenTelemetry standards and can send traces to external systems like Jaeger, and through Alloy to Grafana.

## Basic Usage

### Enable Observability

First, enable observability in your framework instance:

```python
from sincpro_framework import UseFramework

app = UseFramework("my-service")
app.enable_observability(
    service_name="my-service",
    jaeger_endpoint="http://localhost:14268/api/traces"  # Optional
)
```

### Traceability Decorators

You can now add traceability parameters to your feature and app service decorators:

```python
from sincpro_framework import Feature, ApplicationService, DataTransferObject

class MyCommand(DataTransferObject):
    data: str

class MyResponse(DataTransferObject):
    result: str

# Basic traceability - adds events and metadata to traces
@app.feature(MyCommand, traceability=True)
class MyFeature(Feature):
    def execute(self, dto: MyCommand) -> MyResponse:
        return MyResponse(result=f"Processed: {dto.data}")

# Full tracing with span creation
@app.feature(MyCommand, traceability=True, span=True)
class MyFullyTracedFeature(Feature):
    def execute(self, dto: MyCommand) -> MyResponse:
        return MyResponse(result=f"Fully traced: {dto.data}")
```

### Request Correlation

You can pass correlation IDs for request tracking:

```python
# Execute with correlation ID
result = app(
    MyCommand(data="test"),
    MyResponse,
    correlation_id="req-12345"
)

# Execute with trace context from external systems
result = app(
    MyCommand(data="test"),
    MyResponse,
    trace_context={"traceparent": "00-..."}
)
```

## Parameters

### `traceability=True`
- Adds basic traceability events and metadata to spans
- Records execution start/end events
- Captures execution duration and status
- Minimal performance overhead

### `span=True`
- Creates dedicated child spans for the operation
- Provides more detailed tracing granularity
- Useful for complex operations that need detailed breakdown
- Slightly higher overhead but better observability

### Both Parameters
- You can combine both `traceability=True` and `span=True` for maximum observability
- Recommended for critical business operations

## Integration with External Systems

### Jaeger
```python
app.enable_observability(
    service_name="my-service",
    jaeger_endpoint="http://localhost:14268/api/traces"
)
```

### Alloy + Grafana
The framework integrates with Alloy for trace collection and forwarding to Grafana:

1. Configure Alloy to collect traces from your service
2. Set up Grafana with Tempo for trace visualization
3. Use correlation IDs to track requests across services

## Backward Compatibility

The traceability feature is fully backward compatible:

```python
# This still works exactly as before
@app.feature(MyCommand)
class LegacyFeature(Feature):
    def execute(self, dto: MyCommand) -> MyResponse:
        return MyResponse(result="Legacy feature")
```

## Performance Considerations

- **Without observability**: Zero overhead - features work exactly as before
- **With traceability=True**: Minimal overhead (~1-2ms per operation)
- **With span=True**: Slightly higher overhead but better observability
- **Async span export**: Traces are exported asynchronously to avoid blocking

## Example Output

When observability is enabled, you'll see trace data like:

```json
{
    "name": "framework.execute.MyCommand",
    "context": {
        "trace_id": "0xce68a280a7e23b2850c1f18fff06a6cc",
        "span_id": "0x526f420e26aaed97"
    },
    "attributes": {
        "framework.version": "2.5.0",
        "dto.type": "MyCommand",
        "service.name": "my-service",
        "correlation_id": "req-12345",
        "execution.duration_ms": 1.234,
        "execution.status": "success"
    }
}
```

## Best Practices

1. **Enable observability early** in your application startup
2. **Use correlation IDs** for request tracking across services
3. **Apply traceability selectively** - focus on critical business operations
4. **Monitor performance impact** in production environments
5. **Configure appropriate sampling** for high-throughput services

For more examples, see `examples/traceability_poc.py`.