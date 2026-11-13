"""PV2-01 synthetic-only tabular foundation; not connected to Agent or API."""
from .models import Column, Dataset, FileSpec, ParseSettings
from .service import parse_file, preview
from .parser import ParseFailure

__all__ = ['Column', 'Dataset', 'FileSpec', 'ParseSettings', 'ParseFailure', 'parse_file', 'preview']
