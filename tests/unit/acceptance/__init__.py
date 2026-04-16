"""Unit tests for the acceptance-test harness and scenario modules.

These tests guard the coverage matrix and the tool buckets against
silent drift between the harness CLI (``tests/acceptance/automated.py``),
the harness JSON server (``tests/acceptance/_harness_server.py``),
and the final-report builder (``tests/acceptance/_report.py``).
"""
