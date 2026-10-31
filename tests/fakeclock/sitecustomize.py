"""A fake wall clock for a whole test run, its child processes included: is the suite green at 06:00?

    CLIP_FACTORY_FAKE_CLOCK=06:00 PYTHONPATH=tests/fakeclock CLIP_FACTORY_STATE=$(mktemp -d) CLIP_FACTORY_OFFLINE=1 \
        HTTPS_PROXY=http://127.0.0.1:9 /usr/bin/python3 -m unittest discover -s tests

CLIP_FACTORY_FAKE_CLOCK is HH:MM (today) or YYYY-MM-DD HH:MM: the run starts at that time and the clock runs on from
there. Python loads this file by itself when the folder is on PYTHONPATH (it is named sitecustomize for that); it
turns the time into an offset in CLIP_FACTORY_FAKE_CLOCK_OFFSET, so every child process keeps the same clock. Moved:
time.time/time_ns/localtime/gmtime/ctime/asctime/strftime, datetime.now/utcnow/today, date.today, and file times
(os.stat/lstat/fstat and os.scandir's entries read them moved, os.utime writes them back), so a file written now reads
as written at the fake now. Not moved: sleeps, timeouts, the monotonic clocks, and programs that are not Python
(`date` in a shell). Nothing here runs unless the folder is on PYTHONPATH and one of the two variables is set; the
factory never puts it there. tests/test_clock_free.py uses it; it was made when the suite failed between 05:50 and
06:25 on the clock alone (2026-10-20).
"""
import os as _os

_OFF = _os.environ.get("CLIP_FACTORY_FAKE_CLOCK_OFFSET", "").strip()
_AT = _os.environ.get("CLIP_FACTORY_FAKE_CLOCK", "").strip()
if _AT and not _OFF:
    import datetime as _d
    import time as _t
    _when = (_d.datetime.strptime(_AT, "%Y-%m-%d %H:%M") if "-" in _AT
             else _d.datetime.combine(_d.date.today(), _d.datetime.strptime(_AT, "%H:%M").time()))
    _OFF = repr(_when.timestamp() - _t.time())
    _os.environ["CLIP_FACTORY_FAKE_CLOCK_OFFSET"] = _OFF        # the children keep this clock, not a fresh one

