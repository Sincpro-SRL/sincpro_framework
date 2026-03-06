"""Tests de integración Pydantic v2 para sincpro_framework.ddd.value_object."""

from typing import NewType

import pytest
from pydantic import BaseModel, ValidationError

from sincpro_framework.ddd.value_object import new_value_object
from tests.ddd.conftest import AmountVO, EmailVO, ProductCodeVO, StockVO, UserIdVO

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
        """Pydantic rechaza un string cuando el campo espera int."""

        class InvoiceDTO(BaseModel):
            invoice_id: UserIdVO

        with pytest.raises(ValidationError):
            InvoiceDTO(invoice_id="not-an-int")


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

        Counter = NewType("Counter", int)
        CounterVO = new_value_object(Counter, lambda v: (results.append(v), abs(v))[1])

        class MyDTO(BaseModel):
            value: CounterVO

        vo = CounterVO(-5)
        assert results == [-5]  # 1ra ejecución: abs(-5) = 5
        assert vo == 5

        MyDTO(value=vo)
        assert results == [-5, 5]  # 2da ejecución: Pydantic llama CounterVO(5)
