# utils/diff_parser.py

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DiffHunk:
    """
    Represents a single diff hunk from a Git patch.
    Minimal representation for Phase 0.
    """
    file_path: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    content: List[str]        # List of raw hunk lines (“+…”, “-…”, “ …”)


@dataclass
class FileDiff:
    """
    Represents all diff hunks for a given file.
    """
    file_path: str
    hunks: List[DiffHunk]


class DiffParser:
    """
    Minimal diff parser for Phase 0.
    Only supports unified diffs (GitHub / git diff format).
    """

    def parse(self, diff_text: str) -> List[FileDiff]:
        """
        Parse unified diff text and return a list of FileDiff objects.
        """
        files: List[FileDiff] = []
        current_file: Optional[FileDiff] = None
        current_hunk: Optional[DiffHunk] = None
        hunk_lines: List[str] = []

        for line in diff_text.splitlines():
            # Detect start of a file section
            if line.startswith("+++ "):
                # New file path line, but actual path parsing relies on the "---" earlier
                continue

            if line.startswith("--- "):
                # Finish previous file
                if current_file:
                    files.append(current_file)

                file_path = line.replace("--- ", "").strip()
                current_file = FileDiff(file_path=file_path, hunks=[])
                current_hunk = None
                hunk_lines = []
                continue

            # Detect hunk header: @@ -old,+new @@
            if line.startswith("@@"):
                # Finish previous hunk
                if current_hunk:
                    current_hunk.content = hunk_lines
                    current_file.hunks.append(current_hunk)

                # Parse the header
                # Example: @@ -12,7 +12,8 @@
                header = line.strip("@@ ").split(" ")
                old_range = header[0].lstrip("-")
                new_range = header[1].lstrip("+")

                old_start, old_len = [int(x) for x in old_range.split(",")]
                new_start, new_len = [int(x) for x in new_range.split(",")]

                current_hunk = DiffHunk(
                    file_path=current_file.file_path,
                    old_start=old_start,
                    old_lines=old_len,
                    new_start=new_start,
                    new_lines=new_len,
                    content=[],
                )
                hunk_lines = []
                continue

            # Inside hunk (added/removed/context lines)
            if current_hunk is not None:
                hunk_lines.append(line)

        # Close last hunk + file
        if current_hunk and current_file:
            current_hunk.content = hunk_lines
            current_file.hunks.append(current_hunk)

        if current_file:
            files.append(current_file)

        return files
