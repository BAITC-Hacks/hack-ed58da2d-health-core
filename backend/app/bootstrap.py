"""Idempotent first-start import and model training from the local TZ directory."""

import logging
import os
import re
from pathlib import Path
from threading import Lock, Thread

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import ModelArtifact, ModelRevision, SessionLocal
from .pipeline import TRAINING_CUTOFF, import_csv, train_model

logger = logging.getLogger(__name__)
CSV_NAME = re.compile(r"(?:^|[\s_-])turbine[\s_-]*([12])\.csv$", re.IGNORECASE)


class BootstrapManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._started = False
        self._busy = False
        self._status = "unavailable"
        self._message = "Модель ещё не обучена."

    def _set(self, status: str, message: str, busy: bool) -> None:
        with self._lock:
            self._status, self._message, self._busy = status, message, busy

    def start(self, source_dir: Path | None = None) -> None:
        if os.getenv("AUTO_BOOTSTRAP", "true").lower() not in ("1", "true", "yes"):
            return
        with self._lock:
            if self._started:
                return
            self._started = True
            self._busy = True
            self._status = "preparing"
            self._message = "Проверяем данные и модель."
        directory = source_dir or Path(os.getenv("TZ_DIR", "TZ"))
        Thread(target=self.run, args=(SessionLocal, directory), name="healthcore-bootstrap", daemon=True).start()

    @staticmethod
    def _csv_paths(source_dir: Path) -> dict[int, Path]:
        matches: dict[int, Path] = {}
        if source_dir.is_dir():
            for path in source_dir.iterdir():
                if not path.is_file():
                    continue
                match = CSV_NAME.search(path.name)
                if match:
                    turbine_id = int(match.group(1))
                    if turbine_id in matches:
                        raise ValueError(f"В {source_dir} найдено несколько CSV для турбины {turbine_id}.")
                    matches[turbine_id] = path
        missing = {1, 2} - matches.keys()
        if missing:
            raise FileNotFoundError(
                f"В {source_dir} отсутствуют конкурсные CSV для турбин: {', '.join(map(str, sorted(missing)))}. "
                "Поместите файлы из ТЗ в эту папку и перезапустите backend."
            )
        return matches

    def run(self, session_factory=SessionLocal, source_dir: Path | None = None) -> None:
        """Run synchronously for tests or in a background thread at API startup."""
        directory = source_dir or Path(os.getenv("TZ_DIR", "TZ"))
        try:
            with session_factory() as session:
                if session.scalar(select(ModelRevision.id).order_by(ModelRevision.id.desc()).limit(1)):
                    self._set("ready", "Модель загружена из PostgreSQL; прогнозирование доступно.", False)
                    return
                paths = self._csv_paths(directory)
                for turbine_id in (1, 2):
                    self._set("importing", f"Загружаем CSV турбины {turbine_id} в БД.", True)
                    imported = import_csv(session, paths[turbine_id], turbine_id)
                    logger.info("Bootstrap imported %s rows for turbine %s", imported, turbine_id)
                self._set("training", "Данные загружены. Обучаем модель прогноза.", True)
                result = train_model(session, TRAINING_CUTOFF)
                if set(result["turbine_ids"]) != {1, 2}:
                    raise ValueError("Модель не обучилась для обеих турбин; проверьте CSV и дату отсечения.")
            self._set("ready", "Данные загружены, модель обучена; прогнозирование доступно.", False)
        except FileNotFoundError as exc:
            self._set("unavailable", str(exc), False)
            logger.warning("Bootstrap unavailable: %s", exc)
        except ValueError as exc:
            self._set("unavailable", f"Подготовка остановлена: {exc}", False)
            logger.warning("Bootstrap validation failed: %s", exc)
        except Exception:
            self._set("unavailable", "Ошибка подготовки данных или модели. Проверьте журнал backend.", False)
            logger.exception("Bootstrap failed")

    def begin_training(self) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._status = "training"
            self._message = "Модель в процессе обучения."
            return True

    def finish_training(self, error: str | None = None) -> None:
        self._set("unavailable" if error else "ready",
                  f"Обучение не завершено: {error}" if error else "Модель обучена; прогнозирование доступно.",
                  False)

    def snapshot(self, session: Session) -> dict:
        revision = session.scalar(select(ModelRevision).order_by(ModelRevision.id.desc()).limit(1))
        artifact_id = (session.scalar(select(ModelArtifact.revision_id)
                                      .where(ModelArtifact.revision_id == revision.id)) if revision else None)
        supported = set(revision.turbine_ids) if revision else set()
        can_forecast = bool(revision and {1, 2}.issubset(supported)
                            and (artifact_id is not None or Path(revision.artifact_path).is_file()))
        with self._lock:
            status, message, busy = self._status, self._message, self._busy
        if can_forecast and not busy:
            status = "ready"
            message = "Модель обучена; прогнозирование доступно."
        elif revision is not None and not busy:
            status = "unavailable"
            message = "Ревизия модели не содержит обе турбины или её файл недоступен. Переобучите модель."
        return {
            "status": status,
            "can_forecast": can_forecast,
            "message": message,
            "model_revision_id": revision.id if revision else None,
            "model_version": revision.version if revision else None,
            "supported_turbine_ids": revision.turbine_ids if revision else [],
        }


bootstrap = BootstrapManager()
