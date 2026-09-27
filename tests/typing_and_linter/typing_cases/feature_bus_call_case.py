from typing import assert_type

from sincpro_framework import ApplicationService, DataTransferObject, UseFramework


class Command(DataTransferObject):
    identifier: str


class Response(DataTransferObject):
    total: int


framework = UseFramework("feature-bus-call")


@framework.app_service(Command)
class Orchestrate(ApplicationService):
    def execute(self, dto: Command) -> Response:
        answered = self.feature_bus(Command(identifier=dto.identifier), Response)
        assert_type(answered, Response)
        assert_type(self.feature_bus.execute(dto, Response), Response)
        return answered
