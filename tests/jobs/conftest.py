"""Tests skip migrations, so seed the job categories the data migration adds."""

import importlib

import pytest


@pytest.fixture(autouse=True)
def job_categories(db):
    from django.apps import apps
    importlib.import_module('apps.jobs.migrations.0002_seed_categories').seed(apps, None)
