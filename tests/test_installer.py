"""Compile and exercise the real Windows installer against isolated folders."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import runpy
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_installer import build, compiler


@unittest.skipUnless(os.name == 'nt', 'Windows installer')
class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='hvpc-installer-tests-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        setup = cls.directory / 'Install.exe'
        build(ROOT, setup)
        cls.harness = cls.directory / 'InstallerTests.exe'
        result = subprocess.run([
            str(compiler()), '/nologo', '/utf8output', '/target:exe', '/out:' + str(cls.harness),
            '/reference:' + str(setup), '/reference:System.Web.Extensions.dll',
            '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
            str(ROOT / 'tests/InstallerTests.cs'),
        ], capture_output=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout.decode('utf-8', errors='replace'))

    def exercise(self, scenario):
        with tempfile.TemporaryDirectory(prefix='hvpc-install-case-') as directory:
            result = subprocess.run([str(self.harness), str(ROOT), directory, scenario],
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_fresh_install(self): self.exercise('fresh')
    def test_update_preserves_user_data(self): self.exercise('update')
    def test_failure_restores_previous_files(self): self.exercise('rollback')
    def test_failed_fresh_install_can_be_retried(self): self.exercise('fresh_rollback')
    def test_corrupt_config_is_not_overwritten(self): self.exercise('config')
    def test_invalid_inputs_do_not_mutate_folders(self): self.exercise('inputs')
    def test_unicode_and_shell_characters_in_paths(self): self.exercise('paths')
    def test_private_source_data_is_not_copied(self): self.exercise('privacy')
    def test_incomplete_package_is_rejected(self): self.exercise('package')
    def test_existing_install_can_change_token_and_language(self): self.exercise('inplace')
    def test_update_archive_inside_existing_working_folder(self): self.exercise('nested_update')

    def test_generated_menu_launcher_opens_chosen_folder(self):
        with tempfile.TemporaryDirectory(prefix='hvpc-menu-test-') as directory:
            result = subprocess.run([str(self.harness), str(ROOT), directory, 'paths'],
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            expected = Path(directory) / "Музыка & монтаж 'test' (новое)"
            fake = types.ModuleType('resolve_mvsep.main')
            calls = []
            fake.run = lambda root, resolve: calls.append((root, resolve)) or 0
            launcher = Path(directory) / 'fake-Utility/HVPC Resolve-MVSEP.py'
            with patch.dict(sys.modules, {'resolve_mvsep.main': fake}), patch.dict(os.environ, {'RESOLVE_MVSEP_HOME': 'obsolete-folder'}):
                runpy.run_path(str(launcher), init_globals={'resolve': 'injected-test-object'})
            self.assertEqual(calls, [(expected.resolve(), 'injected-test-object')])
