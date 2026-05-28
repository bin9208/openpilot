import base64
import os
import subprocess
import threading
from typing import Any, Callable
from urllib.parse import quote

from aiohttp import ClientSession, ClientTimeout
import requests
from requests.adapters import HTTPAdapter
from requests.auth import AuthBase, HTTPBasicAuth, HTTPDigestAuth

from openpilot.system.hardware import HARDWARE

from ...config import DASHCAM_DEFAULT_DISCORD_KEY, DASHCAM_DEFAULT_DISCORD_WEBHOOK


def param_text(params: Any, key: str, default: str = "unknown") -> str:
  try:
    if not params:
      return default
    value = params.get(key)
    if isinstance(value, bytes):
      value = value.decode("utf-8", errors="replace")
    value = str(value or "").strip()
    return value or default
  except Exception:
    return default


def repo_dir() -> str:
  return os.environ.get("CARROT_REPO_DIR", "/data/openpilot")


def git_text(args: list[str], default: str = "") -> str:
  try:
    result = subprocess.run(
      ["git", *args],
      cwd=repo_dir(),
      capture_output=True,
      text=True,
      timeout=4,
    )
    if result.returncode == 0:
      value = (result.stdout or "").strip()
      return value or default
  except Exception:
    pass
  return default


def device_serial(params: Any) -> str:
  for key in ("HardwareSerial", "DeviceSerial", "Serial", "CarrotSerial"):
    value = param_text(params, key, "")
    if value:
      return value
  for env_key in ("CARROT_DEVICE_SERIAL", "DEVICE_SERIAL", "SERIAL"):
    value = os.environ.get(env_key, "").strip()
    if value:
      return value
  try:
    getter = getattr(HARDWARE, "get_serial", None)
    if callable(getter):
      value = str(getter() or "").strip()
      if value:
        return value
  except Exception:
    pass
  return "unknown"


def upload_metadata(params: Any) -> dict[str, str]:
  return {
    "carName": param_text(params, "CarName", "none"),
    "dongleId": param_text(params, "DongleId", "unknown"),
    "serial": device_serial(params),
    "branch": git_text(["branch", "--show-current"], "unknown"),
    "commit": git_text(["rev-parse", "--short", "HEAD"], "unknown"),
    "commitDate": git_text(["show", "-s", "--date=format:%Y-%m-%d %H:%M:%S", "--format=%cd", "HEAD"], "unknown"),
  }


def decode_obfuscated(value: str, key: str) -> str:
  try:
    token = str(value or "").strip()
    key_bytes = str(key or "").encode("utf-8")
    if not token or not key_bytes:
      return ""
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    decoded = bytes(raw[i] ^ key_bytes[i % len(key_bytes)] for i in range(len(raw)))
    return decoded.decode("utf-8", errors="ignore").strip()
  except Exception:
    return ""


def discord_webhook_url(params: Any) -> str:
  for key in ("CARROT_DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_URL"):
    value = os.environ.get(key, "").strip()
    if value:
      return value
  for key in ("CarrotDiscordWebhookUrl", "CarrotDiscordWebhookURL", "DiscordWebhookUrl", "DiscordWebhookURL"):
    value = param_text(params, key, "")
    if value:
      return value
  if os.environ.get("CARROT_DISCORD_WEBHOOK_DISABLE", "").strip().lower() in {"1", "true", "yes", "on"}:
    return ""
  return decode_obfuscated(DASHCAM_DEFAULT_DISCORD_WEBHOOK, DASHCAM_DEFAULT_DISCORD_KEY)


def webdav_base_url() -> str:
  return (
    os.environ.get("CARROT_WEBDAV_URL")
    or os.environ.get("NAS_UPLOAD_URL")
    or "https://lunabox.myqnapcloud.com:5128/openpilot"
  ).strip().rstrip("/")


def webdav_credentials() -> tuple[str, str] | None:
  username = (
    os.environ.get("CARROT_WEBDAV_USERNAME")
    or os.environ.get("NAS_UPLOAD_USER")
    or "openpilot"
  ).strip()
  password = (
    os.environ.get("CARROT_WEBDAV_PASSWORD")
    or os.environ.get("NAS_UPLOAD_PASS")
    or "openpilot1200@"
  )
  return (username, password) if username else None


