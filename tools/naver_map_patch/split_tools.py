from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

from tools.naver_map_patch.split_package import (
  AndroidTools,
  ApkIdentity,
  ArchivePatch,
  CommandResult,
  ErrorCode,
  PackagingError,
  Sha256,
  SigningConfig,
)


_DEX_ENTRY = re.compile(r"classes(?:[2-9]|[1-9][0-9]+)?\.dex\Z")
_SIGNATURE_ENTRY = re.compile(r"META-INF/(?:MANIFEST\.MF|[^/]+\.(?:SF|RSA|DSA|EC))\Z", re.IGNORECASE)
_CERTIFICATE_DIGEST = re.compile(r"^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F:]+)$", re.MULTILINE)
_KEYSTORE_ENTRY_COUNT = re.compile(r"^Your keystore contains ([0-9]{1,9}) entr(?:y|ies)$")
_KEYSTORE_ALIAS = re.compile(r"^Alias name: (.+)$")
_KEYSTORE_ENTRY_TYPE = re.compile(r"^Entry type: (.+)$")


def run_checked(command: Sequence[str], environment: Mapping[str, str], timeout_seconds: float) -> CommandResult:
  executable = Path(command[0]).name
  try:
    process = subprocess.run(
      command,
      capture_output=True,
      text=True,
      encoding="utf-8",
      errors="replace",
      timeout=timeout_seconds,
      check=False,
      env=environment,
    )
  except subprocess.TimeoutExpired as error:
    raise PackagingError(ErrorCode.TOOL_TIMEOUT, f"{executable} exceeded {timeout_seconds:g}s") from error
  except OSError as error:
    raise PackagingError(ErrorCode.TOOL_FAILURE, f"{executable} could not start: {error.strerror or error.__class__.__name__}") from error
  if process.returncode != 0:
    raise PackagingError(ErrorCode.TOOL_FAILURE, f"{executable} exited {process.returncode}", exit_code=process.returncode)
  return CommandResult(process.stdout, process.stderr)


def sha256_file(path: Path) -> Sha256:
  digest = hashlib.sha256()
  try:
    with path.open("rb") as stream:
      for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
  except OSError as error:
    raise PackagingError(ErrorCode.MISSING_SPLIT, "file is not readable", path=path) from error
  return Sha256(digest.hexdigest())


def tool_environment(tools: AndroidTools) -> dict[str, str]:
  environment = os.environ.copy()
  java_bin = tools.java_home / "bin"
  environment.update({"JAVA_HOME": str(tools.java_home), "PATH": f"{java_bin}{os.pathsep}{environment.get('PATH', '')}"})
  return environment


def _private_key_aliases(listing: str) -> tuple[str, ...]:
  lines = tuple(line.strip() for line in listing.splitlines())
  counts = tuple(
    int(matched.group(1)) for line in lines if (matched := _KEYSTORE_ENTRY_COUNT.fullmatch(line)) is not None
  )
  if lines.count("Keystore type: PKCS12") != 1 or len(counts) != 1:
    raise PackagingError(ErrorCode.TOOL_FAILURE, "keytool output could not be parsed safely")
  entries: list[tuple[str, str]] = []
  pending_alias: str | None = None
  for line in lines:
    alias_match = _KEYSTORE_ALIAS.fullmatch(line)
    if alias_match is not None:
      if pending_alias is not None:
        raise PackagingError(ErrorCode.TOOL_FAILURE, "keytool output could not be parsed safely")
      pending_alias = alias_match.group(1)
      continue
    entry_type_match = _KEYSTORE_ENTRY_TYPE.fullmatch(line)
    if entry_type_match is not None:
      if pending_alias is None:
        raise PackagingError(ErrorCode.TOOL_FAILURE, "keytool output could not be parsed safely")
      entries.append((pending_alias, entry_type_match.group(1)))
      pending_alias = None
  aliases = tuple(alias for alias, _ in entries)
  if pending_alias is not None or len(entries) != counts[0] or len(set(aliases)) != len(aliases):
    raise PackagingError(ErrorCode.TOOL_FAILURE, "keytool output could not be parsed safely")
  if counts[0] != 1:
    return ()
  return tuple(alias for alias, entry_type in entries if entry_type == "PrivateKeyEntry")


