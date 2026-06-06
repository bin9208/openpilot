#!/usr/bin/env python3
import sys
import subprocess
import pathlib

def main():
    script_path = pathlib.Path(__file__).parent / "visualize_camera.py"
    args = [sys.executable, str(script_path)] + sys.argv[1:]
    sys.exit(subprocess.run(args).returncode)

if __name__ == "__main__":
    main()
