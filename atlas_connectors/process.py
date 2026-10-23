"""Bounded process lifetime; never leave a timed-out provider running silently."""
import os
import signal
import subprocess


class ProcessError(RuntimeError):
    pass


def execute(argv, *, cwd, env, stdin="", timeout=120):
    if os.name != "posix":
        raise ProcessError("Live CLI transport currently requires macOS or Linux")
    with subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True) as child:
        try:
            stdout, stderr = child.communicate(stdin, timeout=timeout)
        except BaseException as error:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.communicate()
            if isinstance(error, subprocess.TimeoutExpired):
                raise ProcessError("Process timed out; its process group was killed; no retry") from None
            raise
        if len(stdout) + len(stderr) > 2_000_000:
            raise ProcessError("Process output exceeded the supported limit")
        return child.returncode, stdout, stderr
