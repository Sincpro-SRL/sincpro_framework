"""`TokenSourceContract`: what every `TokenSource` must do — a project's own source proves it by
inheriting this class.

    class TestBankToken(TokenSourceContract):
        def make_source(self) -> TokenSource:
            return BankToken(...)
        def secret_values(self) -> list[str]:
            return ["the-client-secret"]
"""

from sincpro_framework.auth import OutgoingRequest, TokenSource
from sincpro_framework.exceptions import ServiceUnavailableError


class TokenSourceContract:
    """Inherit, answer `make_source` and `secret_values`, and run with pytest."""

    def make_source(self) -> TokenSource:
        """Answer the source under test."""
        raise NotImplementedError("answer the TokenSource under test")

    def secret_values(self) -> list[str]:
        """Answer the secrets the source holds — none may ever show."""
        return []

    def request(self) -> OutgoingRequest:
        """Answer a request the source gives headers for."""
        return OutgoingRequest(
            url="https://api.example/resource", method="POST", tenant="t-1"
        )

    def make_source_whose_endpoint_is_down(self) -> TokenSource | None:
        """The source with what it asks not answering — `None` skips that check."""
        return None

    def test_a_request_gets_headers(self) -> None:
        """The source answers headers for a request — at least one."""
        assert self.make_source().headers_for(self.request())

    def test_no_secret_is_ever_shown(self) -> None:
        """`repr`, `str` and `describe` never hold a secret the source keeps."""
        source = self.make_source()
        said = f"{source!r} {source!s} {source.describe()}"
        for secret in self.secret_values():
            assert secret not in said, f"{type(source).__name__} shows a secret"

    def test_invalidating_answers_a_bool_and_never_raises(self) -> None:
        """Told its headers were refused, a source answers whether to try again."""
        source = self.make_source()
        headers = source.headers_for(self.request())
        assert isinstance(source.invalidate(self.request(), headers, {}), bool)

    def test_what_it_asks_being_down_is_unavailable(self) -> None:
        """A token endpoint that does not answer is `ServiceUnavailableError` — never a refusal
        of the caller."""
        source = self.make_source_whose_endpoint_is_down()
        if source is None:
            return
        try:
            source.headers_for(self.request())
        except ServiceUnavailableError:
            return
        raise AssertionError("a token endpoint down was not told as unavailable")
