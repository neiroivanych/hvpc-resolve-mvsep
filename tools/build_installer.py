"""Build the Windows wizard using the .NET Framework bundled with Windows."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def compiler() -> Path:
    windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    for arch in ('Framework64', 'Framework'):
        path = windows / 'Microsoft.NET' / arch / 'v4.0.30319/csc.exe'
        if path.is_file():
            return path
    raise RuntimeError('The Windows .NET Framework compiler was not found.')


def build(root: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        str(compiler()), '/nologo', '/target:winexe', '/utf8output', '/optimize+',
        '/out:' + str(output), '/win32manifest:' + str(root / 'installer/app.manifest'),
        '/win32icon:' + str(root / 'src/resolve_mvsep/app.ico'),
        '/resource:' + str(root / 'src/resolve_mvsep/app.ico') + ',HVPCSetup.AppIcon',
        '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
        '/reference:System.Web.Extensions.dll', '/reference:System.Management.dll',
        str(root / 'installer/SetupCore.cs'), str(root / 'installer/SetupForm.cs'),
    ], check=True, capture_output=True, timeout=60)


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    output = Path(sys.argv[1]).resolve() if len(sys.argv) == 2 else root / 'build/installer/Install.exe'
    try:
        build(root, output)
    except subprocess.CalledProcessError as error:
        print(error.stdout.decode('utf-8', errors='replace'), file=sys.stderr)
        raise SystemExit(error.returncode)
    print(output)
