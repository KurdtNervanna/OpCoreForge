"""
Regression test: a wrong system clock must be diagnosed, not reported as a
network fault.

Reproduces a real report from an HP EliteBook 8570w. Every download failed
with::

    [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
    certificate is not yet valid

and the user was shown a raw traceback ending in "Failed to fetch release
information from GitHub" -- which points at the network, when in fact the
machine's clock was years behind (a flat CMOS battery on a 2012 laptop) and no
HTTPS connection anywhere could have succeeded.

The checks below assert the two things that were missing: that the real cause
survives upstream's exception swallowing, and that what reaches the user says
"your clock is wrong" rather than "check your connection".

No network and no display needed.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from opcoreforge import netdiag                                   # noqa: E402
from opcoreforge.bridge.ocs import PayloadUnavailable             # noqa: E402

STDERR = sys.stderr
RESULTS = []

# Verbatim from the user's log.
REAL_ERROR = ("<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate "
              "verify failed: certificate is not yet valid (_ssl.c:1081)>")
CONSOLE_LINE = "Connection error: " + REAL_ERROR


def check(label, got, expect):
    ok = expect(got) if callable(expect) else got == expect
    RESULTS.append(("PASS" if ok else "FAIL", label, got))


class FakeFetcher:
    """Stands in for upstream's ResourceFetcher: swallows and returns None."""

    calls = 0

    def _make_request(self, resource_url, timeout=10):
        FakeFetcher.calls += 1
        print(CONSOLE_LINE)
        return None


class FakeModule:
    ResourceFetcher = FakeFetcher


def no_network():
    """Pin the "what time is it really" lookup so no test touches the network."""
    netdiag._real_time = None


def with_real_time(moment):
    netdiag._real_time = moment


def main():
    # -- classification ---------------------------------------------------
    check("the reported error is read as a clock problem",
          netdiag.classify(REAL_ERROR), netdiag.CLOCK_BEHIND)
    check("an expired certificate is a different case",
          netdiag.classify("certificate has expired"), netdiag.CLOCK_AHEAD)
    check("an ordinary TLS failure is not blamed on the clock",
          netdiag.classify("certificate verify failed: unable to get local "
                           "issuer certificate"), netdiag.TLS)
    check("a DNS failure is recognised",
          netdiag.classify("<urlopen error [Errno -2] Name or service not "
                           "known>"), netdiag.DNS)
    check("a timeout is recognised",
          netdiag.classify("The read operation timed out"), netdiag.TIMEOUT)

    # -- the explanation --------------------------------------------------
    no_network()
    netdiag.clear()
    netdiag.record("https://github.com/x/y/releases", REAL_ERROR)
    text = netdiag.explain()
    check("names the clock as the cause", "clock is wrong" in text, True)
    check("shows what the PC thinks the date is",
          "This PC says:" in text, True)
    check("says how to fix it", "Adjust date and time" in text, True)
    check("mentions the CMOS battery", "CMOS battery" in text, True)
    check("does not send the user to check the connection",
          "check the connection" in text.lower(), False)
    check("explains why HTTPS specifically fails",
          "certificate" in text.lower(), True)

    # With a reachable clock source the message quantifies the error.
    with_real_time(datetime.datetime(2026, 9, 8, 12, 0,
                                     tzinfo=datetime.timezone.utc))
    text = netdiag.explain()
    check("reports the true time when it can be learned",
          "Actually:" in text, True)
    check("says how far out the clock is", "Out by:" in text, True)
    no_network()

    # -- precedence -------------------------------------------------------
    netdiag.clear()
    netdiag.record("https://a", REAL_ERROR)
    netdiag.record("https://b", "<urlopen error timed out>")
    check("a clock fault is not masked by later vaguer failures",
          netdiag.last_failure()[0], netdiag.CLOCK_BEHIND)

    # -- the other kinds still say something useful ----------------------
    netdiag.clear()
    netdiag.record("https://a", "<urlopen error [Errno -2] Name or service "
                                "not known>")
    check("a DNS failure gets its own advice",
          "DNS" in netdiag.explain(), True)
    netdiag.clear()
    check("an unclassifiable failure invents nothing",
          netdiag.explain(RuntimeError("something else")), "")

    # -- surviving upstream's swallowed exception ------------------------
    netdiag.set_console_reader(lambda: CONSOLE_LINE + "\nFailed to fetch "
                                       "content from https://github.com/x. "
                                       "Retrying...")
    netdiag.install(FakeModule)
    netdiag.clear()
    fetcher = FakeModule.ResourceFetcher()
    check("the wrapper leaves upstream's return value alone",
          fetcher._make_request("https://github.com/x"), None)
    check("upstream's own method still ran", FakeFetcher.calls, 1)
    check("the swallowed cause is recovered from the console",
          netdiag.last_failure()[0], netdiag.CLOCK_BEHIND)

    netdiag.install(FakeModule)
    check("installing twice does not double-wrap",
          getattr(FakeModule.ResourceFetcher, "_ocf_diag", False), True)

    # -- what the user is shown ------------------------------------------
    failure = PayloadUnavailable(
        "Hardware-Sniffer",
        "Could not download Hardware-Sniffer at this time "
        "(Failed to fetch release information from GitHub.)",
        hint=netdiag.explain())
    shown = str(failure)
    check("the dialog text leads with what failed",
          shown.startswith("Could not download Hardware-Sniffer"), True)
    check("the dialog text carries the diagnosis",
          "clock is wrong" in shown, True)
    check("the hint is reachable for the dialog to render",
          bool(getattr(failure, "hint", "")), True)
    check("a failure with no diagnosis reads as before",
          str(PayloadUnavailable("Lilu", "Could not download Lilu")),
          "Could not download Lilu")

    # -- the pre-flight check ---------------------------------------------
    future = (datetime.date.today() +
              datetime.timedelta(days=400)).isoformat()
    warning = netdiag.preflight(future)
    check("a clock set before this build existed is caught at startup",
          bool(warning), True)
    check("the startup warning says what to do",
          "Adjust date and time" in warning, True)
    past = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    check("a correct clock is not nagged", netdiag.preflight(past), "")
    check("today's build does not warn on today's clock",
          netdiag.preflight(datetime.date.today().isoformat()), "")
    check("a malformed release date is ignored rather than fatal",
          netdiag.preflight("not-a-date"), "")

    # -- the shipped release date is real ---------------------------------
    from opcoreforge.version import __released__
    check("version.py carries a usable release date",
          bool(datetime.date.fromisoformat(__released__)), True)
    check("the release date is not in the future",
          datetime.date.fromisoformat(__released__) <= datetime.date.today(),
          True)

    STDERR.write("\n")
    for status, label, value in RESULTS:
        text = str(value).replace("\n", " ")
        if len(text) > 40:
            text = text[:37] + "..."
        STDERR.write("%s  %-56s %s\n" % (status, label, text))
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    STDERR.write("\n%d checks, %d failed\n" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
