"""Helpers for reporting interpreter runtime details in benchmark servers."""

from __future__ import annotations

import sys
import sysconfig


def runtime_summary(host: str, port: int, app_name: str) -> str:
    """Return a short startup line for benchmark servers."""

    gil_enabled = _gil_enabled()
    free_threaded_build = _free_threaded_build()
    return (
        "[%s] listening on http://%s:%s | python=%s | free_threaded_build=%s | gil_enabled=%s"
        % (
            app_name,
            host,
            port,
            sys.version.split()[0],
            "yes" if free_threaded_build else "no",
            "yes" if gil_enabled else "no",
        )
    )


def _free_threaded_build() -> bool:
    value = sysconfig.get_config_var("Py_GIL_DISABLED")
    return bool(value)


def _gil_enabled() -> bool:
    probe = getattr(sys, "_is_gil_enabled", None)
    if probe is None:
        return True
    return bool(probe())
