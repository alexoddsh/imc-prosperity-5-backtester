from __future__ import annotations

from abc import abstractmethod
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import ContextManager

RESOURCES_PACKAGE = "prosperity5bt.resources"
DATA_SET_PREFIX = "resources-"


def data_set_directory(data_set: int) -> str:
    """Returns the name of the directory containing the given data set's rounds."""
    return f"{DATA_SET_PREFIX}{data_set}"


def parse_data_set_directory(name: str) -> int | None:
    """Returns the data set a directory name belongs to, or None if it's not a data set directory."""
    if not name.startswith(DATA_SET_PREFIX):
        return None

    suffix = name[len(DATA_SET_PREFIX) :]
    if not suffix.isdigit():
        return None

    return int(suffix)


@contextmanager
def wrap_in_context_manager(value):
    yield value


class FileReader:
    @abstractmethod
    def file(self, path_parts: list[str]) -> ContextManager[Path | None]:
        """Given a path to a file, yields a single Path object to the file or None if the file does not exist."""
        raise NotImplementedError()

    @abstractmethod
    def data_sets(self) -> list[int]:
        """Returns the data sets that are available, sorted in ascending order."""
        raise NotImplementedError()


class FileSystemReader(FileReader):
    def __init__(self, root: Path) -> None:
        self._root = root

    def file(self, path_parts: list[str]) -> ContextManager[Path | None]:
        file = self._root
        for part in path_parts:
            file = file / part

        if not file.is_file():
            return wrap_in_context_manager(None)

        return wrap_in_context_manager(file)

    def data_sets(self) -> list[int]:
        if not self._root.is_dir():
            return []

        data_sets = []
        for child in self._root.iterdir():
            data_set = parse_data_set_directory(child.name)
            if data_set is not None and child.is_dir():
                data_sets.append(data_set)

        return sorted(data_sets)


class PackageResourcesReader(FileReader):
    def file(self, path_parts: list[str]) -> ContextManager[Path | None]:
        try:
            file = resources.files(RESOURCES_PACKAGE)
            for part in path_parts:
                file = file / part

            if not file.is_file():
                return wrap_in_context_manager(None)

            return resources.as_file(file)
        except Exception:
            return wrap_in_context_manager(None)

    def data_sets(self) -> list[int]:
        try:
            data_sets = []
            for child in resources.files(RESOURCES_PACKAGE).iterdir():
                data_set = parse_data_set_directory(child.name)
                if data_set is not None and child.is_dir():
                    data_sets.append(data_set)

            return sorted(data_sets)
        except Exception:
            return []
