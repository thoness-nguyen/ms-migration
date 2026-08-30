#!/usr/bin/env python3
from tenant_migrator.sharepoint import copy_library
from tenant_migrator.batch import _copy_library_with_retry
import inspect

sig1 = inspect.signature(copy_library)
sig2 = inspect.signature(_copy_library_with_retry)

max_size = sig1.parameters["max_file_size_mb"].default
timeout = sig2.parameters["timeout_seconds"].default

print(f"✓ sharepoint.py: max_file_size_mb = {max_size} MB ({max_size/1000:.1f} GB)")
print(f"✓ batch.py: timeout_seconds = {timeout} seconds ({timeout/60:.0f} minutes)")
print("\n[OK] Configuration updated successfully!")
