"""Bounded clip concurrency under one queue owner and one service CPU budget."""
import signal
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

from identity_analysis import claim_job, finish_job

_models = None


def initialize_models(folder):
    global _models
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    # The supervisor owns cleanup. Children must terminate before queue recovery.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    from identity_models import LocalModels
    _models = LocalModels(folder)


def analyze_job(db_path, archive_root, job, pause, controller_url):
    from identity_worker import process_one, wait_for_controller
    process_one(db_path, archive_root, job, _models,
                checkpoint=lambda: wait_for_controller(pause, controller_url))


class ParallelClips:
    def __init__(self, db_path, archive_root, folder, pause, controller_url,
                 workers=2, executor=None):
        self.db_path, self.archive_root = db_path, archive_root
        self.pause, self.controller_url = pause, controller_url
        self.workers = workers
        self.executor = executor or ProcessPoolExecutor(
            max_workers=workers, mp_context=get_context('spawn'),
            initializer=initialize_models, initargs=(folder,))
        self.active = {}

    def collect(self):
        for future, job in list(self.active.items()):
            if not future.done():
                continue
            del self.active[future]
            try:
                future.result()
            except Exception as exc:
                finish_job(self.db_path, job['catalog_id'], 'failed', str(exc))

    def fill(self):
        while len(self.active) < self.workers:
            job = claim_job(self.db_path)
            if job is None:
                break
            try:
                future = self.executor.submit(analyze_job, self.db_path,
                    self.archive_root, job, self.pause, self.controller_url)
            except Exception as exc:
                finish_job(self.db_path, job['catalog_id'], 'failed', str(exc))
                raise
            self.active[future] = job

    def message(self):
        ids = ', '.join(str(job['catalog_id']) for job in self.active.values())
        return f'Analyzing clips {ids}'

    def close(self):
        if not self.active:
            self.executor.shutdown(wait=True, cancel_futures=True)
            return
        # Python 3.14 has a public termination API. On older Python, terminate
        # owned children before shutdown; shutdown(wait=True) alone can keep
        # analyzing for many minutes after the service receives SIGTERM.
        if hasattr(self.executor, 'terminate_workers'):
            self.executor.terminate_workers()
        else:
            processes = list((getattr(self.executor, '_processes', None) or {}).values())
            for process in processes:
                if process.is_alive():
                    process.terminate()
            for process in processes:
                process.join(timeout=5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
            self.executor.shutdown(wait=True, cancel_futures=True)
        self.active.clear()
