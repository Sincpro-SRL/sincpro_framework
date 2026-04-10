from sincpro_framework import DataTransferObject, Feature, UseFramework


class CommandA(DataTransferObject):
    value: str


class CommandB(DataTransferObject):
    value: str


framework = UseFramework("typing-list-dto")


@framework.feature([CommandA, CommandB])
class MultiCommandFeature(Feature):
    def execute(self, dto):
        return None