def resolve_signing_alias(signer: SigningConfig, tools: AndroidTools) -> str:
  if not signer.keystore.is_file():
    raise PackagingError(ErrorCode.TOOL_FAILURE, "user-supplied keystore is not a regular file", path=signer.keystore)
  environment = tool_environment(tools)
  if signer.store_password_env not in environment or signer.key_password_env not in environment:
    raise PackagingError(ErrorCode.TOOL_FAILURE, "signing password environment variable is missing")
  java_bin = tools.java_home / "bin"
  keytool_exe = java_bin / "keytool.exe"
  keytool = keytool_exe if keytool_exe.is_file() else java_bin / "keytool"
  listing = run_checked(
    (
      str(keytool),
      "-J-Duser.language=en",
      "-J-Duser.country=US",
      "-list",
      "-v",
      "-keystore",
      str(signer.keystore),
      "-storetype",
      "PKCS12",
      "-storepass:env",
      signer.store_password_env,
    ),
    environment,
    tools.timeout_seconds,
  ).stdout
  private_key_aliases = _private_key_aliases(listing)
  if len(private_key_aliases) != 1:
    raise PackagingError(ErrorCode.TOOL_FAILURE, "PKCS12 must contain exactly one entry, and it must be a private-key entry")
  resolved = private_key_aliases[0]
  if signer.alias is not None and signer.alias != resolved:
    raise PackagingError(ErrorCode.TOOL_FAILURE, "explicit signing alias does not match the sole private-key entry")
  return resolved


def resolve_signing_config(signer: SigningConfig, tools: AndroidTools) -> SigningConfig:
  return SigningConfig(
    keystore=signer.keystore,
    alias=resolve_signing_alias(signer, tools),
    store_password_env=signer.store_password_env,
    key_password_env=signer.key_password_env,
  )


def inspect_apk(path: Path, tools: AndroidTools) -> ApkIdentity:
  environment = tool_environment(tools)
  badging = run_checked((str(tools.aapt2), "dump", "badging", str(path)), environment, tools.timeout_seconds).stdout
  package_line = next((line for line in badging.splitlines() if line.startswith("package:")), "")
  package_name = _quoted_field(package_line, "name", path)
  version_name = _quoted_field(package_line, "versionName", path)
  version_code_text = _quoted_field(package_line, "versionCode", path)
  split_match = re.search(r"\bsplit='([^']+)'", package_line)
  try:
    version_code = int(version_code_text)
  except ValueError as error:
    raise PackagingError(ErrorCode.WRONG_VERSION, "manifest versionCode is not an integer", path=path) from error
  verification = run_checked(
    (str(tools.apksigner), "verify", "--verbose", "--print-certs", str(path)), environment, tools.timeout_seconds,
  )
  certificate_digests = {
    match.group(1).replace(":", "").lower() for match in _CERTIFICATE_DIGEST.finditer(verification.stdout)
  }
  if len(certificate_digests) != 1:
    raise PackagingError(ErrorCode.SIGNER_MISMATCH, "APK must have exactly one package signer", path=path)
  xmltree = run_checked(
    (str(tools.aapt2), "dump", "xmltree", "--file", "AndroidManifest.xml", str(path)),
    environment,
    tools.timeout_seconds,
  ).stdout
  required_types = _required_split_types(xmltree)
  return ApkIdentity(
    path=path,
    package_name=package_name,
    version_code=version_code,
    version_name=version_name,
    split_name=None if split_match is None else split_match.group(1),
    signer_sha256=Sha256(certificate_digests.pop()),
    required_split_types=required_types,
  )


def _quoted_field(line: str, field: str, path: Path) -> str:
  matched = re.search(rf"\b{re.escape(field)}='([^']*)'", line)
  if matched is None:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, f"manifest package field {field} is missing", path=path)
  return matched.group(1)


