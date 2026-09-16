"""Explicit, request-local external inputs for validation before a write lock."""
import copy
import hashlib
import json
from pathlib import Path

from journal_io import JournalError, capture_file


class ValidationInputs:
    def __init__(self):
        self.values = {}
        self.frozen = False

    def capture(self, key, reader):
        if key not in self.values:
            if self.frozen:
                raise JournalError("validation dependency changed; capture inputs again: " + key)
            self.values[key] = copy.deepcopy(reader())
        return copy.deepcopy(self.values[key])

    def file(self, path):
        path = Path(path).absolute()
        def read():
            try:
                return capture_file(path)
            except FileNotFoundError:
                return None
        value = self.capture("file:" + str(path), read)
        if value is None:
            raise FileNotFoundError(path)
        return value

    def freeze(self):
        self.frozen = True

    def hashes(self):
        def serial(value):
            if isinstance(value, bytes):
                return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
            raise TypeError(type(value).__name__)
        return {key: hashlib.sha256(json.dumps(value, sort_keys=True, default=serial).encode()).hexdigest()
                for key, value in self.values.items()}


def captured(inputs, key, reader):
    return inputs.capture(key, reader) if inputs is not None else reader()


def read_input(path, inputs=None):
    return inputs.file(path) if inputs is not None else Path(path).read_bytes()
