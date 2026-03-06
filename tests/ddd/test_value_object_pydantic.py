"""Tests de integración Pydantic v2 para sincpro_framework.ddd.value_object."""

from typing import NewType

import pytest
from pydantic import BaseModel, ValidationError

from sincpro_framework.ddd import ValueObject
from sincpro_framework.ddd.value_object import new_value_object
from tests.ddd.conftest import AmountVO, EmailVO, ProductCodeVO, StockVO, TagVO, UserIdVO

# ---------------------------------------------------------------------------
# Pydantic integration — value objects as model fields (scalars)
# ---------------------------------------------------------------------------


class TestPydanticIntegration:
    """Value objects como tipo nativo de campo Pydantic v2 — sin AfterValidator."""

    def test_int_vo_abs_executes(self):
        """UserIdVO: validate_fn abs() debe ejecutarse al asignar el campo."""

        class OrderDTO(BaseModel):
            order_id: UserIdVO

        order = OrderDTO(order_id=-42)
        assert order.order_id == 42

    def test_str_vo_strip_and_lowercase_executes(self):
        """EmailVO: validate_fn strip+lower debe ejecutarse al asignar el campo."""

        class UserDTO(BaseModel):
            email: EmailVO

        user = UserDTO(email="  HELLO@WORLD.COM  ")
        assert user.email == "hello@world.com"

    def test_float_vo_round_executes(self):
        """AmountVO: validate_fn round(v, 2) debe ejecutarse al asignar el campo."""

        class PaymentDTO(BaseModel):
            amount: AmountVO

        payment = PaymentDTO(amount=3.14159)
        assert payment.amount == 3.14

    def test_multiple_vo_fields(self):
        """Múltiples campos VO en un mismo modelo; cada validate_fn se ejecuta."""

        class ProductDTO(BaseModel):
            code: ProductCodeVO
            stock: StockVO

        product = ProductDTO(code=" abc ", stock=50)
        assert product.code == "ABC"
        assert product.stock == 100  # 50 * 2

    def test_model_dump_returns_transformed_value(self):
        """model_dump() devuelve el valor ya transformado por validate_fn."""

        class ItemDTO(BaseModel):
            item_id: UserIdVO

        item = ItemDTO(item_id=-7)  # abs(-7) = 7
        assert item.model_dump() == {"item_id": 7}

    def test_rejects_incompatible_scalar(self):
        """Pydantic rejects a string when the field expects int."""

        class InvoiceDTO(BaseModel):
            invoice_id: UserIdVO

        with pytest.raises(ValidationError):
            InvoiceDTO(invoice_id="not-an-int")


# ---------------------------------------------------------------------------
# Pydantic: validation failure cases
# ---------------------------------------------------------------------------


class TestPydanticValidationErrors:
    """Pydantic surfaces validate_fn errors and type mismatches as ValidationError."""

    def test_wrong_type_str_for_int_field(self):
        """Passing a string to an int-based VO field raises ValidationError."""

        class OrderDTO(BaseModel):
            order_id: UserIdVO

        with pytest.raises(ValidationError):
            OrderDTO(order_id="abc")

    def test_wrong_type_int_for_str_field(self):
        """Passing an int to a str-based VO field raises ValidationError."""

        class UserDTO(BaseModel):
            email: EmailVO

        with pytest.raises(ValidationError):
            UserDTO(email=123)

    def test_wrong_type_str_for_float_field(self):
        """Passing a non-numeric string to a float-based VO field raises ValidationError."""

        class PaymentDTO(BaseModel):
            amount: AmountVO

        with pytest.raises(ValidationError):
            PaymentDTO(amount="not-a-number")

    def test_missing_required_field(self):
        """Omitting a required VO field raises ValidationError."""

        class OrderDTO(BaseModel):
            order_id: UserIdVO

        with pytest.raises(ValidationError):
            OrderDTO()

    def test_none_for_required_vo_field(self):
        """Passing None to a required VO field raises ValidationError."""

        class OrderDTO(BaseModel):
            order_id: UserIdVO

        with pytest.raises(ValidationError):
            OrderDTO(order_id=None)

    def test_validate_fn_raises_value_error_surfaces_as_validation_error(self):
        """A ValueError raised inside validate_fn is surfaced as a Pydantic ValidationError."""

        class TagDTO(BaseModel):
            tag: TagVO

        with pytest.raises(ValidationError):
            TagDTO(tag="   ")  # all whitespace → _validate_non_empty raises ValueError

    def test_validate_fn_raising_explicitly(self):
        """A ValueError raised inside a custom validate_fn is wrapped by Pydantic."""
        StrictPositive = ValueObject(
            int,
            lambda v: (_ for _ in ()).throw(ValueError("must be positive")) if v <= 0 else v,
            name="StrictPositive",
        )

        class DTO(BaseModel):
            value: StrictPositive

        with pytest.raises(ValidationError):
            DTO(value=-1)

    def test_multiple_invalid_fields_all_reported(self):
        """Pydantic collects all field errors, not just the first one."""

        class ProductDTO(BaseModel):
            order_id: UserIdVO
            email: EmailVO

        with pytest.raises(ValidationError) as exc_info:
            ProductDTO(order_id="bad", email=999)

        assert exc_info.value.error_count() == 2