def _required_split_types(xmltree: str) -> tuple[str, ...]:
  line = next((item for item in xmltree.splitlines() if "requiredSplitTypes" in item), "")
  matched = re.search(r'(?:Raw: )?"([^"]+)"', line)
  if matched is None:
    return ()
  return tuple(sorted(part.strip() for part in matched.group(1).split(",") if part.strip()))


def rewrite_apk(source: Path, destination: Path, patches: tuple[ArchivePatch, ...]) -> None:
  patch_names = tuple(item.entry_name for item in patches)
  if len(set(patch_names)) != len(patch_names):
    raise PackagingError(ErrorCode.INVALID_PATCH_ENTRY, "duplicate patch entry")
  invalid = next((name for name in patch_names if _DEX_ENTRY.fullmatch(name) is None), None)
  if invalid is not None:
    raise PackagingError(ErrorCode.INVALID_PATCH_ENTRY, "only classes*.dex base entries may be replaced", path=Path(invalid))
  replacements = {item.entry_name: item.content for item in patches}
  try:
    with zipfile.ZipFile(source, "r") as source_zip, zipfile.ZipFile(destination, "w") as destination_zip:
      names = source_zip.namelist()
      if len(set(names)) != len(names):
        raise PackagingError(ErrorCode.MALFORMED_METADATA, "APK contains duplicate archive entries", path=source)
      for source_info in sorted(source_zip.infolist(), key=lambda item: item.filename):
        if _SIGNATURE_ENTRY.fullmatch(source_info.filename) is not None:
          continue
        content = replacements.pop(source_info.filename, None)
        target_info = _normalized_info(source_info)
        if content is not None:
          destination_zip.writestr(target_info, content)
          continue
        with source_zip.open(source_info, "r") as input_stream, destination_zip.open(target_info, "w") as output_stream:
          shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
      for entry_name, content in sorted(replacements.items()):
        info = zipfile.ZipInfo(entry_name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_STORED
        destination_zip.writestr(info, content)
  except (OSError, zipfile.BadZipFile, RuntimeError) as error:
    raise PackagingError(ErrorCode.MALFORMED_METADATA, "APK archive could not be repacked", path=source) from error


def _normalized_info(source: zipfile.ZipInfo) -> zipfile.ZipInfo:
  target = zipfile.ZipInfo(source.filename, date_time=(1980, 1, 1, 0, 0, 0))
  target.compress_type = source.compress_type
  target.external_attr = source.external_attr
  target.internal_attr = source.internal_attr
  target.create_system = source.create_system
  return target


def align_and_sign(unsigned: Path, output: Path, signer: SigningConfig, tools: AndroidTools) -> None:
  if not signer.keystore.is_file():
    raise PackagingError(ErrorCode.TOOL_FAILURE, "user-supplied keystore is not a regular file", path=signer.keystore)
  environment = tool_environment(tools)
  if signer.store_password_env not in environment or signer.key_password_env not in environment:
    raise PackagingError(ErrorCode.TOOL_FAILURE, "signing password environment variable is missing")
  alias = signer.alias
  if alias is None:
    raise PackagingError(ErrorCode.TOOL_FAILURE, "signing alias was not resolved")
  aligned = unsigned.with_name(f"{unsigned.stem}-aligned.apk")
  run_checked(
    (str(tools.zipalign), "-f", "-P", "16", "4", str(unsigned), str(aligned)), environment, tools.timeout_seconds,
  )
  run_checked(
    (
      str(tools.apksigner), "sign", "--ks", str(signer.keystore), "--ks-key-alias", alias,
      "--ks-pass", f"env:{signer.store_password_env}", "--key-pass", f"env:{signer.key_password_env}",
      "--v4-signing-enabled", "false", "--out", str(output), str(aligned),
    ),
    environment,
    tools.timeout_seconds,
  )
  verify_alignment(output, tools)
  inspect_apk(output, tools)


def verify_alignment(path: Path, tools: AndroidTools) -> None:
  run_checked(
    (str(tools.zipalign), "-c", "-P", "16", "-v", "4", str(path)), tool_environment(tools), tools.timeout_seconds,
  )
