"""Prepare a build-local Vivado project that references the firmware sources."""

import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def stage_project(source_project: Path, build_dir: Path) -> None:
    source_dir = source_project.parent
    build_project = build_dir / source_project.name
    source_roots = {
        "$PPRDIR": source_dir,
        "$PSRCDIR": source_project.with_suffix(".srcs"),
    }
    project = ET.parse(source_project)
    project.getroot().set("Path", str(build_project))

    for file_entry in project.iter("File"):
        root, _, relative_path = file_entry.attrib["Path"].partition("/")
        source = source_roots[root] / relative_path
        if not source.is_file():
            raise FileNotFoundError(f"Vivado project input not found: {source}")
        if source.suffix.lower() in {".xci", ".xcix"}:
            # Vivado can upgrade IP definitions in place and uses their paths to
            # locate its generated IP products.
            build_ip = build_dir / source.relative_to(source_dir)
            build_ip.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, build_ip)
        else:
            file_entry.set("Path", str(source))

    for entry in project.iter():
        if entry.get("Name") in {"ImportPath", "TargetConstrsFile"}:
            root, _, relative_path = entry.attrib["Val"].partition("/")
            if root in source_roots:
                entry.set("Val", str(source_roots[root] / relative_path))

    build_dir.mkdir(parents=True, exist_ok=True)
    project.write(build_project, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    source, destination = map(Path, sys.argv[1:])
    stage_project(source.resolve(), destination.resolve())
