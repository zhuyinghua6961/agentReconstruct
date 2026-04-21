#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SERVICE_ROOT = REPO_ROOT / "public-service" / "backend"
if str(PUBLIC_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(PUBLIC_SERVICE_ROOT))

from app.modules.personnel.backfill_service import PersonnelDepartmentBackfillService  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview or apply personnel department backfill")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Only preview the backfill summary")
    mode.add_argument("--apply", action="store_true", help="Apply backfill for auto-resolvable personnel")
    return parser.parse_args(argv)


def _exit_code(summary: dict[str, object]) -> int:
    missing = int(summary.get("missing_department") or 0)
    conflicting = int(summary.get("conflicting_departments") or 0)
    return 1 if (missing or conflicting) else 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    service = PersonnelDepartmentBackfillService()
    result = service.apply() if args.apply else service.preview()
    if not result.get("success"):
        print(
            json.dumps(
                {
                    "success": False,
                    "error": result.get("error"),
                    "code": result.get("code"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    summary = result.get("data") if isinstance(result.get("data"), dict) else {}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return _exit_code(summary.get("summary") if isinstance(summary.get("summary"), dict) else {})


if __name__ == "__main__":
    raise SystemExit(main())