def webdav_upload_workers() -> int:
  value = (
    os.environ.get("CARROT_WEBDAV_UPLOAD_WORKERS")
    or os.environ.get("NAS_UPLOAD_WORKERS")
    or "3"
  ).strip()
  try:
    return max(1, min(8, int(value)))
  except (TypeError, ValueError):
    return 3


def webdav_verify_uploads() -> bool:
  value = (
    os.environ.get("CARROT_WEBDAV_VERIFY")
    or os.environ.get("NAS_UPLOAD_VERIFY")
    or "0"
  ).strip().lower()
  return value in {"1", "true", "yes", "on"}


def resolve_webdav_auth_kind(base_url: str, session: requests.Session | None = None) -> tuple[str, tuple[str, str] | None]:
  credentials = webdav_credentials()
  if not credentials:
    return ("none", None)

  requester = session.request if session else requests.request
  for kind, auth in (("basic", HTTPBasicAuth(*credentials)), ("digest", HTTPDigestAuth(*credentials))):
    try:
      resp = requester("OPTIONS", base_url, auth=auth, timeout=10, allow_redirects=False)
    except requests.RequestException:
      continue
    if resp.status_code != 401:
      return (kind, credentials)

  raise RuntimeError(f"WebDAV authentication failed [401]: {base_url}")


def webdav_auth_from_kind(kind: str, credentials: tuple[str, str] | None) -> AuthBase | None:
  if not credentials or kind == "none":
    return None
  if kind == "digest":
    return HTTPDigestAuth(*credentials)
  return HTTPBasicAuth(*credentials)


def resolve_webdav_auth(base_url: str) -> AuthBase | None:
  kind, credentials = resolve_webdav_auth_kind(base_url)
  return webdav_auth_from_kind(kind, credentials)


def webdav_url(base_url: str, *parts: str) -> str:
  quoted_parts = []
  for part in parts:
    for subpart in str(part or "").replace("\\", "/").split("/"):
      subpart = subpart.strip()
      if subpart:
        quoted_parts.append(quote(subpart, safe=""))
  if not quoted_parts:
    return base_url.rstrip("/")
  return f"{base_url.rstrip('/')}/{'/'.join(quoted_parts)}"


class WebDAVClient:
  def __init__(self, base_url: str | None = None, verify: bool | None = None, pool_maxsize: int | None = None):
    self.base_url = (base_url or webdav_base_url()).rstrip("/")
    self.verify = webdav_verify_uploads() if verify is None else bool(verify)
    self.pool_maxsize = max(1, int(pool_maxsize or webdav_upload_workers()))
    self.auth_kind, self.credentials = self._resolve_auth()
    self._local = threading.local()
    self._sessions: list[requests.Session] = []
    self._sessions_lock = threading.Lock()
    self._known_dirs: set[str] = set()
    self._known_dirs_lock = threading.Lock()

  def _resolve_auth(self) -> tuple[str, tuple[str, str] | None]:
    with requests.Session() as session:
      adapter = HTTPAdapter(pool_connections=self.pool_maxsize, pool_maxsize=self.pool_maxsize)
      session.mount("http://", adapter)
      session.mount("https://", adapter)
      return resolve_webdav_auth_kind(self.base_url, session)

  def _session_and_auth(self) -> tuple[requests.Session, AuthBase | None]:
    session = getattr(self._local, "session", None)
    auth = getattr(self._local, "auth", None)
    if session is None:
      session = requests.Session()
      adapter = HTTPAdapter(pool_connections=self.pool_maxsize, pool_maxsize=self.pool_maxsize)
      session.mount("http://", adapter)
      session.mount("https://", adapter)
      auth = webdav_auth_from_kind(self.auth_kind, self.credentials)
      self._local.session = session
      self._local.auth = auth
      with self._sessions_lock:
        self._sessions.append(session)
    return session, auth

  def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
    session, auth = self._session_and_auth()
    return session.request(method, url, auth=auth, **kwargs)

  def mkcol(self, url: str) -> None:
    resp = self.request("MKCOL", url, timeout=10)
    if resp.status_code in (200, 201, 204, 405):
      return
    raise RuntimeError(f"WebDAV MKCOL failed [{resp.status_code}]: {url}")

  def verify_file(self, url: str, expected_size: int) -> None:
    if not self.verify:
      return
    resp = self.request("HEAD", url, timeout=10)
    if 200 <= resp.status_code < 300:
      length = resp.headers.get("Content-Length")
      try:
        actual_size = int(length) if length is not None else expected_size
      except (TypeError, ValueError):
        actual_size = expected_size
      if actual_size == expected_size:
        return
      raise RuntimeError(f"WebDAV verify size mismatch [{actual_size} != {expected_size}]: {url}")

    if resp.status_code in (404, 410):
      raise RuntimeError(f"WebDAV verify missing file [{resp.status_code}]: {url}")

    # Some WebDAV servers do not implement HEAD. Fall back to PROPFIND depth 0.
    propfind = self.request("PROPFIND", url, headers={"Depth": "0"}, timeout=10)
    if propfind.status_code in (200, 207):
      return
    raise RuntimeError(f"WebDAV verify failed [{resp.status_code}/{propfind.status_code}]: {url}")

  def ensure_dir(self, remote_dir: str, check_cancel: Callable[[], None]) -> None:
    parts = [part for part in str(remote_dir or "").replace("\\", "/").split("/") if part]
    cur = []
    for part in parts:
      check_cancel()
      cur.append(part)
      path = "/".join(cur)
      with self._known_dirs_lock:
        if path in self._known_dirs:
          continue
        self.mkcol(webdav_url(self.base_url, *cur))
        self._known_dirs.add(path)

  def upload_file(self, local_path: str, remote_dir: str, filename: str) -> tuple[int, str]:
    file_size = os.path.getsize(local_path)
    url = webdav_url(self.base_url, remote_dir, filename)
    with open(local_path, "rb") as f:
      resp = self.request("PUT", url, data=f, timeout=120)
    if resp.status_code not in (200, 201, 204):
      raise RuntimeError(f"WebDAV upload failed [{resp.status_code}]: {url}")
    self.verify_file(url, file_size)
    return file_size, url

  def close(self) -> None:
    with self._sessions_lock:
      sessions = list(self._sessions)
      self._sessions.clear()
    for session in sessions:
      session.close()


