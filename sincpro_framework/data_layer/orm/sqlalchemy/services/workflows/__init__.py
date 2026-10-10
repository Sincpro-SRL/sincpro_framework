"""The workflows of the adapter — what orchestrates the atomic services, as an ApplicationService
orchestrates Features: the unit of work, the readings, the writes, over the `Store` they share.
A workflow imports atomic services, `domain` and `infrastructure`, never another workflow
(`Store` aside); an atomic service never imports a workflow (`tests/data_layer/orm/test_layers.py`).
"""
