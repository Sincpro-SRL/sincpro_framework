# Recipe: form hints in `presentation` — defaults for a client, never rules

Use it when a screen, a mobile app or an agent builds a form from `Meta` and needs to know which
fields are read-only, asked for, pre-filled, or shown only in some states.

## 1. Declare them on the entity (optional)

```python
from dataclasses import dataclass
from decimal import Decimal
from sincpro_framework.ddd import All, Condition, Entity, Presentation, When
from sincpro_framework.ddd.criteria import Operator

@dataclass
class Invoice(Entity):
    partner_id: str                                   # no default → required, by structure
    number: str = "/"                                 # default "/" published
    state: InvoiceState = InvoiceState.DRAFT
    payment: Payment = Payment.CASH
    card_reference: str = ""
    discount: Decimal = Decimal("0")
    discount_reason: str = ""

    presentation = Presentation["Invoice"](
        readonly=lambda i: [i.number, i.state],       # the domain writes them
        required=lambda i: i.partner_id,
        readonly_when=lambda i: When(
            i.state != InvoiceState.DRAFT, i.partner_id, i.discount
        ),
        required_when=lambda i: [
            When(i.payment == Payment.CARD, i.card_reference),
            When(i.state == InvoiceState.DRAFT and i.discount > Decimal("0"), i.discount_reason),
        ],
        visible_when=lambda i: When(i.payment == Payment.CARD, i.card_reference),
    )
```

Nothing declared still publishes: framework fields (`id`, `created_at`, `updated_at`,
`version`, mixin fields) are read-only, a field with no default that is not `Optional` is
required, and each plain dataclass default is the `default`.

## 2. What a client receives

The property `partner_id` of the entity's contract (`contract_of(Invoice)`, published in the
discovery document of every wire that exposes a read of it):

```json
{"type": "string", "x-sincpro": {"type": "many2one", "widget": "many2one",
 "readonly_when": {"field": "state", "operator": "!=", "value": "draft"}, "...": "..."}}
```

and `"partner_id"` is listed in the schema's `required`.

The client evaluates each condition over the record with the same evaluator it filters with
(`@sincpro/criteria`), reading each value as the field's type (`FieldMeta.type`, `exact` for
decimals: an amount arrives as the text `"0"`). A component prop wins over the hint:
`<Field name="partner_id" required />`, or hiding by the user's groups, is the client's call.

## 3. When the server must enforce a rule: a `@check` on the entity

The framework enforces no hint. A rule the server holds over the entity's own fields is a
`@check` — written once, warned while the form is drafted, refused on save
([rules.md](rules.md)):

```python
from sincpro_framework.ddd import check, exceptions

@dataclass
class Invoice(Entity):
    ...
    @check(lambda i: [i.card_reference, i.payment])
    def card_reference_when_card(self) -> None:
        if self.payment == Payment.CARD and not self.card_reference:
            raise exceptions.InvalidValue("Paying by card needs the card reference")
```

A rule that reads outside the record — the customer's credit, another aggregate — is a
`before_save` hook, which has the bus. A state lock ("a confirmed invoice cannot change its
partner") is the aggregate's own method refusing the change, or a hook comparing with the
stored version.

## Mistakes to avoid

- **Treating a hint as protection.** `readonly`/`required` greyed in a form do not stop the API.
  If it matters, write a `@check`.
- **Hiding a sensitive field only in the UI.** Hiding is not omitting: cut it on the server
  with the specification or an `after_read` hook.
- **Writing a condition the language cannot say.** `When(i.total * 2 > 10, ...)` is refused:
  write `When(i.state == State.DONE, ...)`, which is publishable data.
- **Asking a record with a specification that drops the fields a condition reads.** The client
  cannot evaluate `readonly_when` over `state` if the record arrived without `state`.
- **Adding role or tenant logic to `presentation`.** It is the same for every caller; groups and
  tenants are the client's or a hook's business.
