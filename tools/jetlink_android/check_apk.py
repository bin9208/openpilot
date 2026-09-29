"""Check the actual APK has arm64 ORT/JNI and executable Android content."""
import hashlib
import sys
import zipfile
from pathlib import Path


def check(path: Path):
  with zipfile.ZipFile(path) as archive:
    names = set(archive.namelist())
    required = {'AndroidManifest.xml', 'classes.dex', 'resources.arsc',
                'lib/arm64-v8a/libonnxruntime.so', 'lib/arm64-v8a/libonnxruntime4j_jni.so'}
    if not required <= names or any(n.startswith('lib/') and not n.startswith('lib/arm64-v8a/') for n in names):
      raise ValueError('APK is missing runtime content or contains an unexpected ABI')
    if archive.testzip() is not None:
      raise ValueError('APK CRC failure')
  print(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name} ({path.stat().st_size} bytes)')


if __name__ == '__main__':
  check(Path(sys.argv[1]))
