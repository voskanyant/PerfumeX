import logging
import threading

from django.db import close_old_connections
from django.utils import timezone

from prices import models


logger = logging.getLogger(__name__)


def run_in_background(callable, *, run_id=None, label=""):
    def _target():
        close_old_connections()
        try:
            callable()
        except Exception as exc:
            logger.exception(
                "Background task failed label=%s run_id=%s",
                label or callable.__name__,
                run_id or "-",
            )
            if run_id:
                models.EmailImportRun.objects.filter(id=run_id).update(
                    status=models.EmailImportStatus.FAILED,
                    finished_at=timezone.now(),
                    errors=1,
                    last_message=f"{label or 'Background task'} failed: {exc}",
                )
        finally:
            close_old_connections()

    thread = threading.Thread(
        target=_target,
        name=f"perfumex-{label or callable.__name__}",
        daemon=True,
    )
    thread.start()
    return thread