# ---------------------------------------------------------------------------
# Pydantic: constructor vs instancia pre-construida
# ---------------------------------------------------------------------------


class TestPydanticConstructorVsInstance:
    """
    Enfoque 1: pasar el valor crudo → Pydantic construye el VO internamente.
    Enfoque 2: instanciar el VO antes → pasar la instancia ya transformada.
    """

    def test_enfoque1_valor_crudo_pydantic_llama_vo(self):
        """Enfoque 1: Pydantic recibe el escalar crudo y llama al constructor del VO."""

        class UserDTO(BaseModel):
            email: EmailVO

        dto = UserDTO(email="  RAW@EXAMPLE.COM  ")
        assert dto.email == "raw@example.com"

    def test_enfoque2_instancia_preconstruida(self):
        """Enfoque 2: el VO se construye antes; Pydantic recibe la instancia ya transformada."""

        class UserDTO(BaseModel):
            email: EmailVO

        email_vo = EmailVO("  PRE@EXAMPLE.COM  ")
        assert email_vo == "pre@example.com"  # ya transformado al construir

        dto = UserDTO(email=email_vo)
        assert dto.email == "pre@example.com"

    def test_ambos_enfoques_mismo_resultado(self):
        """Enfoque 1 y 2 producen el mismo valor final en el campo."""

        class PaymentDTO(BaseModel):
            amount: AmountVO

        raw_value = 3.14159

        dto1 = PaymentDTO(amount=raw_value)  # enfoque 1: crudo
        dto2 = PaymentDTO(amount=AmountVO(raw_value))  # enfoque 2: pre-construido

        assert dto1.amount == dto2.amount == 3.14

    def test_enfoque2_validate_fn_se_ejecuta_dos_veces(self):
        """Con enfoque 2, validate_fn corre al construir el VO y una 2da vez cuando
        Pydantic pasa el valor por el constructor al asignarlo al campo."""

        results = []

        CounterVO = ValueObject(int, lambda v: (results.append(v), abs(v))[1], name="Counter")

        class MyDTO(BaseModel):
            value: CounterVO

        vo = CounterVO(-5)
        assert results == [-5]  # 1ra ejecución: abs(-5) = 5
        assert vo == 5

        MyDTO(value=vo)
        assert results == [-5, 5]  # 2da ejecución: Pydantic llama CounterVO(5)


# ---------------------------------------------------------------------------
# Compatibilidad: ambas APIs funcionan igual en Pydantic
# ---------------------------------------------------------------------------


class TestBothAPIsInPydantic:
    def test_new_api_value_object_with_pydantic(self):
        """API nueva: ValueObject(int, fn, name='X') funciona como campo Pydantic."""
        Score = ValueObject(int, lambda v: max(0, v), name="Score")

        class Model(BaseModel):
            score: Score

        m = Model(score=-10)
        assert m.score == 0
        assert isinstance(m.score, int)

    def test_old_api_new_value_object_with_newtype_pydantic(self):
        """API vieja: new_value_object(NewType('X', int), fn) funciona como campo Pydantic."""
        Rating = new_value_object(NewType("Rating", int), lambda v: max(1, min(5, v)))

        class Model(BaseModel):
            rating: Rating

        assert Model(rating=10).rating == 5  # clamp a 5
        assert Model(rating=0).rating == 1  # clamp a 1
        assert Model(rating=3).rating == 3

    def test_both_apis_equivalent_in_pydantic(self):
        """Ambas APIs producen VOs intercambiables dentro de Pydantic."""
        NewApiEmail = ValueObject(str, lambda v: v.strip().lower(), name="Email")
        OldApiEmail = new_value_object(NewType("Email", str), lambda v: v.strip().lower())

        class NewModel(BaseModel):
            email: NewApiEmail

        class OldModel(BaseModel):
            email: OldApiEmail

        assert NewModel(email="  HI@X.COM  ").email == "hi@x.com"
        assert OldModel(email="  HI@X.COM  ").email == "hi@x.com"
