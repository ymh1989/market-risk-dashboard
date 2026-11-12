from __future__ import annotations

from contextlib import redirect_stdout
from importlib import import_module
from io import StringIO
from types import ModuleType


class KrxAuthenticationError(RuntimeError):
    """재시도 대신 계정 설정 확인이 필요한 KRX 인증 오류."""

    def __init__(self, code: str = "login_failed") -> None:
        self.code = code
        if code == "password_change_required":
            message = (
                "KRX 인증 실패: 비밀번호 변경 안내 처리 필요(CD010). KRX 홈페이지에서 "
                "계정 안내를 처리하고 재실행하세요. 비밀번호를 실제 변경한 경우에만 "
                "로컬 .env의 KRX_PW도 갱신하세요."
            )
        else:
            message = (
                "KRX 인증 실패: 로그인 세션을 만들지 못했습니다. KRX 홈페이지 로그인과 "
                "로컬 .env의 KRX_ID·KRX_PW를 확인하세요."
            )
        super().__init__(message)


def _check_login_output(output: str) -> None:
    """pykrx가 예외 대신 출력하는 인증 거절을 식별하며 계정 정보는 노출하지 않는다."""
    if any(value in output for value in ("CD010", "비밀번호 변경", "패스워드 변경")):
        raise KrxAuthenticationError("password_change_required")
    if "KRX 로그인 실패" in output or "KRX 세션 갱신 실패" in output:
        raise KrxAuthenticationError()


def load_authenticated_stock() -> ModuleType:
    """pykrx 최초 로그인과 세션 갱신을 검증해 빈 응답의 연쇄 재시도를 막는다."""
    output = StringIO()
    try:
        # pykrx는 최초 import 때 로그인하므로 출력 판독을 그 이전부터 시작한다.
        with redirect_stdout(output):
            stock = import_module("pykrx.stock")
        _check_login_output(output.getvalue())
        with redirect_stdout(output):
            auth = import_module("pykrx.website.comm.auth")
            session = auth.get_auth_session()
        _check_login_output(output.getvalue())
        if session is None or not session.is_valid():
            raise KrxAuthenticationError()
        return stock
    except KrxAuthenticationError:
        raise
    except ImportError:
        raise RuntimeError(
            "KRX 인증 인터페이스를 불러올 수 없습니다. Breadth 환경의 pykrx 설치를 확인하세요."
        ) from None
    except Exception:
        _check_login_output(output.getvalue())
        raise RuntimeError(
            "KRX 로그인 또는 세션 확인 중 통신 오류가 발생했습니다. 네트워크와 원천 상태를 확인하세요."
        ) from None
