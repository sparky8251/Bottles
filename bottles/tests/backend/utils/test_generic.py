import shlex
from typing import Optional

import pytest

from bottles.backend.utils import generic
from bottles.backend.utils.generic import detect_encoding, is_ntsync_available


# CP932 is superset of Shift-JIS, which is default codec for Japanese in Windows
# GBK is default codec for Chinese in Windows
@pytest.mark.parametrize(
    "text, hint, codec",
    [
        ("Hello, world!", None, "ascii"),
        ("   ", None, "ascii"),
        pytest.param(
            "Привет, мир!",
            None,
            "windows-1251",
            marks=pytest.mark.xfail(
                strict=True,
                reason="chardet scores this 0.12, under the 0.5 floor; see the TODO in detect_encoding",
            ),
        ),
        ("こんにちは、世界！", "ja_JP", "cp932"),
        ("こんにちは、世界！", "ja_JP.utf-8", "utf-8"),
        ("你好，世界！", "zh_CN", "gbk"),
        ("你好，世界！", "zh_CN.UTF-8", "utf-8"),
        ("你好，世界！", "zh_CN.invalid_fallback", "gbk"),
        ("", None, "utf-8"),
    ],
)
def test_detect_encoding(text: str, hint: Optional[str], codec: Optional[str]):
    text_bytes = text.encode(codec)
    guess = detect_encoding(text_bytes, hint)
    assert guess.lower() == codec.lower()


# Bytes captured from Soda 11.0-10 inside the flatpak sandbox with the bottle
# locale set as Bottles sets it. Each producer uses a different code page, and
# the detector cannot recover any of them; these document the bug recorded in
# the TODO in detect_encoding and must be updated when it is fixed.
@pytest.mark.parametrize(
    "producer, locale, raw, codec",
    [
        ("reg query", "ru_RU", b"    ru    REG_SZ    \x8f\xe0\xa8\xa2\xa5\xe2, \xac\xa8\xe0!\r\n", "cp866"),
        ("reg query", "ja_JP", b"    ja    REG_SZ    \x82\xb1\x82\xf1\x82\xc9\x82\xbf\x82\xcd\x81A\x90\xa2\x8aE\x81I\r\n", "cp932"),
        ("cmd echo", "de_DE", b"Gr\x81\xe1e\r\n", "cp850"),
        ("winedbg info proc", "ru_RU", b" 000000f0 1        \\_ '\xc8\xe3\xf0\xe0.exe'", "cp1251"),
    ],
)
@pytest.mark.xfail(strict=True, reason="legacy code pages score under chardet's floor; see the TODO in detect_encoding")
def test_detect_encoding_recovers_wine_output(producer: str, locale: str, raw: bytes, codec: str):
    assert raw.decode(codec)  # the fixture itself is valid in its real encoding
    guess = detect_encoding(raw)
    assert guess is not None and guess.lower().replace("-", "") in codec.replace("-", "")


def test_ntsync_requires_kernel_device(monkeypatch, tmp_path):
    runner = tmp_path / "runner" / "bin"
    runner.mkdir(parents=True)
    (runner / "wineserver").write_bytes(b"compiled with /dev/ntsync support")
    monkeypatch.setattr(generic, "NTSYNC_DEVICE", str(tmp_path / "missing"))

    assert not is_ntsync_available(str(runner / "wine"))


def test_ntsync_rejects_runner_without_compiled_support(monkeypatch, tmp_path):
    device = tmp_path / "ntsync"
    device.touch()
    runner = tmp_path / "wine-11.0" / "bin"
    runner.mkdir(parents=True)
    (runner / "wineserver").write_bytes(b"regular wineserver")
    monkeypatch.setattr(generic, "NTSYNC_DEVICE", str(device))

    assert not is_ntsync_available(str(runner / "wine"))


def test_ntsync_accepts_backported_runner_with_spaces(monkeypatch, tmp_path):
    device = tmp_path / "ntsync"
    device.touch()
    runner = tmp_path / "custom runner 9" / "bin"
    runner.mkdir(parents=True)
    (runner / "wineserver").write_bytes(b"prefix /dev/ntsync suffix")
    monkeypatch.setattr(generic, "NTSYNC_DEVICE", str(device))

    assert is_ntsync_available(shlex.quote(str(runner / "wine")))
