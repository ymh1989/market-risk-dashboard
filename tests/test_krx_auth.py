from types import SimpleNamespace

import pytest

from kospi_risk import krx_auth


@pytest.mark.parametrize("message,code", [
    ("KRX 비밀번호 변경이 필요합니다.", "password_change_required"),
    ("패스워드 변경 필요", "password_change_required"),
    ("CD010", "password_change_required"),
    ("KRX 로그인 실패: 자격 증명을 확인하세요.", "login_failed"),
])
def test_initial_login_failure_stops_before_another_login(monkeypatch, capsys, message, code):
    calls = []

    def importer(name):
        calls.append(name)
        print("로그인 ID: private-user")
        print(message)
        return SimpleNamespace()

    monkeypatch.setattr(krx_auth, "import_module", importer)
    with pytest.raises(krx_auth.KrxAuthenticationError) as caught:
        krx_auth.load_authenticated_stock()
    assert caught.value.code == code
    assert calls == ["pykrx.stock"]
    assert "private-user" not in str(caught.value)
    assert capsys.readouterr().out == ""


def test_authenticated_session_is_reused(monkeypatch, capsys):
    stock = SimpleNamespace()
    session = SimpleNamespace(is_valid=lambda: True)
    checks = []

    def get_session():
        checks.append(1)
        return session

    def importer(name):
        return stock if name == "pykrx.stock" else SimpleNamespace(get_auth_session=get_session)

    monkeypatch.setattr(krx_auth, "import_module", importer)
    assert krx_auth.load_authenticated_stock() is stock
    assert checks == [1]
    assert capsys.readouterr().out == ""


def test_session_refresh_password_failure_is_classified(monkeypatch):
    def get_session():
        print("KRX 비밀번호 변경이 필요합니다.")
        return None

    monkeypatch.setattr(krx_auth, "import_module", lambda _: SimpleNamespace(get_auth_session=get_session))
    with pytest.raises(krx_auth.KrxAuthenticationError, match="CD010"):
        krx_auth.load_authenticated_stock()


@pytest.mark.parametrize("session", [None, SimpleNamespace(is_valid=lambda: False)])
def test_silent_missing_or_invalid_session_is_rejected(monkeypatch, session):
    monkeypatch.setattr(
        krx_auth, "import_module", lambda _: SimpleNamespace(get_auth_session=lambda: session)
    )
    with pytest.raises(krx_auth.KrxAuthenticationError, match="로그인 세션"):
        krx_auth.load_authenticated_stock()


def test_network_failure_is_not_mislabeled_and_details_are_hidden(monkeypatch):
    def importer(_):
        raise RuntimeError("private-password")

    monkeypatch.setattr(krx_auth, "import_module", importer)
    with pytest.raises(RuntimeError, match="통신 오류") as caught:
        krx_auth.load_authenticated_stock()
    assert not isinstance(caught.value, krx_auth.KrxAuthenticationError)
    assert "private-password" not in str(caught.value)
    assert caught.value.__suppress_context__
