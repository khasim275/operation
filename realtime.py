from __future__ import annotations

import json
import queue
import threading

import psycopg

from config import get_settings

_subscribers: set[queue.Queue[str]] = set()
_subscribers_lock = threading.Lock()
_stop = threading.Event()
_listener_thread: threading.Thread | None = None


def _listen_forever() -> None:
    while not _stop.is_set():
        try:
            with psycopg.connect(get_settings().database_dsn, autocommit=True) as connection:
                connection.execute("LISTEN crm_events")
                for notification in connection.notifies(timeout=1, stop_after=1):
                    with _subscribers_lock:
                        subscribers = tuple(_subscribers)
                    for subscriber in subscribers:
                        try:
                            subscriber.put_nowait(notification.payload)
                        except queue.Full:
                            try:
                                subscriber.get_nowait()
                                subscriber.put_nowait(notification.payload)
                            except queue.Empty:
                                pass
        except psycopg.Error:
            _stop.wait(2)


def start_listener() -> None:
    global _listener_thread
    _stop.clear()
    _listener_thread = threading.Thread(target=_listen_forever, name="postgres-notify-listener", daemon=True)
    _listener_thread.start()


def stop_listener() -> None:
    _stop.set()
    if _listener_thread and _listener_thread.is_alive():
        _listener_thread.join(timeout=3)


def subscribe() -> queue.Queue[str]:
    subscriber: queue.Queue[str] = queue.Queue(maxsize=100)
    with _subscribers_lock:
        _subscribers.add(subscriber)
    return subscriber


def unsubscribe(subscriber: queue.Queue[str]) -> None:
    with _subscribers_lock:
        _subscribers.discard(subscriber)


def encode_event(raw_payload: str) -> str:
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        payload = {"entity": "unknown", "action": "changed"}
    return json.dumps(payload, separators=(",", ":"))