def webdav_mkcol(url: str, auth: AuthBase | None) -> None:
  resp = requests.request("MKCOL", url, auth=auth, timeout=10)
  if resp.status_code in (200, 201, 204, 405):
    return
  raise RuntimeError(f"WebDAV MKCOL failed [{resp.status_code}]: {url}")


def verify_webdav_file(url: str, auth: AuthBase | None, expected_size: int) -> None:
  resp = requests.head(url, auth=auth, timeout=10)
  if 200 <= resp.status_code < 300:
    length = resp.headers.get("Content-Length")
    try:
      actual_size = int(length) if length is not None else expected_size
    except (TypeError, ValueError):
      actual_size = expected_size
    if actual_size == expected_size:
      return
    raise RuntimeError(f"WebDAV verify size mismatch [{actual_size} != {expected_size}]: {url}")

  if resp.status_code in (404, 410):
    raise RuntimeError(f"WebDAV verify missing file [{resp.status_code}]: {url}")

  # Some WebDAV servers do not implement HEAD. Fall back to PROPFIND depth 0.
  propfind = requests.request("PROPFIND", url, auth=auth, headers={"Depth": "0"}, timeout=10)
  if propfind.status_code in (200, 207):
    return
  raise RuntimeError(f"WebDAV verify failed [{resp.status_code}/{propfind.status_code}]: {url}")


def ensure_webdav_dir(base_url: str, remote_dir: str, auth: AuthBase | None, check_cancel: Callable[[], None]) -> None:
  parts = [part for part in str(remote_dir or "").replace("\\", "/").split("/") if part]
  cur = []
  for part in parts:
    check_cancel()
    cur.append(part)
    webdav_mkcol(webdav_url(base_url, *cur), auth)


