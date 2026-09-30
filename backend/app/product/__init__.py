"""Product read models (D25).

Composite, read-only views the React product needs — a labelled catalog, the decision
workspace, semantic activity, and scenario explanations. Everything here is DERIVED from
platform records (datasets, contracts, bindings, runs, results, change events, ingestions,
baselines, scenarios); nothing is hard-coded per screen and nothing here writes.

The federation engine is untouched: these services only read what it recorded.
"""
