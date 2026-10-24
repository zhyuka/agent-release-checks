import logging

log = logging.getLogger(__name__)


def total(prices):
    print("prices:", prices)  # the planted defect: a debug print left in
    return sum(prices)
