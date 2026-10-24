import logging

log = logging.getLogger(__name__)


def total(prices):
    log.debug("prices: %s", prices)
    return sum(prices)


def mean(prices):
    return total(prices) / len(prices) if prices else 0.0
