from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PatentResourceRegistry:
    repo_root: Path
    abstract_db_path: Path
    chunk_db_path: Path
    archive_root: Path
    abstract_collection: str = "patent_abstracts"
    chunk_collection: str = "patent_chunks"

    @classmethod
    def discover(cls) -> "PatentResourceRegistry":
        repo_root = Path(__file__).resolve().parents[3]
        resource_root = repo_root / "resource" / "patentQA"
        archive_candidates = sorted(path for path in resource_root.iterdir() if path.is_dir() and path.name.startswith("__"))
        archive_root = archive_candidates[0] if archive_candidates else resource_root / "archive_missing"
        return cls(
            repo_root=repo_root,
            abstract_db_path=resource_root / "vector_db_patent_abstracts",
            chunk_db_path=resource_root / "vector_db_patent_chunks",
            archive_root=archive_root,
        )

    def vector_resources_available(self) -> bool:
        return self.abstract_db_path.joinpath("chroma.sqlite3").is_file() and self.chunk_db_path.joinpath("chroma.sqlite3").is_file()

    def archive_available(self) -> bool:
        return self.archive_root.is_dir()
