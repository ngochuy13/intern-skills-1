"""
ssh_sdk.py - Cross-platform SSH SDK for Autonomous Intern (Lobster) devices.

Pure-Python (paramiko) so it works identically on Mac, Linux, and Windows
without the host needing the ssh / sshpass / scp binaries.

Public surface:
    LobsterSSH                       -- connection class, context-manager
        connect / close
        run(cmd, sudo=False, ...)    -> CommandResult
        put / get / put_dir / get_dir
        exists / read_text / write_text / listdir

    Module-level shortcuts (open + one op + close):
        run_once(host, user, cmd=..., ...)
        scp_to(host, user, local, remote, ...)
        scp_from(host, user, remote, local, ...)

Errors:
    SSHError, ConnectionError, CommandError, TransferError
"""

import io
import logging
import os
import posixpath
import stat
import time
from dataclasses import dataclass
from typing import List, Optional

try:
    import paramiko
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "ssh_sdk requires paramiko. Install with: pip install paramiko"
    ) from exc

log = logging.getLogger(__name__)


class SSHError(Exception):
    """Base class for SSH SDK errors."""


class ConnectionError(SSHError):  # noqa: A001 (intentionally shadows builtin)
    """Failed to establish or authenticate the SSH connection."""


class CommandError(SSHError):
    """A run() with check=True returned a non-zero exit code."""

    def __init__(self, cmd: str, result: "CommandResult"):
        super().__init__(
            f"command exited {result.exit_code}: {cmd}\n"
            f"stderr: {result.stderr.strip()[:500]}"
        )
        self.cmd = cmd
        self.result = result


class TransferError(SSHError):
    """SFTP put/get failure."""


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_s: float


