"""
Turns network failures into something the person can act on.

Every download OpCoreForge makes goes through OpenCore-Simplify's
``ResourceFetcher``, which catches its own exceptions, prints a line and
returns ``None``.  Three layers up that becomes ``ValueError: Failed to fetch
release information from GitHub`` and the actual cause -- printed once, long
scrolled away -- is gone.  On a machine reported from the field the real cause
was::

    [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
    certificate is not yet valid

which is not a network fault at all.  A certificate cannot be "not yet valid"
unless the clock reading it is behind the certificate's start date, so the PC's
own clock was wrong -- the classic symptom of a flat CMOS battery in an older
laptop.  No amount of retrying fixes that, and nothing in the message says so.

So failures are recorded as they happen and translated here.  The translation
is deliberately specific: the useful output is "your clock says 2013, set it
correctly", not "check your internet connection".
"""

from __future__ import annotations

import datetime
import re
import threading

# Signatures worth naming. Ordered: the first match wins, so the precise
# certificate cases are tested before the generic SSL one.
CLOCK_BEHIND = "clock-behind"
CLOCK_AHEAD = "clock-ahead"
TLS = "tls"
DNS = "dns"
TIMEOUT = "timeout"
OFFLINE = "offline"
UNKNOWN = "unknown"

_SIGNATURES = [
    (CLOCK_BEHIND, ("certificate is not yet valid",)),
    (CLOCK_AHEAD, ("certificate has expired",)),
    (TLS, ("certificate verify failed", "ssl:", "sslerror",
           "self signed certificate")),
    (DNS, ("name or service not known", "getaddrinfo failed",
           "nodename nor servname", "temporary failure in name resolution",
           "no address associated")),
    (TIMEOUT, ("timed out", "timeout")),
    (OFFLINE, ("network is unreachable", "connection refused",
               "connection reset", "unreachable host", "urlopen error")),
]

_lock = threading.Lock()
_last = None          # (kind, text, url)
_real_time = "unset"  # cached: None once a lookup has failed


def classify(text: str) -> str:
    lowered = str(text).lower()
    for kind, needles in _SIGNATURES:
        for needle in needles:
            if needle in lowered:
                return kind
    return UNKNOWN


def record(url, exc) -> None:
    """Note a failed request. Safe to call from any thread."""
    kind = classify(exc)
    global _last
    with _lock:
        # A clock problem outranks whatever was recorded before it: every
        # later request fails too, usually with a vaguer message, and the
        # last one would otherwise win.
        if _last and _last[0] in (CLOCK_BEHIND, CLOCK_AHEAD) and \
                kind not in (CLOCK_BEHIND, CLOCK_AHEAD):
            return
        _last = (kind, str(exc), url)


def clear() -> None:
    global _last
    with _lock:
        _last = None


def last_failure():
    with _lock:
        return _last


def real_time_utc(timeout=4.0):
    """The current time according to a web server, over plain HTTP.

    Deliberately not HTTPS: this is called precisely when TLS is failing, and
    the whole point is to learn the true time in spite of that. Every HTTP
    response carries a Date header, including the redirect these hosts answer
    with, so no page is actually fetched.
    """
    global _real_time
    if _real_time != "unset":
        return _real_time

    import http.client

    value = None
    for host in ("github.com", "www.google.com", "example.com"):
        conn = None
        try:
            conn = http.client.HTTPConnection(host, timeout=timeout)
            conn.request("HEAD", "/", headers={"User-Agent": "OpCoreForge"})
            header = conn.getresponse().getheader("Date")
            if header:
                # RFC 7231 IMF-fixdate, always GMT.
                value = datetime.datetime.strptime(
                    header, "%a, %d %b %Y %H:%M:%S %Z").replace(
                        tzinfo=datetime.timezone.utc)
                break
        except Exception:
            continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    _real_time = value
    return value


def _format(moment) -> str:
    return moment.strftime("%A %d %B %Y, %H:%M") if moment else "unknown"


def _skew_phrase(delta: datetime.timedelta) -> str:
    seconds = abs(delta.total_seconds())
    direction = "behind" if delta.total_seconds() > 0 else "ahead"
    days = seconds / 86400.0
    if days >= 365:
        amount = "%.1f years" % (days / 365.25)
    elif days >= 1:
        amount = "%d days" % round(days)
    elif seconds >= 3600:
        amount = "%d hours" % round(seconds / 3600.0)
    else:
        amount = "%d minutes" % round(seconds / 60.0)
    return "%s %s" % (amount, direction)


