import threading
import time

from app.services.document_processor import DocumentProcessor


def test_document_processor_limits_processing_concurrency_to_three():
    lock = threading.Lock()
    active = 0
    max_active = 0

    class TrackingProcessor(DocumentProcessor):
        def __init__(self):
            pass

        def _process_locked(self, db, document):  # noqa: ANN001
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return document

    processor = TrackingProcessor()
    threads = [
        threading.Thread(target=processor.process, args=(None, object()))
        for _ in range(8)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert max_active <= 3
