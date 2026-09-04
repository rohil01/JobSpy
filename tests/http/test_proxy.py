from jobsniffer.http.proxy import ProxyRotator


def test_no_proxies_returns_none_every_time():
    rotator = ProxyRotator(None)
    assert rotator.next() is None
    assert rotator.next() is None


def test_single_string_proxy_is_repeated():
    rotator = ProxyRotator("1.2.3.4:8080")
    assert rotator.next() == {"http": "http://1.2.3.4:8080", "https": "http://1.2.3.4:8080"}
    assert rotator.next() == {"http": "http://1.2.3.4:8080", "https": "http://1.2.3.4:8080"}


def test_list_of_proxies_cycles_in_order():
    rotator = ProxyRotator(["1.1.1.1:80", "2.2.2.2:80"])
    first = rotator.next()
    second = rotator.next()
    third = rotator.next()
    assert first == {"http": "http://1.1.1.1:80", "https": "http://1.1.1.1:80"}
    assert second == {"http": "http://2.2.2.2:80", "https": "http://2.2.2.2:80"}
    assert third == first


def test_localhost_entry_means_no_proxy_this_turn():
    rotator = ProxyRotator(["1.1.1.1:80", "localhost"])
    assert rotator.next() is not None
    assert rotator.next() is None


def test_scheme_prefixed_proxy_is_preserved_verbatim():
    rotator = ProxyRotator("socks5://user:pass@host:1080")
    proxy = rotator.next()
    assert proxy == {
        "http": "socks5://user:pass@host:1080",
        "https": "socks5://user:pass@host:1080",
    }


def test_empty_list_behaves_like_none():
    rotator = ProxyRotator([])
    assert rotator.next() is None
