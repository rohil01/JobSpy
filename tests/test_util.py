from jobsniffer.http.curl_client import CurlCffiClient
from jobsniffer.util import create_session


def test_create_session_returns_a_curl_cffi_client():
    session = create_session()
    assert isinstance(session, CurlCffiClient)


def test_default_retry_policy_is_conservative():
    session = create_session()
    assert session._max_attempts == 3
    assert session._wait_initial == 0.5


def test_has_retry_widens_attempts_and_forwards_delay_as_wait_initial():
    """has_retry/delay used to be silently discarded when create_session
    switched to CurlCffiClient -- this verifies the intent (a slower,
    more patient retry policy for sites that ask for it) is actually
    honored, not just accepted and dropped."""
    session = create_session(has_retry=True, delay=5)
    assert session._max_attempts == 5
    assert session._wait_initial == 5.0


def test_is_tls_and_clear_cookies_are_accepted_as_no_ops():
    # Must not raise, and must not affect the resulting client's behavior.
    session = create_session(is_tls=False, clear_cookies=True)
    assert isinstance(session, CurlCffiClient)
