"""Offline publication audit and clean ZIP; never initializes or publishes this repo.

Run with a standalone Python 3.10+ and Git, from any directory. Output goes into
ignored build/ and logs/. Credentials are read only to detect accidental copies;
their values are never printed, written to the report, or added to the archive.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, quote_plus

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PARTS = {'config', 'jobs', 'output', 'downloads', 'Untitled Project',
                 '__pycache__', '.git', '.venv', 'venv', 'build', 'dist'}
PRIVATE_SUFFIXES = {'.wav', '.mp3', '.flac', '.mp4', '.mov', '.drp', '.dra',
                    '.log', '.pem', '.key', '.pfx', '.pyc'}


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, timeout=kwargs.pop('timeout', 30), **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace') or 'Command failed.')
    return result


def is_private(name):
    path = Path(name)
    return (any(part.casefold() in {value.casefold() for value in PRIVATE_PARTS} for part in path.parts)
            or path.suffix.lower() in PRIVATE_SUFFIXES
            or path.name in {'settings.json', 'ResolveMVSEP.py'}
            or path.name.startswith('.env')
            or (name.startswith('logs/') and name != 'logs/.gitkeep'))


def secret_variants():
    secrets = {os.environ.get('MVSEP_API_TOKEN', '').strip()}
    config = ROOT / 'config/mvsep.json'
    if config.exists():
        try:
            data = json.loads(config.read_text(encoding='utf-8-sig'))
            value = data.get('api_token', '')
            if not isinstance(value, str):
                raise ValueError()
            secrets.add(value.strip())
        except (OSError, ValueError, TypeError, AttributeError):
            raise RuntimeError('Cannot safely read local token for the audit; check config/mvsep.json.') from None
    secrets.discard('')
    variants = secrets | {quote(value, safe='') for value in secrets} | {quote_plus(value) for value in secrets}
    return [value.encode('utf-8') for value in variants]


def check_docs(clean):
    for name in ('README.md', 'README.en.md'):
        text = (clean / name).read_text(encoding='utf-8-sig')
        if re.search(r'\bM[1-5]\b', text):
            raise RuntimeError(f'Development-stage labels found in {name}')
        if text.count('```') % 2:
            raise RuntimeError(f'Unclosed code fence in {name}')
        for link in re.findall(r'\]\(([^)]+)\)', text):
            if '://' not in link and not link.startswith('#') and not (clean / link.split('#')[0]).is_file():
                raise RuntimeError(f'Broken local documentation link in {name}: {link}')


def main():
    if not shutil.which('git'):
        raise RuntimeError('Git is required for the publication inventory.')
    if (ROOT / '.git').exists():
        tracked_private = run(['git', '-C', str(ROOT), 'ls-files', '-ci', '--exclude-standard', '-z']).stdout
        if tracked_private:
            raise RuntimeError('Ignored local files are already tracked by Git. Review git ls-files -ci --exclude-standard.')
    tokens = secret_variants()
    with tempfile.TemporaryDirectory(prefix='hvpc-release-') as temporary:
        scratch = Path(temporary)
        inventory = scratch / 'inventory'
        run(['git', 'init', '-q', str(inventory)])
        output = run(['git', '-c', 'core.excludesFile=', '--git-dir=' + str(inventory / '.git'),
                      '--work-tree=' + str(ROOT), 'ls-files', '--others', '--exclude-standard', '-z'], cwd=ROOT).stdout
        names = sorted(filter(None, output.decode('utf-8').split('\0')))
        if not names:
            raise RuntimeError('Empty publication inventory.')
        clean = scratch / 'clean'
        clean.mkdir()
        hashes = {}
        for name in names:
            source = ROOT / name
            if is_private(name) or source.is_symlink() or not source.resolve().is_relative_to(ROOT):
                raise RuntimeError('Private or linked path in publication inventory: ' + name)
            content = source.read_bytes()
            if any(token in content for token in tokens):
                raise RuntimeError('Local API token found in candidate file: ' + name)
            # A personal home path is never needed in portable source/docs.
            home = str(Path.home())
            homes = {home, home.replace('\\', '/'), home.replace('\\', '\\\\')}
            if any(value.encode('utf-8') in content for value in homes):
                raise RuntimeError('Personal home path found in candidate file: ' + name)
            target = clean / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            hashes[name] = hashlib.sha256(content).hexdigest()
            if name.endswith('.py'):
                ast.parse(content.decode('utf-8-sig'), filename=name, feature_version=(3, 10))
        if json.loads((clean / 'mvsep.example.json').read_text(encoding='utf-8-sig')).get('api_token'):
            raise RuntimeError('The example configuration contains credentials.')
        if json.loads((clean / 'settings.example.json').read_text(encoding='utf-8-sig')).get('action') != 'separate':
            raise RuntimeError('Fresh installation must open separation settings.')
        check_docs(clean)
        env = dict(os.environ, PYTHONUTF8='1')
        env.pop('MVSEP_API_TOKEN', None)
        env.pop('RESOLVE_MVSEP_HOME', None)
        tests = run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'],
                    cwd=clean, env=env, timeout=120).stderr.decode('utf-8').strip()
        run([sys.executable, '-m', 'compileall', '-q', 'src', 'scripts', 'tests', 'tools'], cwd=clean, env=env)
        for name in names:
            if hashlib.sha256((clean / name).read_bytes()).hexdigest() != hashes[name]:
                raise RuntimeError('A public file changed during the clean-copy tests: ' + name)
        created = datetime.now(timezone.utc)
        folder = ROOT / 'build'
        folder.mkdir(exist_ok=True)
        archive = folder / ('HVPC-Resolve-MVSEP-clean-' + created.strftime('%Y%m%d-%H%M%S-%f') + '.zip')
        # Only the audited inventory is packed: test caches/config never enter it.
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
            for name in names:
                bundle.write(clean / name, 'HVPC Resolve-MVSEP/' + name)
        with zipfile.ZipFile(archive) as bundle:
            expected = {'HVPC Resolve-MVSEP/' + name for name in names}
            if set(bundle.namelist()) != expected or bundle.testzip() is not None:
                raise RuntimeError('ZIP verification failed.')
            for name in names:
                if hashlib.sha256(bundle.read('HVPC Resolve-MVSEP/' + name)).hexdigest() != hashes[name]:
                    raise RuntimeError('ZIP content mismatch: ' + name)
        report = {'created_at': created.isoformat(), 'file_count': len(names), 'files': names,
                  'sha256_by_file': hashes, 'private_files_excluded': True,
                  'local_secret_variants_checked': len(tokens), 'local_secret_matches': 0,
                  'personal_home_matches': 0, 'clean_copy_tests': tests, 'compilation': 'passed',
                  'python_3_10_syntax': 'passed', 'readme_links': 'passed',
                  'archive': str(archive), 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                  'limitations': 'Checks current files and known local tokens; not a Git history audit or a rights clearance.'}
        (ROOT / 'logs').mkdir(exist_ok=True)
        (ROOT / 'logs/release-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({key: value for key, value in report.items() if key not in {'files', 'sha256_by_file'}}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Release preparation failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
