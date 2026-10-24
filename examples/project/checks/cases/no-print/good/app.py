import logging

log = logging.getLogger(__name__)


def total(prices):
    log.debug("prices: %s", prices)
    return sum(prices)
