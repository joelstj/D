"""Unit tests for the typed exception hierarchy.

The hierarchy is load-bearing: the whole retry policy keys off ``isinstance``
checks against :class:`DataError` (raise) vs :class:`InfraError` (retry). These
tests pin the tree shape so a refactor cannot silently reparent an exception and
turn a data bug into a retried infra blip.
"""

from __future__ import annotations

import pytest

from l2arb.errors import (
    ConfigError,
    DataError,
    InfraError,
    IngestError,
    L2ArbError,
    PoolStateError,
    RateLimitError,
    RpcError,
    StaleDataError,
    SubscriptionError,
    VerificationError,
)

pytestmark = pytest.mark.unit

DATA_ERRORS = [PoolStateError, StaleDataError, VerificationError, IngestError]
INFRA_ERRORS = [RpcError, RateLimitError, SubscriptionError]


@pytest.mark.parametrize("exc", [*DATA_ERRORS, *INFRA_ERRORS, DataError, InfraError, ConfigError])
def test_everything_descends_from_base(exc: type[Exception]) -> None:
    assert issubclass(exc, L2ArbError)


@pytest.mark.parametrize("exc", DATA_ERRORS)
def test_data_errors_are_data_not_infra(exc: type[Exception]) -> None:
    assert issubclass(exc, DataError)
    assert not issubclass(exc, InfraError)


@pytest.mark.parametrize("exc", INFRA_ERRORS)
def test_infra_errors_are_infra_not_data(exc: type[Exception]) -> None:
    assert issubclass(exc, InfraError)
    assert not issubclass(exc, DataError)


def test_data_and_infra_are_disjoint() -> None:
    # The retry policy depends on these two being mutually exclusive.
    assert not issubclass(DataError, InfraError)
    assert not issubclass(InfraError, DataError)


def test_raising_and_catching_by_category() -> None:
    with pytest.raises(DataError):
        raise PoolStateError("negative reserves")
    with pytest.raises(InfraError):
        raise RpcError("502 from endpoint")


def test_message_is_preserved() -> None:
    err = StaleDataError("block 123 older than 5s bound")
    assert "block 123" in str(err)
