"""Persist events and their report projection before publishing them live."""

from app import storage
from app.broker import Broker
from app.events import CheckEvent, StoredEvent
from app.models import CheckStatus


class RecorderSink:
    def __init__(self, db_path: str, check_id: str, broker: Broker) -> None:
        self.db_path, self.check_id, self.broker = db_path, check_id, broker

    def emit(self, event: CheckEvent) -> None:
        seq = storage.append_event(self.db_path, self.check_id, event.type, event.model_dump_json())
        if event.type == "environment":
            storage.update_check(
                self.db_path,
                self.check_id,
                versions=event.versions,
                failed_packages=event.failed_packages,
            )
        elif event.type == "extracted":
            storage.update_check(
                self.db_path,
                self.check_id,
                title=event.title,
                published=event.published,
                date_source=event.date_source,
            )
        elif event.type == "step_final":
            storage.save_step(self.db_path, self.check_id, event.step)
        elif event.type == "done":
            storage.update_check(
                self.db_path, self.check_id, status=CheckStatus.DONE, score=event.score
            )
        elif event.type == "failed":
            storage.update_check(
                self.db_path, self.check_id, status=CheckStatus.FAILED, error=event.error
            )
        self.broker.publish(
            self.check_id, StoredEvent(seq=seq, check_id=self.check_id, event=event)
        )