class LobsterSSH:
    """
    SSH connection to a Lobster device.

    Examples:
        with LobsterSSH("172.168.20.145", "system", password="12345") as ssh:
            result = ssh.run("uname -a")
            print(result.stdout)
            ssh.put("local.py", "/tmp/remote.py")
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: Optional[str] = None,
        key_path: Optional[str] = None,
        port: int = 22,
        connect_timeout: float = 10.0,
    ):
        if password is None and key_path is None:
            raise ValueError("provide password or key_path")
        self.host = host
        self.user = user
        self.password = password
        self.key_path = key_path
        self.port = port
        self.connect_timeout = connect_timeout
        self._client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None

    # ------------------------------------------------------- lifecycle

    def connect(self) -> None:
        if self._client is not None:
            return
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.user,
            "timeout": self.connect_timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        # Auth precedence: try key first (if provided), then password.
        last_exc: Optional[Exception] = None
        if self.key_path:
            try:
                client.connect(key_filename=os.path.expanduser(self.key_path), **kwargs)
                self._client = client
                return
            except Exception as exc:  # paramiko raises a few different types
                last_exc = exc
                log.debug("key auth failed: %s", exc)
        if self.password:
            try:
                client.connect(password=self.password, **kwargs)
                self._client = client
                return
            except Exception as exc:
                last_exc = exc
        raise ConnectionError(f"could not connect to {self.user}@{self.host}: {last_exc}")

    def close(self) -> None:
        if self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def __enter__(self) -> "LobsterSSH":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _require_client(self) -> paramiko.SSHClient:
        if self._client is None:
            self.connect()
        assert self._client is not None
        return self._client

    def _require_sftp(self) -> paramiko.SFTPClient:
        client = self._require_client()
        if self._sftp is None:
            self._sftp = client.open_sftp()
        return self._sftp

    # ----------------------------------------------------------- exec

    def run(
        self,
        cmd: str,
        sudo: bool = False,
        timeout: Optional[float] = None,
        check: bool = False,
    ) -> CommandResult:
        """
        Run a command. Returns CommandResult. Set check=True to raise on non-zero.

        sudo=True wraps the command with `sudo -n` (passwordless). If the remote
        user does not have NOPASSWD sudo, the command exits with an error.
        """
        client = self._require_client()
        wrapped = f"sudo -n bash -c {_shquote(cmd)}" if sudo else cmd
        log.debug("ssh run: %s", wrapped)
        start = time.time()
        stdin, stdout, stderr = client.exec_command(wrapped, timeout=timeout)
        stdin.close()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        duration = time.time() - start
        result = CommandResult(stdout=out, stderr=err, exit_code=exit_code, duration_s=duration)
        if check and exit_code != 0:
            raise CommandError(cmd, result)
        return result

    # ----------------------------------------------------------- file ops

    def put(self, local: str, remote: str) -> None:
        sftp = self._require_sftp()
        try:
            self._mkdirs_for(remote)
            sftp.put(local, remote)
            log.info("uploaded %s -> %s:%s", local, self.host, remote)
        except Exception as exc:
            raise TransferError(f"put {local} -> {remote} failed: {exc}") from exc

    def get(self, remote: str, local: str) -> None:
        sftp = self._require_sftp()
        try:
            parent = os.path.dirname(local)
            if parent:
                os.makedirs(parent, exist_ok=True)
            sftp.get(remote, local)
            log.info("downloaded %s:%s -> %s", self.host, remote, local)
        except Exception as exc:
            raise TransferError(f"get {remote} -> {local} failed: {exc}") from exc

    def put_dir(self, local_dir: str, remote_dir: str) -> None:
        local_dir = os.path.abspath(local_dir)
        for root, _dirs, files in os.walk(local_dir):
            rel = os.path.relpath(root, local_dir)
            target_root = remote_dir if rel == "." else posixpath.join(remote_dir, rel.replace(os.sep, "/"))
            self._mkdir_p(target_root)
            for fname in files:
                self.put(os.path.join(root, fname), posixpath.join(target_root, fname))

    def get_dir(self, remote_dir: str, local_dir: str) -> None:
        sftp = self._require_sftp()
        os.makedirs(local_dir, exist_ok=True)
        for entry in sftp.listdir_attr(remote_dir):
            r = posixpath.join(remote_dir, entry.filename)
            l = os.path.join(local_dir, entry.filename)
            if stat.S_ISDIR(entry.st_mode or 0):
                self.get_dir(r, l)
            else:
                self.get(r, l)

    def exists(self, remote_path: str) -> bool:
        sftp = self._require_sftp()
        try:
            sftp.stat(remote_path)
            return True
        except IOError:
            return False

    def read_text(self, remote_path: str) -> str:
        sftp = self._require_sftp()
        with sftp.file(remote_path, "r") as f:
            return f.read().decode("utf-8", errors="replace")

    def write_text(self, remote_path: str, content: str) -> None:
        sftp = self._require_sftp()
        self._mkdirs_for(remote_path)
        with sftp.file(remote_path, "w") as f:
            f.write(content)

    def listdir(self, remote_dir: str) -> List[str]:
        sftp = self._require_sftp()
        return sftp.listdir(remote_dir)

    # --------------------------------------------------------- internals

    def _mkdirs_for(self, remote_path: str) -> None:
        parent = posixpath.dirname(remote_path)
        if parent and parent != "/":
            self._mkdir_p(parent)

    def _mkdir_p(self, remote_dir: str) -> None:
        sftp = self._require_sftp()
        parts: List[str] = []
        head = remote_dir
        while head and head != "/":
            parts.insert(0, head)
            head = posixpath.dirname(head)
        for p in parts:
            try:
                sftp.stat(p)
            except IOError:
                sftp.mkdir(p)


# ----------------------------------------------------- module-level shortcuts

def run_once(
    host: str,
    user: str,
    *,
    cmd: str,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
    sudo: bool = False,
    port: int = 22,
    timeout: Optional[float] = None,
) -> CommandResult:
    """Open a connection, run one command, close. Returns CommandResult."""
    with LobsterSSH(host, user, password=password, key_path=key_path, port=port) as ssh:
        return ssh.run(cmd, sudo=sudo, timeout=timeout)


def scp_to(
    host: str,
    user: str,
    local: str,
    remote: str,
    *,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
    port: int = 22,
) -> None:
    """Open a connection, upload one file, close."""
    with LobsterSSH(host, user, password=password, key_path=key_path, port=port) as ssh:
        ssh.put(local, remote)


def scp_from(
    host: str,
    user: str,
    remote: str,
    local: str,
    *,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
    port: int = 22,
) -> None:
    """Open a connection, download one file, close."""
    with LobsterSSH(host, user, password=password, key_path=key_path, port=port) as ssh:
        ssh.get(remote, local)


def _shquote(s: str) -> str:
    """Shell-quote a string for safe inclusion in `sudo -n bash -c <quoted>`."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


# --------------------------------------------------------------------- script

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Run a one-shot SSH command via ssh_sdk")
    parser.add_argument("host")
    parser.add_argument("user")
    parser.add_argument("--password", required=True)
    parser.add_argument("--cmd", required=True)
    parser.add_argument("--sudo", action="store_true")
    args = parser.parse_args()
    res = run_once(args.host, args.user, cmd=args.cmd, password=args.password, sudo=args.sudo)
    print(res.stdout, end="")
    if res.stderr:
        print(res.stderr, end="")
    raise SystemExit(res.exit_code)
