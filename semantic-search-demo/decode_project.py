import shutil
import sys
from pathlib import Path


def copy_project(source_path, output_path=None):
    """Copy everything from the source folder into a new folder."""

    source = Path(source_path).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"Project folder was not found: {source}")

    if output_path:
        output = Path(output_path).expanduser().resolve()
    else:
        output = source.parent / f"{source.name}-decoded"

    # The output must be outside the source or it would copy itself forever.
    if output == source or output.is_relative_to(source):
        raise ValueError("Output folder must be outside the source project")

    # Reuse the output folder when this script is run again.
    shutil.copytree(
        source,
        output,
        copy_function=shutil.copy2,
        symlinks=True,
        dirs_exist_ok=True,
    )
    return output


if __name__ == "__main__":
    # Default source is the project folder containing this file.
    source_folder = sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent
    output_folder = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        copied_project = copy_project(source_folder, output_folder)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    file_count = sum(
        1 for path in copied_project.rglob("*") if path.is_file()
    )
    print(f"Copied project: {copied_project}")
    print(f"Copied {file_count} files.")