def clock_message(kind=CLOCK_BEHIND, check_network=True) -> str:
    """The full explanation for a wrong system clock."""
    now = datetime.datetime.now(datetime.timezone.utc)
    actual = real_time_utc() if check_network else None

    lines = ["This PC's clock is wrong, which is why the download failed."
             if kind else "This PC's clock looks wrong."]
    lines.append("")
    lines.append("    This PC says:   %s UTC" % _format(now))
    if actual:
        lines.append("    Actually:       %s UTC" % _format(actual))
        lines.append("    Out by:         %s" % _skew_phrase(actual - now))
    lines.append("")
    lines.append(
        "Secure downloads check a certificate's start and end dates against "
        "the clock, so while the date is wrong every HTTPS download fails - "
        "no matter how good the connection is.")
    lines.append("")
    lines.append("To fix it:")
    lines.append("  1. Right-click the clock in the taskbar, choose "
                 "\"Adjust date and time\".")
    lines.append("  2. Turn on \"Set time zone automatically\" and \"Set time "
                 "automatically\", then press \"Sync now\".")
    lines.append("  3. Run OpCoreForge again.")
    lines.append("")
    lines.append(
        "If the date resets every time the machine is unplugged, the "
        "motherboard's CMOS battery is flat. It is a CR2032 coin cell and "
        "costs very little; on a laptop of this age it is a common failure "
        "and worth replacing, or the clock will drift back after every "
        "shutdown.")
    return "\n".join(lines)


def explain(exc=None) -> str:
    """A readable account of why a download failed, or "" if there is none.

    ``exc`` is the exception the caller has in hand; the recorded low-level
    failure is preferred when there is one, because it is the one that knows
    what actually went wrong.
    """
    failure = last_failure()
    kind = failure[0] if failure else classify(exc or "")

    if kind == CLOCK_BEHIND:
        return clock_message(CLOCK_BEHIND)
    if kind == CLOCK_AHEAD:
        # An expired certificate is genuinely ambiguous: either the clock is
        # ahead, or the site really has let its certificate lapse. Ask the
        # network which, and only blame the clock when it says so.
        actual = real_time_utc()
        now = datetime.datetime.now(datetime.timezone.utc)
        if actual and abs((actual - now).total_seconds()) > 86400:
            return clock_message(CLOCK_AHEAD)
        return ("A site's security certificate has expired. If this PC's date "
                "is correct, the problem is at the far end and will clear on "
                "its own; check the date first, since a clock set into the "
                "future produces exactly this message.")
    if kind == TLS:
        return ("The secure connection could not be verified. This is usually "
                "antivirus or a corporate proxy inspecting HTTPS traffic, or a "
                "wi-fi captive portal that has not been signed into yet.")
    if kind == DNS:
        return ("This PC could not look up the address. It is offline, or its "
                "DNS settings are wrong - try opening any website in a "
                "browser first.")
    if kind == TIMEOUT:
        return ("The server did not answer in time. That is usually a slow or "
                "dropping connection; trying again often works.")
    if kind == OFFLINE:
        return ("This PC could not reach the internet. Check the connection, "
                "and any firewall or VPN that might be blocking it.")
    return ""


def preflight(released: str) -> str:
    """Warn about a clock that is wrong before anything has been downloaded.

    A build cannot have been released after the moment it is running, so a
    system date earlier than this build's release date is proof the clock is
    wrong. It costs nothing to check and catches the problem at startup rather
    than several minutes into a failing download.
    """
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(released or ""))
    if not match:
        return ""
    release_date = datetime.datetime(
        int(match.group(1)), int(match.group(2)), int(match.group(3)),
        tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    # A day of slack absorbs time zones; nothing subtler is worth reporting.
    if now >= release_date - datetime.timedelta(days=1):
        return ""
    return ("This PC's clock says %s, which is before this build of "
            "OpCoreForge existed. Downloads will fail until the date is "
            "corrected - open \"Adjust date and time\" and switch on \"Set "
            "time automatically\"." % now.strftime("%d %B %Y"))


def install(fetcher_module) -> None:
    """Record every failed request made through upstream's fetcher.

    Upstream swallows the exception inside ``_make_request``; rather than
    change vendored code, the method is wrapped so the exception is seen on
    the way past. The return value is untouched, so upstream's retry and
    fallback behaviour is exactly as it was.
    """
    if getattr(fetcher_module.ResourceFetcher, "_ocf_diag", False):
        return
    original = fetcher_module.ResourceFetcher._make_request

    def _make_request(self, resource_url, timeout=10):
        try:
            response = original(self, resource_url, timeout=timeout)
        except Exception as exc:          # upstream normally catches its own
            record(resource_url, exc)
            raise
        if response is None:
            # Upstream already caught and printed it. The printed line is the
            # only surviving evidence, so it is read back off the console.
            record(resource_url, _recent_console_error() or
                   "request to %s failed" % resource_url)
        return response

    fetcher_module.ResourceFetcher._make_request = _make_request
    fetcher_module.ResourceFetcher._ocf_diag = True


_console_reader = None


def set_console_reader(reader) -> None:
    """Tell netdiag how to read back what upstream just printed."""
    global _console_reader
    _console_reader = reader


def _recent_console_error() -> str:
    if not _console_reader:
        return ""
    try:
        text = _console_reader() or ""
    except Exception:
        return ""
    for line in reversed(text.splitlines()[-40:]):
        if line.startswith(("Connection error:", "SSL error:", "Timeout error:",
                            "Request failed:")):
            return line
    return ""
