from __future__ import annotations


class AppError(Exception):
    """Base application error with error code."""

    def __init__(self, code: int, message: str, detail: str = "") -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)


# ── Spider errors (1xxx) ───────────────────────────────────
class SpiderNotFoundError(AppError):
    def __init__(self, spider_id: str) -> None:
        super().__init__(1001, "Spider not found", f"Spider {spider_id} does not exist")


class SpiderConfigError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(1002, "Invalid spider config", detail)


# ── Job errors (2xxx) ──────────────────────────────────────
class JobAlreadyRunningError(AppError):
    def __init__(self, job_id: str) -> None:
        super().__init__(2001, "Job already running", f"Job {job_id} is already running")


class ScheduleConflictError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(2002, "Schedule conflict", detail)


# ── Scraping runtime errors (3xxx) ─────────────────────────
class NetworkTimeoutError(AppError):
    def __init__(self, url: str) -> None:
        super().__init__(3001, "Network timeout", f"Request to {url} timed out")


class ParseError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(3002, "Parse failed", detail)


class ProxyExhaustedError(AppError):
    def __init__(self) -> None:
        super().__init__(3003, "Proxy exhausted", "No available proxies in pool")


# ── Storage errors (4xxx) ─────────────────────────────────
class WriteError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(4001, "Write failed", detail)


class ConnectionError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(4002, "Connection broken", detail)
