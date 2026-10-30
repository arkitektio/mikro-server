"""`$fits`: a compiled formula from the client, held to a whitelist before it runs on the rows."""

import pytest

from core.logic.descriptor_filter import _check_fits_path

DECLARED = {"@mikro/size", "@mikro/dsize"}


@pytest.mark.parametrize(
    "path",
    [
        '($."@mikro/size" * $."@mikro/dsize") < ($agent.memory_mib * 1048576)',
        '($."@mikro/size" / 3).floor() <= 333.0',
        '($."@mikro/size" < 5) || ($agent.gpu_count > 0)',
        '(-$."@mikro/size").abs() >= 1e+20',
    ],
)
def test_what_the_compiler_writes_passes(path: str) -> None:
    _check_fits_path(path, DECLARED)


@pytest.mark.parametrize(
    "path",
    [
        '$."@mikro/other" < 5',
        '$.* < 5',
        '$.**.a < 5',
        '$."@mikro/size" like_regex "a"',
        '$."@mikro/size".datetime() < 5',
        '$.keyvalue() < 5',
        'exists($."@mikro/size")',
        '$secret.a < 5',
        '$agent.hostname < 5',
        '$."@mikro/size" == "x"',
    ],
)
def test_everything_else_is_refused(path: str) -> None:
    with pytest.raises(ValueError):
        _check_fits_path(path, DECLARED)
