# 🏢 Application Services

Business logic and application layer services.

## 📋 Overview

This framework includes **1** application services
that handle business logic and orchestrate domain operations.

---

## 💼 Available Services

### MakeTransaction

**Module:** `dynamic.MakeTransaction`

Second layer of the framework, orchestration of features.

ApplicationServices coordinate multiple Features to accomplish complex business workflows.
They have access to all injected dependencies (same as Features) plus an exclusive
feature_bus for executing other Features.

ApplicationServices are ideal for:
- Non-atomic operations requiring multiple steps
- Coordinating between different Features
- Complex business workflows with multiple decision points
- Aggregating data from multiple sources

For better IDE support with typed dependencies, inherit with specific DTO types:

Example:
    @framework.app_service(MyOrchestrationDTO)
    class MyApplicationService(ApplicationService[MyOrchestrationDTO, MyResponseDTO]):
        # Type your injected dependencies for IDE autocomplete
        external_service: ExternalService

        def execute(self, dto: MyOrchestrationDTO) -> MyResponseDTO:
            # Access context with the new API
            correlation_id = self.context.get("correlation_id")
            user_id = self.context.get("user.id")

            # Execute Features through feature_bus with proper typing
            step1_result = self.feature_bus.execute(Step1DTO(...), Step1ResponseDTO)
            step2_result = self.feature_bus.execute(Step2DTO(...), Step2ResponseDTO)

            # Use injected dependencies for additional operations
            final_result = self.external_service.combine(step1_result, step2_result)
            return MyResponseDTO(result=final_result)

For backward compatibility, you can also use untyped ApplicationService:

    @framework.app_service(MyOrchestrationDTO)
    class MyApplicationService(ApplicationService):
        def execute(self, dto: MyOrchestrationDTO) -> MyResponseDTO:
            # This still works but with less IDE support
            return MyResponseDTO(result="example")

**Methods:**

#### execute

```python
(self, dto: MakeTransactionCommand) -> MakeTransactionResponse
```

Execute transaction processing
Args:
    dto (MakeTransactionCommand): Data transfer object containing transaction details
Returns:
    MakeTransactionResponse: Response containing transaction details

---
