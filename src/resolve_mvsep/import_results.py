"""Launch import separately so a slow Resolve bridge cannot freeze the status UI."""
import os
import subprocess
from pathlib import Path

from .core.i18n import tr
from .core.logging import create_logger, close_logger
from .core.resolve_import import import_message, read_receipt, save_receipt, import_running
from .separation import worker_python


def launch_import(root: Path, path: Path, target: str):
    with path.with_name('import-worker.log').open('ab') as output:
        return subprocess.Popen([str(worker_python()), str(root / 'src/mvsep_cli.py'),
                                 '--import-job', str(path), '--import-target', target],
                                cwd=str(root), stdin=subprocess.DEVNULL, stdout=output,
                                stderr=subprocess.STDOUT, close_fds=True,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def run_import(root: Path, path: Path, target: str) -> int:
    from .resolve.importer import import_results, import_error
    logger, _ = create_logger(root, prefix='resolve_import')
    previous = read_receipt(path)
    try:
        receipt = import_results(root, path, target, logger=logger)
        logger.info(import_message(receipt))
        return 0
    except Exception as exc:
        logger.exception(tr('Импорт результатов в Resolve не завершён.'))
        # Preflight/lock failures happen before the importer writes its receipt.
        # Surface them too, without overwriting an active or newer invocation.
        current = read_receipt(path)
        if (path.resolve().parent.parent == (root / 'jobs').resolve() and path.name == 'job.json'
                and path.is_file() and current == previous and not import_running(current)):
            try:
                save_receipt(path, {**current, 'status': 'failed', 'target': target,
                                    'error': import_error(exc, tr('Запуск импорта в Resolve'))})
            except OSError:
                logger.exception('Could not save import failure; see import-worker.log.')
        return 1
    finally:
        close_logger(logger)