def upload_message_lines(payload: dict[str, Any], max_results: int | None = None) -> list[str]:
  meta = payload.get("meta") or {}
  commit = str(meta.get("commit") or "").strip()
  commit_date = meta.get("commitDate") or "unknown"
  commit_text = (
    f"[{commit}](https://github.com/ajouatom/openpilot/commit/{commit})"
    if commit and commit != "unknown"
    else "unknown"
  )
  uploaded = [item for item in payload.get("results") or [] if item.get("ok")]
  failed = [item for item in payload.get("results") or [] if not item.get("ok")]
  lines = [
    "# Carrot Dashcam Upload",
    "### Upload",
    f"- Time: {payload.get('uploadedAt') or ''}",
    f"- Path: {payload.get('remoteBasePath') or ''}",
    "### Device",
    f"- Car name: {meta.get('carName') or 'none'}",
    f"- DongleId: {meta.get('dongleId') or 'unknown'}",
    f"- Serial: {meta.get('serial') or 'unknown'}",
    f"- Branch: {meta.get('branch') or 'unknown'}",
    f"- Commit: {commit_text} ({commit_date})",
    "### Result",
  ]

  result_items = uploaded + failed
  visible_items = result_items if max_results is None else result_items[:max_results]
  for item in visible_items:
    if item.get("ok"):
      lines.append(f"- {item.get('segment')} OK")
    else:
      error = str(item.get("error") or "").strip()
      suffix = f": {error}" if error else ""
      lines.append(f"- {item.get('segment')} FAILED{suffix}")

  hidden_count = len(result_items) - len(visible_items)
  if hidden_count > 0:
    lines.append(f"- ... +{hidden_count} more")
  if not result_items:
    lines.append("- none")
  return lines


def upload_share_text(payload: dict[str, Any]) -> str:
  return "\n".join(upload_message_lines(payload)).strip()


def discord_content(payload: dict[str, Any]) -> str:
  content = "\n".join(upload_message_lines(payload, max_results=24)).strip()
  if len(content) <= 1900:
    return content

  content = "\n".join(upload_message_lines(payload, max_results=10)).strip()
  if len(content) <= 1900:
    return content

  return "\n".join(upload_message_lines(payload, max_results=3)).strip()[:1900]


async def send_discord_webhook(url: str, payload: dict[str, Any]) -> dict[str, Any]:
  url = (url or "").strip()
  if not url:
    return {"configured": False, "ok": False, "skipped": True}
  if not url.startswith(("http://", "https://")):
    return {"configured": True, "ok": False, "error": "invalid webhook url"}
  body = {
    "username": "Carrot Dashcam",
    "content": discord_content(payload),
    "allowed_mentions": {"parse": []},
    "flags": 4,
  }
  try:
    timeout = ClientTimeout(total=12)
    async with ClientSession(timeout=timeout) as session:
      async with session.post(url, json=body) as resp:
        text = await resp.text()
        if 200 <= resp.status < 300:
          return {"configured": True, "ok": True, "status": resp.status}
        return {"configured": True, "ok": False, "status": resp.status, "error": text[:500]}
  except Exception as e:
    return {"configured": True, "ok": False, "error": str(e)}


def upload_folder_to_webdav(
  local_folder: str,
  directory: str,
  remote_path: str,
  should_cancel: Callable[[], bool] | None = None,
  client: WebDAVClient | None = None,
) -> bool:
  def check_cancel() -> None:
    if should_cancel and should_cancel():
      raise RuntimeError("upload canceled")

  own_client = client is None
  client = client or WebDAVClient()
  base_path = f"routes/{directory}/{remote_path}".strip("/").replace("\\", "/")

  try:
    check_cancel()
    client.ensure_dir(base_path, check_cancel)

    uploaded_count = 0
    uploaded_bytes = 0
    for root, _, files in os.walk(local_folder):
      check_cancel()
      rel_dir = os.path.relpath(root, local_folder)
      remote_dir = base_path if rel_dir == "." else f"{base_path}/{rel_dir.replace(os.sep, '/')}"
      client.ensure_dir(remote_dir, check_cancel)
      for filename in files:
        check_cancel()
        local_path = os.path.join(root, filename)
        if filename.endswith(".lock") or not os.path.isfile(local_path):
          continue
        file_size, _ = client.upload_file(local_path, remote_dir, filename)
        uploaded_count += 1
        uploaded_bytes += file_size
        check_cancel()

    if uploaded_count <= 0:
      raise RuntimeError(f"WebDAV upload found no files in {local_folder}")
    print(f"WebDAV upload complete: {uploaded_count} files, {uploaded_bytes} bytes -> {webdav_url(client.base_url, base_path)}")
    return True
  finally:
    if own_client:
      client.close()


def upload_folder_to_ftp(
  local_folder: str,
  directory: str,
  remote_path: str,
  should_cancel: Callable[[], bool] | None = None,
) -> bool:
  return upload_folder_to_webdav(local_folder, directory, remote_path, should_cancel)