if _OFF:
    import datetime as _dt
    import time as _time

    _off = float(_OFF)
    _off_ns = int(round(_off * 1e9))
    _off_s = int(round(_off))

    class _Fn:
        """A plain callable, never a method: logging.Formatter keeps `converter = time.localtime` as a class
        attribute, and a Python function there would be bound to the formatter."""
        __slots__ = ("f", "__name__", "__wrapped__")

        def __init__(self, f, real):
            self.f, self.__name__, self.__wrapped__ = f, getattr(real, "__name__", "fn"), real

        def __call__(self, *a, **k):
            return self.f(*a, **k)

    _r_time, _r_time_ns = _time.time, _time.time_ns
    _r_localtime, _r_gmtime = _time.localtime, _time.gmtime
    _r_ctime, _r_asctime, _r_strftime = _time.ctime, _time.asctime, _time.strftime

    def _now():
        return _r_time() + _off

    _time.time = _Fn(_now, _r_time)
    _time.time_ns = _Fn(lambda: _r_time_ns() + _off_ns, _r_time_ns)
    _time.localtime = _Fn(lambda secs=None: _r_localtime(_now() if secs is None else secs), _r_localtime)
    _time.gmtime = _Fn(lambda secs=None: _r_gmtime(_now() if secs is None else secs), _r_gmtime)
    _time.ctime = _Fn(lambda secs=None: _r_ctime(_now() if secs is None else secs), _r_ctime)
    _time.asctime = _Fn(lambda t=None: _r_asctime(_r_localtime(_now()) if t is None else t), _r_asctime)
    _time.strftime = _Fn(lambda fmt, t=None: _r_strftime(fmt, _r_localtime(_now()) if t is None else t), _r_strftime)

    _real_date, _real_datetime = _dt.date, _dt.datetime

    class _Meta(type):
        def __instancecheck__(cls, obj):
            return isinstance(obj, cls._real)

        def __subclasscheck__(cls, sub):
            return issubclass(sub, cls._real)

    class date(_real_date, metaclass=_Meta):
        _real = _real_date

        @classmethod
        def today(cls):
            return cls.fromtimestamp(_now())

    class datetime(_real_datetime, metaclass=_Meta):
        _real = _real_datetime

        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(_now(), tz)

        @classmethod
        def utcnow(cls):
            return cls.utcfromtimestamp(_now())

        @classmethod
        def today(cls):
            return cls.fromtimestamp(_now())

    for _c in (date, datetime):
        _c.__module__ = "datetime"
    _dt.date, _dt.datetime = date, datetime

    # file times: shift what is read by the offset, and what is written back by minus the offset
    _r_stat, _r_lstat, _r_fstat, _r_utime, _r_scandir = _os.stat, _os.lstat, _os.fstat, _os.utime, _os.scandir
    _EXTRA = ("st_blksize", "st_blocks", "st_rdev", "st_flags", "st_gen", "st_birthtime", "st_fstype", "st_rsize",
              "st_creator", "st_type", "st_file_attributes", "st_reparse_tag")

    def _shift(st):
        seq = list(st[:10])
        seq[7], seq[8], seq[9] = seq[7] + _off_s, seq[8] + _off_s, seq[9] + _off_s
        extra = {"st_atime": st.st_atime + _off, "st_mtime": st.st_mtime + _off, "st_ctime": st.st_ctime + _off,
                 "st_atime_ns": st.st_atime_ns + _off_ns, "st_mtime_ns": st.st_mtime_ns + _off_ns,
                 "st_ctime_ns": st.st_ctime_ns + _off_ns}
        for k in _EXTRA:
            if hasattr(st, k):
                extra[k] = getattr(st, k)
        if "st_birthtime" in extra:
            extra["st_birthtime"] = extra["st_birthtime"] + _off
        return _os.stat_result(seq, extra)

    def _utime(path, times=None, *, ns=None, **kw):
        if times is not None:
            times = (times[0] - _off, times[1] - _off)
        if ns is not None:
            ns = (ns[0] - _off_ns, ns[1] - _off_ns)
        if ns is None:
            return _r_utime(path, times, **kw)
        return _r_utime(path, times, ns=ns, **kw)

    class _Entry:
        __slots__ = ("_e",)

        def __init__(self, e):
            self._e = e

        def __getattr__(self, n):
            return getattr(self._e, n)

        def stat(self, *, follow_symlinks=True):
            return _shift(self._e.stat(follow_symlinks=follow_symlinks))

        def __fspath__(self):
            return self._e.__fspath__()

        def __repr__(self):
            return repr(self._e)

    class _Scan:
        def __init__(self, it):
            self._it = it

        def __iter__(self):
            return self

        def __next__(self):
            return _Entry(next(self._it))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._it.close()

        def close(self):
            self._it.close()

    _new = {"stat": _Fn(lambda *a, **k: _shift(_r_stat(*a, **k)), _r_stat),
            "lstat": _Fn(lambda *a, **k: _shift(_r_lstat(*a, **k)), _r_lstat),
            "fstat": _Fn(lambda *a, **k: _shift(_r_fstat(*a, **k)), _r_fstat),
            "utime": _Fn(_utime, _r_utime),
            "scandir": _Fn(lambda *a, **k: _Scan(_r_scandir(*a, **k)), _r_scandir)}
    for _name, _f in _new.items():
        _real = getattr(_os, _name)
        for _s in (_os.supports_dir_fd, _os.supports_fd, _os.supports_follow_symlinks, _os.supports_effective_ids):
            if _real in _s:
                _s.add(_f)
        setattr(_os, _name, _f)
