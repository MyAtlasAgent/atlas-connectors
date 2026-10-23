"""The shared permission gate and bounded filesystem tools, independent of Atlas."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import weakref

from .permissions import PermissionPolicy

MAX_BYTES = 65536
TOOLS = {"read_file": "read_files", "write_file": "write_files", "list_files": "read_files"}
SENSITIVE = {".env", ".ssh", ".aws", ".grok", ".claude", ".codex", "auth.json", "keys.json", "credentials.json", ".netrc"}


class PermissionDenied(RuntimeError):
    pass


class UnsupportedCapability(PermissionDenied):
    pass


class ToolBroker:
    """Execute only concrete tools after the user's choice is resolved.

    Approvals come from a trusted application callback, never model output.
    File descriptors and O_NOFOLLOW contain traversal/symlink races on POSIX.
    Writes replace the directory entry atomically, avoiding hardlink truncation.
    """

    def __init__(self, workspace, policy: PermissionPolicy, approve=None, protected_paths=()):
        if os.name != "posix":
            raise UnsupportedCapability("Filesystem enforcement requires POSIX directory descriptors")
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be a directory")
        self.policy = policy
        self.approve = approve
        self.protected_paths = tuple(Path(p).resolve() for p in protected_paths) + (Path(__file__).resolve().parent,)
        self._root_fd = os.open(self.workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self._finalizer = weakref.finalize(self, os.close, self._root_fd)
        self.events = []
        self._seen = {}

    def _authorize(self, capability, request):
        choice = self.policy.preference(capability)
        if choice == "deny":
            raise PermissionDenied(f"User policy denies {capability}")
        if choice == "ask" and (self.approve is None or self.approve(capability, request) is not True):
            raise PermissionDenied(f"Explicit user approval required for {capability}")

    def _parts(self, value):
        if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
            raise PermissionDenied("Use a relative workspace path")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise PermissionDenied("Path escapes the workspace")
        candidate = self.workspace.joinpath(*path.parts)
        if any(candidate == protected or candidate.is_relative_to(protected) for protected in self.protected_paths):
            raise PermissionDenied("Connector policy, login and runtime files are protected")
        if ".git" in path.parts:
            raise UnsupportedCapability("Git metadata changes require a dedicated adapter; unavailable")
        sensitive = any(part in SENSITIVE or part.startswith(".env.")
                        or part.endswith((".pem", ".key")) for part in path.parts)
        if sensitive:
            self._authorize("read_credentials", {"path": value})
        return path.parts

    def _parent(self, parts):
        descriptor = os.dup(self._root_fd)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def call(self, request):
        if not isinstance(request, dict) or set(request) != {"id", "tool", "arguments"}:
            raise PermissionDenied("Invalid tool request")
        ident, tool, args = request["id"], request["tool"], request["arguments"]
        if not isinstance(ident, str) or not ident or len(ident) > 128:
            raise PermissionDenied("A bounded request id is required")
        if not isinstance(tool, str) or tool not in TOOLS or not isinstance(args, dict):
            self.events.append({"id": ident, "tool": "unsupported", "status": "unsupported",
                                "policy": self.policy.fingerprint})
            raise UnsupportedCapability("Unsupported tool; no command/network/authority fallback")
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        if ident in self._seen:
            previous, result = self._seen[ident]
            if previous != digest:
                raise PermissionDenied("Request id reused with different arguments")
            return dict(result)
        event = {"id": ident, "tool": tool, "policy": self.policy.fingerprint}
        try:
            expected = {"path", "text"} if tool == "write_file" else {"path"}
            if set(args) != expected:
                raise PermissionDenied("Unexpected tool arguments")
            self._authorize(TOOLS[tool], request)
            if tool == "list_files" and args["path"] == ".":
                parts = (".",)
            else:
                parts = self._parts(args["path"])
            parent = self._parent(parts)
            try:
                if tool == "read_file":
                    fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                    with os.fdopen(fd, "rb") as stream:
                        import stat
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            raise PermissionDenied("Only regular files can be read")
                        data = stream.read(MAX_BYTES + 1)
                    if len(data) > MAX_BYTES:
                        raise PermissionDenied("File exceeds read limit")
                    result = {"status": "executed", "text": data.decode("utf-8")}
                elif tool == "list_files":
                    fd = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        names = sorted(os.listdir(fd))
                    finally:
                        os.close(fd)
                    result = {"status": "executed", "names": [n for n in names[:1000] if n != ".git"]}
                else:
                    text = args["text"]
                    if not isinstance(text, str) or len(text.encode()) > MAX_BYTES:
                        raise PermissionDenied("Invalid text or write exceeds limit")
                    temporary = ".connector-write-" + secrets.token_hex(12)
                    try:
                        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=parent)
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(text.encode())
                            stream.flush()
                            os.fsync(stream.fileno())
                        # Replacing a symlink/hardlink changes the workspace entry, not its target.
                        os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
                    finally:
                        try:
                            os.unlink(temporary, dir_fd=parent)
                        except FileNotFoundError:
                            pass
                    result = {"status": "executed", "bytes": len(text.encode())}
            finally:
                os.close(parent)
            event["status"] = "executed"
        except (PermissionDenied, OSError, UnicodeError) as error:
            event["status"] = "denied"
            result = {"status": "denied", "reason": str(error) if isinstance(error, PermissionDenied) else type(error).__name__}
        self.events.append(event)
        self._seen[ident] = (digest, dict(result))
        return result
