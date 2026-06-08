from __future__ import annotations

from typing import Any

from app.modules.auth.repository import AuthRepository
from app.modules.departments.service import department_service as shared_department_service
from app.modules.personnel.repository import PersonnelRepository


class PersonnelDepartmentBackfillService:
    def __init__(
        self,
        *,
        repository: PersonnelRepository | Any | None = None,
        department_service: Any | None = None,
        users_repo: AuthRepository | Any | None = None,
    ) -> None:
        self._repository = repository or PersonnelRepository()
        self._departments = department_service or shared_department_service
        self._users = users_repo or AuthRepository()

    @staticmethod
    def _normalize_optional_int(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _is_complete_department_triplet(cls, candidate: dict[str, Any]) -> bool:
        if cls._normalize_optional_int(candidate.get("primary_department_id")) is None:
            return False
        if cls._normalize_optional_int(candidate.get("tertiary_department_id")) is not None:
            return cls._normalize_optional_int(candidate.get("secondary_department_id")) is not None
        return True

    def _build_department_preview(self, candidate: dict[str, Any]) -> dict[str, Any]:
        describe = getattr(self._departments, "describe_user_department", None)
        if not callable(describe):
            return {
                "primary_department_id": candidate.get("primary_department_id"),
                "secondary_department_id": candidate.get("secondary_department_id"),
                "tertiary_department_id": candidate.get("tertiary_department_id"),
            }
        payload = describe(
            primary_department_id=candidate.get("primary_department_id"),
            secondary_department_id=candidate.get("secondary_department_id"),
            tertiary_department_id=candidate.get("tertiary_department_id"),
        )
        return payload if isinstance(payload, dict) else {}

    def _collect_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        list_personnel = getattr(self._repository, "list_personnel_for_backfill", None)
        if not callable(list_personnel):
            return items
        for personnel in list_personnel() or []:
            personnel_id = int(personnel.get("id") or 0)
            candidates = self._repository.list_bound_department_candidates(personnel_id=personnel_id)
            complete_candidates = [candidate for candidate in candidates if self._is_complete_department_triplet(candidate)]
            if len(complete_candidates) == 1:
                candidate = complete_candidates[0]
                items.append(
                    {
                        "personnel_id": personnel_id,
                        "employee_no": personnel.get("employee_no"),
                        "full_name": personnel.get("full_name"),
                        "status": "synced",
                        "candidate_count": len(complete_candidates),
                        "department": self._build_department_preview(candidate),
                        "primary_department_id": candidate.get("primary_department_id"),
                        "secondary_department_id": candidate.get("secondary_department_id"),
                        "tertiary_department_id": candidate.get("tertiary_department_id"),
                    }
                )
                continue
            if not complete_candidates:
                items.append(
                    {
                        "personnel_id": personnel_id,
                        "employee_no": personnel.get("employee_no"),
                        "full_name": personnel.get("full_name"),
                        "status": "missing_department",
                        "candidate_count": 0,
                        "department": None,
                    }
                )
                continue
            items.append(
                {
                    "personnel_id": personnel_id,
                    "employee_no": personnel.get("employee_no"),
                    "full_name": personnel.get("full_name"),
                    "status": "conflicting_departments",
                    "candidate_count": len(complete_candidates),
                    "candidates": [self._build_department_preview(item) for item in complete_candidates],
                }
            )
        return items

    @staticmethod
    def _summary(items: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "total": len(items),
            "synced": sum(1 for item in items if item.get("status") == "synced"),
            "missing_department": sum(1 for item in items if item.get("status") == "missing_department"),
            "conflicting_departments": sum(1 for item in items if item.get("status") == "conflicting_departments"),
        }

    def preview(self) -> dict[str, Any]:
        items = self._collect_items()
        return {
            "success": True,
            "data": {
                "summary": self._summary(items),
                "items": items,
            },
        }

    def apply(self) -> dict[str, Any]:
        items = self._collect_items()
        backfill_personnel_department_and_sync_users = getattr(self._repository, "backfill_personnel_department_and_sync_users", None)
        update_personnel_department = getattr(self._repository, "update_personnel_department", None)
        sync_departments_for_personnel = getattr(self._users, "sync_departments_for_personnel", None)
        if callable(backfill_personnel_department_and_sync_users):
            for item in items:
                if item.get("status") != "synced":
                    continue
                backfill_personnel_department_and_sync_users(
                    personnel_id=int(item["personnel_id"]),
                    primary_department_id=item.get("primary_department_id"),
                    secondary_department_id=item.get("secondary_department_id"),
                    tertiary_department_id=item.get("tertiary_department_id"),
                )
        elif callable(update_personnel_department):
            for item in items:
                if item.get("status") != "synced":
                    continue
                update_personnel_department(
                    personnel_id=int(item["personnel_id"]),
                    primary_department_id=item.get("primary_department_id"),
                    secondary_department_id=item.get("secondary_department_id"),
                    tertiary_department_id=item.get("tertiary_department_id"),
                )
                if callable(sync_departments_for_personnel):
                    sync_departments_for_personnel(
                        personnel_id=int(item["personnel_id"]),
                        primary_department_id=item.get("primary_department_id"),
                        secondary_department_id=item.get("secondary_department_id"),
                        tertiary_department_id=item.get("tertiary_department_id"),
                    )
        return {
            "success": True,
            "data": {
                "summary": self._summary(items),
                "items": items,
            },
        }
