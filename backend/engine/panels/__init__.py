"""
Dashboard panels that the WKO5 expression language cannot express:
per-climb scatters, grade-bin tables, load focus, recommendations.

Every module has pure functions over numbers / numpy arrays (unit tested on
synthetic data) plus a thin adapter that reads a Dataset. The registry in
`registry.py` turns a panel name into chart JSON with the same series shape
render.py produces, so the viewer draws both the same way.
"""
