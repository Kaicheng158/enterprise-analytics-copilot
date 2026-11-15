"""Manual, bounded PV2-02b maintenance entrypoint; disabled unless explicitly enabled."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.datasets import (
    DatasetLifecycleSettings,
    PersistenceFailure,
    PrivateDatasetLifecycleService,
    PrivateLocalBlobStore,
)
from scripts.migrate import connect_kwargs


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one bounded trusted-local Dataset maintenance pass")
    parser.add_argument("--trusted-local-synthetic", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--stale-minutes", type=int, default=30)
    args = parser.parse_args()
    if not args.trusted_local_synthetic or os.environ.get("PRIVATE_DATASET_LIFECYCLE_ENABLED") != "true":
        print("lifecycle_disabled", file=sys.stderr)
        return 2
    root_value = os.environ.get("PRIVATE_DATASET_STORAGE_ROOT")
    if not root_value or not Path(root_value).is_absolute():
        print("invalid_storage_root", file=sys.stderr)
        return 2

    def connections():
        return psycopg.connect(**connect_kwargs(), autocommit=True)

    try:
        service = PrivateDatasetLifecycleService(
            connections,
            PrivateLocalBlobStore(Path(root_value)),
            settings=DatasetLifecycleSettings(enabled=True),
        )
        now = datetime.now(timezone.utc)
        result = service.run_maintenance(
            now=now,
            stale_before=now - timedelta(minutes=args.stale_minutes),
            limit=args.limit,
            trusted_local=True,
        )
        print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
        return 0
    except PersistenceFailure as exc:
        print(exc.code, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
