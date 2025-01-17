from sincpro_framework import ApplicationService as _ApplicationService
from sincpro_framework import DataTransferObject
from sincpro_framework import Feature as _Feature
from sincpro_framework import UseFramework as _UseFramework


# ----------------------------------------------------------------------
# Adapters or services to be injected
# ----------------------------------------------------------------------
class FakeClient:
    pass


class ProxyServiceFake:
    def __init__(self):
        self.client = FakeClient()

    def get_client(self, name: str):
        return self.client


proxy_service_instance = ProxyServiceFake()


# ----------------------------------------------------------------------
# Setup framework
# ----------------------------------------------------------------------
class DependencyContextType:
    proxy_services: ProxyServiceFake

    def any_client(self, client_name: str) -> FakeClient:
        """Helper function"""
        return self.proxy_services.get_client(client_name)


fake_bundle_context = _UseFramework("fake-bundle-context", log_after_execution=False)
fake_bundle_context.add_dependency("proxy_services", proxy_service_instance)


class Feature(_Feature, DependencyContextType):
    pass


class ApplicationService(_ApplicationService, DependencyContextType):
    pass


# ----------------------------------------------------------------------
# Use the framework previously setup
# ----------------------------------------------------------------------


#: Feature case
class CmdExecuteFeature(DataTransferObject):
    param_1: str
    param_2: int


class ResFeature(DataTransferObject):
    result: str
    client: FakeClient


@fake_bundle_context.feature(CmdExecuteFeature)
class ExecuteFeature(Feature):

    def execute(self, dto: CmdExecuteFeature, **kwargs) -> ResFeature | None:
        client = self.any_client("client_name")
        return ResFeature(
            result=f"Feature Result getting the Client: {client}", client=client
        )


#: Application Service case
class CmdExecuteAppService(DataTransferObject):
    app_param_1: str
    app_param_2: int


class ResAppService(DataTransferObject):
    app_result: str


@fake_bundle_context.app_service(CmdExecuteAppService)
class ExecuteAppService(ApplicationService):

    def execute(self, dto: CmdExecuteAppService, **kwargs) -> ResAppService | None:
        res = self.feature_bus.execute(
            CmdExecuteFeature(param_1=dto.app_param_1, param_2=dto.app_param_2), ResFeature
        )
        assert self.any_client("client_name") == res.client

        return ResAppService(app_result=f"App service result from {res.client}")


#: Start test


def test_use_framework():
    cmd = CmdExecuteAppService(app_param_1="param1", app_param_2=2)
    res = fake_bundle_context(cmd, ResAppService)
    assert res.app_result.startswith("App service result from")
