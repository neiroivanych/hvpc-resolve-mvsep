"""Check publication exclusions in an isolated Git directory, never the user's repo."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('git'), 'Git required to check publication exclusions')
class ReleaseTests(unittest.TestCase):
    def test_private_runtime_files_are_ignored_and_portable_sources_are_included(self):
        root = Path(__file__).resolve().parents[1]
        private = ['config/mvsep.json', 'config/mvsep.json.backup', 'config/recent_settings.json',
                   'config/recent_settings.tmp', 'config/recent_settings.lock', 'config/ui.json',
                   'config/profiles.json', 'config/source-runs.json', 'settings.json', '.env',
                   'jobs/example/job.json', 'logs/window.log', 'logs/screenshot.png',
                   'output/song/song_vocals.wav', 'downloads/song.wav', 'ResolveMVSEP.py',
                   'Untitled Project/cache/audio.wav', 'src/__pycache__/main.cpython-314.pyc',
                   'NOTES.md', 'docs/AUDIT-2026-10-10.md', 'Install.exe']
        public = ['README.md', 'README.en.md', 'LICENSE', '.gitignore', 'mvsep.example.json', 'settings.example.json',
                  'logs/.gitkeep', 'src/resolve_mvsep/cli.py', 'scripts/HVPC Resolve-MVSEP.py',
                  'tests/test_release.py', '.github/workflows/tests.yml', 'tools/prepare_release.py',
                  '.github/ISSUE_TEMPLATE/bug_report.md', 'docs/releases/v0.2.0.md',
                  'Install.cmd', 'installer/SetupCore.cs', 'installer/SetupForm.cs',
                  'installer/app.manifest', 'tools/build_installer.py', 'tests/InstallerTests.cs']
        with tempfile.TemporaryDirectory() as directory:
            working = Path(directory)
            subprocess.run(['git', 'init', '-q', str(working)], check=True, capture_output=True, timeout=10)
            shutil.copyfile(root / '.gitignore', working / '.gitignore')
            result = subprocess.run(['git', '-c', 'core.excludesFile=', 'check-ignore', '--no-index', '--stdin', '-z'],
                                    cwd=working, input='\0'.join(private + public).encode() + b'\0',
                                    capture_output=True, check=False, timeout=10)
            self.assertIn(result.returncode, (0, 1), result.stderr.decode(errors='replace'))
            ignored = set(result.stdout.decode().strip('\0').split('\0'))
            self.assertEqual(ignored, set(private))
