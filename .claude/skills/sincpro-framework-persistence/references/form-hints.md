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
            Condition.on(i.state, Operator.NE, InvoiceState.DRAFT), i.partner_id, i.discount
        ),
        required_when=lambda i: [
            When(Condition.on(i.payment, Operator.EQ, Payment.CARD), i.card_reference),
            When(All(all=[Condition.on(i.state, Operator.EQ, InvoiceState.DRAFT),
                          Condition.on(i.discount, Operator.GT, Decimal("0"))]), i.discount_reason),
        ],
        visible_when=lambda i: When(Condition.on(i.payment, Operator.EQ, Payment.CARD), i.card_reference),
    )
```

Nothing declared still publishes: framework fields (`id`, `created_at`, `updated_at`,
`version`, mixin fields) are read-only, a field with no default that is not `Optional` is
required, and each plain dataclass default is the `default`.

## 2. What a client receives

`Meta.fields["partner_id"]` (in every `entity_meta_data`, e.g. from `EntityReads`):

```json
{"readonly": false, "required": true, "default": null,
 "readonly_when": {"field": "state", "operator": "!=", "value": "draft"},
 "required_when": null, "visible_when": null, "...": "..."}
```

The client evaluates each condition over the record with the same evaluator it filters with
(`@sincpro/criteria`), reading each value as the field's type (`FieldMeta.type`, `exact` for
decimals: an amount arrives as the text `"0"`). A component prop wins over the hint:
`<Field name="partner_id" required />`, or hiding by the user's groups, is the client's call.

## 3. When the server must enforce a rule: the project's own hook

The framework enforces no hint. A rule the server enforces is a normal hook, ordered with
`after=`/`sequence=` like any other; read the published condition so the rule is written once:

```python
from sincpro_framework.ddd import exceptions, presentation_of
from sincpro_framework.ddd.criteria import Evaluation

@billing_hooks.on(Invoice)
class PaymentNeedsItsReference(BillingHook):
    def before_save(self, invoice: Invoice) -> None:
        for name, condition in presentation_of(Invoice).required_when.items():
            if Evaluation.matches(invoice, condition) and not getattr(invoice, name):
                raise exceptions.ConstraintViolation(f"{name} is required for this invoice")
```

A state lock ("a confirmed invoice cannot change its partner") is better written as the aggregate's
own method refusing the change, or a `before_save` hook comparing with the stored version.

## Mistakes to avoid

- **Treating a hint as protection.** `readonly`/`required` greyed in a form do not stop the API.
  If it matters, write the hook.
- **Hiding a sensitive field only in the UI.** Hiding is not omitting: cut it on the server
  with the specification or an `after_read` hook.
- **Writing a condition as a Python expression.** `When(i.state == "done", ...)` is refused:
  write `When(Condition.on(i.state, Operator.EQ, State.DONE), ...)`, which is publishable data.
- **Asking a record with a specification that drops the fields a condition reads.** The client
  cannot evaluate `readonly_when` over `state` if the record arrived without `state`.
- **Adding role or tenant logic to `presentation`.** It is the same for every caller; groups and
  tenants are the client's or a hook's business.
